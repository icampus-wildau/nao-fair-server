#!/bin/bash
set -euo pipefail

for sibling in ../naoqi-control ../nao-websocket-bridge-client ../nao-websocket-bridge-protocol; do
  if [[ ! -f "$sibling/pyproject.toml" ]]; then
    echo "Missing $sibling. Open this folder from the NAO checkout that contains the sibling repositories." >&2
    exit 1
  fi
done

sudo mkdir -p /opt/venv
sudo chown -R vscode:vscode /opt/venv
uv sync --locked --all-extras --compile-bytecode

if [[ ! -e .venv ]]; then
  ln -s /opt/venv .venv
fi

if [[ ! -d /opt/venv/nltk_data/tokenizers/punkt_tab ]]; then
  mkdir -p -m 700 /opt/venv/nltk_data
  uv run python -m nltk.downloader -d /opt/venv/nltk_data punkt_tab
fi
