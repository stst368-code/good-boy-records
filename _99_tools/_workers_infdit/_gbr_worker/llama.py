"""Song-writing transport for llama.cpp's OpenAI-compatible HTTP server."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def address(value):
    value = value.strip().rstrip('/')
    if value.endswith('/v1'):value = value[:-3]
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise ValueError('Use the llama.cpp base URL, e.g. http://127.0.0.1:8080')
    return value


class LlamaClient:
    def __init__(self, endpoint):
        self.endpoint = address(endpoint)

    def request(self, path, data=None, timeout=10):
        payload = None if data is None else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        try:
            with urlopen(Request(self.endpoint + path, data=payload, headers={'Content-Type':'application/json'}), timeout=timeout) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:raise ValueError('Writing worker response exceeds 2 MiB')
            try:result = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):raise ValueError('Writing worker returned non-JSON at ' + path + '; check its server URL') from None
            if not isinstance(result,dict):raise ValueError('Writing worker returned an invalid response at ' + path)
            return result
        except HTTPError as error:
            detail = error.read(2000).decode('utf8', errors='replace')
            raise ValueError(f'Writing worker HTTP {error.code} at {path}: {detail}') from None
        except (URLError, TimeoutError) as error:
            raise ValueError('Writing worker is unavailable at ' + self.endpoint + ': ' + str(error)) from None

    def query(self):
        self.request('/health')
        result = self.request('/v1/models')
        if not isinstance(result.get('data'),list) or any(not isinstance(r,dict) for r in result['data']):raise ValueError('Writing worker model inventory is invalid')
        models = [r['id'] for r in result.get('data', []) if isinstance(r.get('id'), str)]
        if not models:raise ValueError('The writing worker did not advertise a loaded model')
        details = [{k:v for k,v in row.items() if k in {'id','object','owned_by','created','meta'}} for row in result.get('data',[]) if row.get('id') in models]
        return {'address':self.endpoint, 'models':models, 'model_details':details, 'gpu_name':None, 'usable_vram_bytes':None,
                'measurement_note':'The HTTP server does not advertise verified per-request GPU/VRAM measurements.'}

    def complete(self, messages, settings):
        discovered = self.query()
        model = settings.get('model') or discovered['models'][0]
        if model not in discovered['models']:raise ValueError('Selected writing model is not loaded on this worker')
        response = self.request('/v1/chat/completions', {'model':model, 'messages':messages,
            'temperature':settings['temperature'], 'top_p':settings['top_p'], 'max_tokens':settings['max_tokens'],
            'response_format':{'type':'json_object'}, 'stream':False}, timeout=300)
        if not isinstance(response.get('choices'),list) or not response['choices'] or not isinstance(response['choices'][0],dict):raise ValueError('Writing worker returned no completion')
        choice = response['choices'][0]
        if choice.get('finish_reason') == 'length':raise ValueError('Writing response was truncated; increase the output token limit before retrying')
        try:
            text = choice['message']['content']
            value = json.loads(text)
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError('Writing worker must return one JSON object; no YAML or reasoning text was applied') from None
        if not isinstance(value, dict):raise ValueError('Writing response is not a JSON object')
        return value, {'model':model, 'worker':discovered, 'usage':response.get('usage'), 'timings':response.get('timings')}
