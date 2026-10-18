"""Create/reuse the existing controller environment and repair changed dependencies once."""
import hashlib
from pathlib import Path
import subprocess
import sys


def bootstrap(root, arguments):
    review = Path(root) / '_03_Review' / '_Generation_Review'
    environment = review / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    requirements = review / 'requirements.txt'
    if Path(sys.prefix).resolve() != environment.resolve():
        if not python.is_file():
            print('Preparing the GBR application environment…', flush=True)
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True)
        # Preserve the venv executable path; do not resolve its interpreter symlink.
        return subprocess.call([str(python), str(Path(root) / '_97_App/run_gbr.py'), *arguments])
    signature = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = environment / 'gbr-requirements.sha256'
    if not marker.is_file() or marker.read_text().strip() != signature:
        print('Updating GBR application dependencies…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        from zoneinfo import ZoneInfo
        ZoneInfo('Europe/London')
        marker.write_text(signature)
    return None
