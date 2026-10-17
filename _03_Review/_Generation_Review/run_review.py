"""Run GBR Review with either supported package directory name."""
import os
from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent
    packages = [name for name in ("review", "gbr_review")
                if (root / name / "__main__.py").is_file()]
    if len(packages) != 1:
        raise SystemExit("Keep exactly one application package folder beside run_review.py: review or gbr_review.")
    sys.path.insert(0, str(root))
    os.chdir(root)
    runpy.run_module(packages[0], run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
