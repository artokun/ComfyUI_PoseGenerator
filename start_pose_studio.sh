#!/bin/sh
# Type "sh " in Terminal, drag this file in, then press Return.
set -eu

if [ -n "${POSE_STUDIO_DIR:-}" ]; then
  STUDIO_DIR=$POSE_STUDIO_DIR
else
  STUDIO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
  if [ ! -f "$STUDIO_DIR/scripts/pose_studio.py" ]; then
    # Browser downloads/previews may copy only this launcher out of the repo.
    STUDIO_DIR="$HOME/code/ComfyUI_PoseGenerator"
  fi
fi
if [ ! -f "$STUDIO_DIR/scripts/pose_studio.py" ]; then
  printf '%s\n' 'Project not found. Set POSE_STUDIO_DIR to the ComfyUI_PoseGenerator folder.' >&2
  exit 1
fi
cd "$STUDIO_DIR"

if [ ! -x "$STUDIO_DIR/.venv/bin/python" ]; then
  printf '%s\n' 'Missing .venv/bin/python. Create the project virtual environment first.' >&2
  exit 1
fi

if ! "$STUDIO_DIR/.venv/bin/python" -c 'import aiohttp, requests, PIL' 2>/dev/null; then
  printf '%s\n' 'Install dependencies from the project folder:' \
    '  .venv/bin/python -m pip install -r requirements-studio.txt' >&2
  exit 1
fi

printf '%s\n' 'Opening Pose Studio. Keep this Terminal window open while generating.'
exec "$STUDIO_DIR/.venv/bin/python" "$STUDIO_DIR/scripts/pose_studio.py" --open "$@"
