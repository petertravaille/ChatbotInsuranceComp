#!/bin/bash
# Double-click this file on a Mac to start the PT Health Assistant demo.
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/ and try again."
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi

if [ ! -d .venv ]; then
  echo "First run: setting things up (takes a minute)..."
  python3 -m venv .venv || exit 1
fi
source .venv/bin/activate
pip install -q -r requirements.txt || exit 1

# The API key is asked once and kept in .anthropic_key in this folder (never uploaded to GitHub).
if [ -z "$ANTHROPIC_API_KEY" ]; then
  if [ ! -s .anthropic_key ]; then
    read -r -p "Paste your Anthropic API key (starts with sk-ant-) and press Enter: " KEY
    printf '%s' "$KEY" > .anthropic_key
    chmod 600 .anthropic_key
  fi
  export ANTHROPIC_API_KEY="$(cat .anthropic_key)"
fi

echo "Starting... the demo opens in your browser. Close this window to stop it."
(sleep 2; open "http://localhost:5000") &
python app/server.py
