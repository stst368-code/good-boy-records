"""Install an isolated CUDA worker environment, without downloading model weights."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=ROOT / ".venv")
    parser.add_argument("--runtime", type=Path, default=ROOT / "runtime")
    parser.add_argument('--skip-cuda-check', action='store_true', help='Container build only; CUDA is still checked at every job start')
    args = parser.parse_args()
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        raise SystemExit("Use 64-bit Python 3.12 or 3.11 for this verified worker profile")
    if sys.maxsize <= 2**32:
        raise SystemExit("A 64-bit Python installation is required")
    environment = args.environment
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    def run(*args):
        subprocess.run([str(python), *args], check=True)
    run("-m", "pip", "install", "--upgrade", "pip")
    run("-m", "pip", "install", "torch==2.10.0", "--index-url", "https://download.pytorch.org/whl/cu128")
    run("-m", "pip", "install", "-r", str(ROOT / "requirements.txt"))
    run("-m", "pip", "check")
    runtime = args.runtime
    runtime.mkdir(parents=True, exist_ok=True)
    freeze = subprocess.check_output([str(python), "-m", "pip", "freeze"], text=True, encoding="utf-8")
    (runtime / "installed-packages.txt").write_text(freeze, encoding="utf-8")
    if args.skip_cuda_check:
        subprocess.run([str(python), '-c', 'from _gbr_worker.hardware import check_install; check_install()'], cwd=ROOT, check=True)
        print('Worker dependencies installed; runtime GPU check will run at job start.')
        return
    report = subprocess.check_output([str(python), str(ROOT / "worker.py"), "doctor"], text=True, encoding="utf-8")
    json.loads(report)
    (runtime / "hardware-check.json").write_text(report, encoding="utf-8")
    print("Native worker installed and CUDA check passed. Model components download during the first test.")


if __name__ == "__main__":
    main()

