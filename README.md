# nao-fair-server

*Operator backend of the NAO Fair app for one NAO.*

![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square)
![CI](https://img.shields.io/github/actions/workflow/status/icampus-wildau/nao-fair-server/ci.yml?style=flat-square&label=CI)
![License: Apache 2.0](https://img.shields.io/badge/license-Apache%20License%202.0-blue)

App to manage the features of the NAO Fair app for one NAO:
- Control the Pipecat speech pipeline
- Store and apply presets / scenarios for the robot in different settings, e.g. fairs, exhibitions, etc.
- Connect to the robot and control its features
- Control autostart assignments for the NAO

It listens on `127.0.0.1:8000` and can be accessed via the [NAO Fair frontend](https://github.com/icampus-wildau/nao-fair-frontend). The combined install with the frontend is at [nao-fair](https://github.com/icampus-wildau/nao-fair). The steps below are for this repository on its own.

The API-only install supports Python 3.12 or newer. The robot extra needs Python **3.12** on Linux x86-64: `naoqi-control` requires it, and the locked [`qi`](https://pypi.org/project/qi/) wheel has no Windows or Linux ARM build. On Windows, use WSL or the Dev Container in [INSTALL.md](INSTALL.md).

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git.

```bash
git clone https://github.com/icampus-wildau/nao-fair-server.git
cd nao-fair-server
uv sync --locked --extra dev                              # API and tests, no robot
# uv sync drops extras that this command does not name:
uv sync --locked --extra dev --extra robot --extra browser
```

The robot libraries come from PyPI. OpenAI and Gladia keys are set in the app under **System → Schlüssel**, or with `OPENAI_API_KEY` and `GLADIA_API_KEY`. The app never receives the stored value. `NAO_BRIDGE_TOKEN` is the microphone-bridge path token.

## Start

```bash
uv run nao-fair   # http://127.0.0.1:8000 ; Ctrl+C stops it
```

The process starts with no robot connected. Prompts, presets, setups, and settings can be edited and applied before a connection. A successful connection sends the applied configuration.

To serve the app from this process, build [nao-fair-frontend](https://github.com/icampus-wildau/nao-fair-frontend) and set `NAO_FAIR_FRONTEND_DIR` to its `dist` directory. The default is `../nao-fair-frontend/dist`. Without that build, the API still starts. For live frontend updates, run `pnpm run dev` in the frontend checkout and open [http://127.0.0.1:5173](http://127.0.0.1:5173). From a [nao-fair](https://github.com/icampus-wildau/nao-fair) checkout, `./start-nao-fair.sh` and `./start-nao-fair-dev.sh` do this.

## Test the conversation without a NAO

```bash
uv sync --locked --extra dev --extra browser --extra robot   # omit --extra robot for a browser-only setup
mkdir -p -m 700 .venv/nltk_data
uv run python -m nltk.downloader -d .venv/nltk_data punkt_tab
```

Restart the server, open **System → Tests**, and click **Mikrofontest starten**. Browsers allow the microphone on `http://127.0.0.1:8000`, `http://localhost:8000`, or HTTPS. Set the Gladia key, and the OpenAI key when the LLM is enabled. Only one browser test can run at a time. Disconnect a connected robot first.

```bash
uv run pytest   # with the browser extra, also a Pipecat smoke test that does not call cloud services
```

CI on Linux installs every extra. The Windows job checks the API-only environment. Robot support on Windows still needs WSL or the Dev Container, and a hardware check.
