#!/bin/bash
set -euo pipefail

# The named volume is mounted here before uv runs. An existing directory with
# no interpreter is not an environment uv will replace.
sudo chown -R vscode:vscode /opt/venv
if [[ ! -x /opt/venv/bin/python ]]; then
  find /opt/venv -mindepth 1 -delete
  uv venv /opt/venv
fi

uv sync --locked --all-extras --compile-bytecode

if [[ ! -e .venv ]]; then
  ln -s /opt/venv .venv
fi

if [[ ! -d /opt/venv/nltk_data/tokenizers/punkt_tab ]]; then
  mkdir -p -m 700 /opt/venv/nltk_data
  uv run python -m nltk.downloader -d /opt/venv/nltk_data punkt_tab
fi
