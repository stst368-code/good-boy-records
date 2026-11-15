"""Read progress for one owned ComfyUI prompt, with HTTP polling as a fallback."""
from datetime import datetime, timezone
import json
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

try:
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect
except ImportError:
    connect = None  # Standalone controller installations can still use HTTP.


NODE_LABELS = {
    'CLIPLoader': 'Loading text encoder',
    'MiniMaxMusic3TextEncode': 'Text conditioning',
    'UNETLoader': 'Loading diffusion model',
    'EmptyMiniMaxMusic3LatentAudio': 'Preparing audio',
    'KSampler': 'Sampling',
    'VAELoader': 'Loading audio decoder',
    'VAEDecodeAudio': 'Decoding audio',
    'SaveAudio': 'Saving FLAC',
}


def comfy_timestamp(value):
    if type(value) not in (int, float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec='milliseconds')
    except (ValueError, OverflowError, OSError):
        return None


class PromptProgress:
    def __init__(self, address, prefix, client_id, graph, user_agent):
        url = urlsplit(address)
        scheme = 'wss' if url.scheme == 'https' else 'ws'
        query = urlencode({'clientId': client_id})
        routes = dict.fromkeys(('/ws', prefix + '/ws'))
        self.urls = [urlunsplit((scheme, url.netloc, path, query, '')) for path in routes]
        self.user_agent = user_agent
        self.graph = graph
        self.socket = None
        self.retry_at = 0
        self.node = None
        self.step = self.steps = None
        self.started_at = None
        self.finished = False

    def open(self):
        if connect is None or self.socket is not None or time.monotonic() < self.retry_at:
            return
        for url in self.urls:
            try:
                self.socket = connect(url, open_timeout=1, close_timeout=.2, compression=None,
                                      max_size=4 * 1024 * 1024, user_agent_header=self.user_agent)
                return
            except (OSError, TimeoutError, WebSocketException):
                continue
        self.retry_at = time.monotonic() + 15

    def close(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except (OSError, WebSocketException):
                pass
            self.socket = None

    def accept(self, message, prompt_id):
        if not isinstance(message, dict):
            return False
        data = message.get('data')
        if not isinstance(data, dict) or data.get('prompt_id') != prompt_id:
            return False
        kind = message.get('type')
        if not isinstance(kind, str):
            return False
        if kind == 'execution_start':
            self.started_at = comfy_timestamp(data.get('timestamp')) or self.started_at
            return True
        if kind in {'execution_success', 'execution_error', 'execution_interrupted'} or (
            kind == 'executing' and 'node' in data and data['node'] is None
        ):
            self.finished = True
            self.node = self.step = self.steps = None
            return True
        if kind not in {'executing', 'progress'} or self.finished:
            return False
        node = str(data.get('node'))
        if node not in self.graph:
            return False
        if kind == 'executing':
            self.node = node
            self.step = self.steps = None
            return True
        value, maximum = data.get('value'), data.get('max')
        if type(value) is not int or type(maximum) is not int or not (0 <= value <= maximum and maximum > 0):
            return False
        self.node, self.step, self.steps = node, value, maximum
        return True

    def snapshot(self):
        node = self.graph.get(self.node, {})
        kind = node.get('class_type')
        return {
            'execution_started_at': self.started_at,
            'node_label': NODE_LABELS.get(kind, kind),
            'step': self.step, 'steps': self.steps,
            'progress_unit': 'steps' if kind == 'KSampler' else None,
        }

    def wait(self, prompt_id, seconds, on_change):
        deadline = time.monotonic() + seconds
        self.open()
        while (remaining := deadline - time.monotonic()) > 0:
            if self.socket is None:
                time.sleep(remaining)
                break
            try:
                raw = self.socket.recv(timeout=remaining)
            except TimeoutError:
                break
            except (OSError, WebSocketException):
                self.close()
                self.node = self.step = self.steps = None
                self.retry_at = time.monotonic() + 3
                on_change()
                continue
            if isinstance(raw, str):
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if self.accept(message, prompt_id):
                    on_change()
                    if self.finished:
                        # Check HTTP history as soon as this prompt leaves execution.
                        break
