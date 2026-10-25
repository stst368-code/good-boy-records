"""Simple password-protected HTTP access on the same local network."""
import ipaddress
import secrets
import socket
from .config import password_hash, password_ok
from .util import atomic_text


def addresses():
    candidates = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            candidates.add(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted(ip for ip in candidates if ipaddress.ip_address(ip).is_private
                  and not ipaddress.ip_address(ip).is_loopback and not ipaddress.ip_address(ip).is_link_local)


def access_code(config):
    path = config.data / "mobile-code.txt"
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if password_ok(value, config.values["password_hash"]):
            return value
    return None


def enable_http(config):
    # Invoked explicitly by Start-Mobile-Windows.bat / the mobile command.
    # Retain any existing user password. Generate an access code only when
    # no password exists; show it on the host, never to remote visitors.
    original = config.path.read_text(encoding="utf-8") if config.path.exists() else None
    if original:
        backup = config.data / "backups" / "config-before-mobile.json"
        if not backup.exists():
            atomic_text(backup, original)
            try:
                backup.chmod(0o600)
            except OSError:
                pass
    config.values.update(host="0.0.0.0", ssl_certfile="", ssl_keyfile="")
    if not config.values["password_hash"]:
        code = str(secrets.randbelow(900000) + 100000)
        config.values["password_hash"] = password_hash(code)
        path = config.data / "mobile-code.txt"
        atomic_text(path, code + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    config.validate()
    config.save()
    return access_code(config)


def connection_info(config):
    scheme = "https" if config.values["ssl_certfile"] else "http"
    return {"enabled": not config.local_only,
            "urls": [f"{scheme}://{ip}:{config.values['port']}" for ip in addresses()] if not config.local_only else [],
            "access_code": access_code(config) if not config.local_only else None}
