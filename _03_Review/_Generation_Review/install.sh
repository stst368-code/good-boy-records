#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
python3 -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,13), 'Use Python 3.11–3.13'"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_review.py setup
echo 'Installed. Start with: bash start.sh'
