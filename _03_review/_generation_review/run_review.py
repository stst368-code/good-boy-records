"""Run the review interface from its canonical package directory."""
import os
from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent
    if not (root / '_review/__main__.py').is_file():
        raise SystemExit('The _review package is missing beside run_review.py.')
    sys.path.insert(0, str(root))
    os.chdir(root)
    runpy.run_module('_review', run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
