import hashlib
import json
import secrets
from pathlib import Path
from .util import atomic_text, dumps

DEFAULTS = {
    "input_directories": [], "archive_directory": "GBR_Reviewed", "data_directory": "data",
    "host": "127.0.0.1", "port": 8765, "scan_interval_seconds": 30,
    "fast_seconds": 10, "quick_seconds": 20, "standard_seconds": 50, "fade_ms": 35,
    "transition_sensitivity": "balanced",
    "reservation_queue_ahead": 2, "reservation_seconds": 180,
    "password_hash": "", "allowed_hosts": [], "ssl_certfile": "", "ssl_keyfile": "",
}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ":" + hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def password_ok(password, encoded):
    try:
        salt, _ = encoded.split(":", 1)
        return secrets.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


class Config:
    def __init__(self, path, allow_insecure_lan=False):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.values = {**DEFAULTS, **(json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {})}
        self.validate(allow_insecure_lan)
        if not self.path.exists():
            self.save()

    def validate(self, allow_insecure_lan=False):
        v = self.values
        if not allow_insecure_lan and v["host"] not in ("127.0.0.1", "localhost", "::1") and not v["password_hash"]:
            raise ValueError("LAN binding requires a password. Run: python run_review.py password")
        if not 1 <= int(v["port"]) <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        if not 5 <= float(v["quick_seconds"]) <= 120 or not 10 <= float(v["standard_seconds"]) <= 300:
            raise ValueError("Quick duration must be 5–120s; Standard 10–300s")
        if not 5 <= float(v["fast_seconds"]) <= 30:
            raise ValueError("Fast duration must be 5–30s")
        if v["transition_sensitivity"] not in ("sensitive", "balanced", "conservative"):
            raise ValueError("Transition sensitivity must be sensitive, balanced or conservative")
        if not isinstance(v["reservation_queue_ahead"], int) or not 0 <= v["reservation_queue_ahead"] <= 10:
            raise ValueError("Reserved queue ahead must be an integer from 0 to 10")
        if not isinstance(v["reservation_seconds"], int) or not 30 <= v["reservation_seconds"] <= 3600:
            raise ValueError("Reservation expiry must be 30–3600 seconds")
        if not 5 <= int(v["scan_interval_seconds"]) <= 3600 or not 0 <= int(v["fade_ms"]) <= 150:
            raise ValueError("Scan interval must be 5–3600s; fades 0–150ms")
        if bool(v["ssl_certfile"]) != bool(v["ssl_keyfile"]):
            raise ValueError("Configure both TLS certificate and key")
        if not isinstance(v['input_directories'], list) or not all(isinstance(p,str) and p.strip() for p in v['input_directories']):
            raise ValueError("input_directories must be an array of nonempty folder paths")
        if self.archive.is_relative_to(self.data) or self.data.is_relative_to(self.archive):
            raise ValueError("Archive and application data must be separate directories")
        if any(p.is_relative_to(self.archive) or p.is_relative_to(self.data) for p in self.inputs):
            raise ValueError("Input directories cannot be within the archive or application-data directory")

    def resolve(self, value):
        p = Path(value).expanduser()
        return (self.root / p).resolve() if not p.is_absolute() else p.resolve()

    @property
    def inputs(self):
        return list(dict.fromkeys(self.resolve(p) for p in self.values["input_directories"]))

    @property
    def data(self):
        return self.resolve(self.values["data_directory"])

    @property
    def archive(self):
        return self.resolve(self.values["archive_directory"])

    @property
    def local_only(self):
        return self.values["host"] in ("127.0.0.1", "localhost", "::1")

    def save(self):
        atomic_text(self.path, dumps(self.values) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
