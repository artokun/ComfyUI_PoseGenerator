#!/bin/zsh
# Double-click in Finder. Paths are resolved from this file, not Terminal's cwd.
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'Missing .venv/bin/python. Create the project virtual environment first.'
  read -r '?Press Return to close.'
  exit 1
fi
if ! .venv/bin/python -c 'import aiohttp, requests, PIL' 2>/dev/null; then
  print 'Install web dependencies with: .venv/bin/python -m pip install -r requirements-studio.txt'
  read -r '?Press Return to close.'
  exit 1
fi
print 'Pose Studio opens in your browser. Keep this window open while generating.'
.venv/bin/python scripts/pose_studio.py --open
if [[ $? -ne 0 ]]; then
  read -r '?Press Return to close.'
fi
