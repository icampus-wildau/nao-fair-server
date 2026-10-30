# NAO Fair server

Operator backend for one NAO. One process, no login, and no simulator. It listens on `127.0.0.1:8000` unless `NAO_FAIR_HOST` or `NAO_FAIR_PORT` is set.

The API-only install supports Python 3.12 or newer. For the robot extra use Python **3.12** on Linux x86-64: `naoqi-control` requires it and the locked [`qi`](https://pypi.org/project/qi/) has no Windows or Linux ARM wheels. On Windows, use WSL or the Dev Container in [INSTALL.md](INSTALL.md).

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. Clone this repository on Linux, then choose one command for the features you need:

```bash
git clone https://github.com/icampus-wildau/nao-fair-server.git
cd nao-fair-server
uv sync --locked --extra dev                              # API and tests, no robot
# Or, for robot, browser microphone test, and tests together:
uv sync --locked --extra dev --extra robot --extra browser
```

`uv sync` removes extras not selected in the current command. When adding an extra later, include every extra you still need. The robot libraries come from PyPI, so their source checkouts are not required. See [INSTALL.md](INSTALL.md) for the complete frontend and platform setup.

OpenAI and Gladia keys are set in the console under **System → Schlüssel**, or with `OPENAI_API_KEY` and `GLADIA_API_KEY`. The console never receives the stored value. `NAO_BRIDGE_TOKEN` is the microphone-bridge path token.

## Start

```bash
uv run nao-fair
```

The API is at [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with Ctrl+C. Restart after backend changes, and stop any process already bound to the port first.

The process starts with no robot connected. Prompts, presets, setups, and settings can be edited and applied before a connection. A successful connection sends the applied configuration.

To serve the operator console from this process, build [nao-fair-frontend](https://github.com/icampus-wildau/nao-fair-frontend) and set `NAO_FAIR_FRONTEND_DIR` to its `dist` directory. The default is `../nao-fair-frontend/dist`. Without that build, the API still starts. For live frontend updates, run `pnpm run dev` in the frontend checkout and open [http://127.0.0.1:5173](http://127.0.0.1:5173).

## Test the conversation without a NAO

```bash
# Include robot too if it is needed:
uv sync --locked --extra dev --extra browser --extra robot
mkdir -p -m 700 .venv/nltk_data
uv run python -m nltk.downloader -d .venv/nltk_data punkt_tab
```

Restart the server, open **System → Tests**, and click **Mikrofontest starten**. Browsers allow the microphone on `http://127.0.0.1:8000`, `http://localhost:8000`, or HTTPS. Set the Gladia key, and the OpenAI key when the LLM is enabled. Only one browser test can run at a time; disconnect a connected robot first.

For a browser-only setup without robot support, omit `--extra robot` from the sync command.

```bash
uv run pytest
```

With the browser extra installed, `pytest` also runs a Pipecat smoke test that does not call cloud services.
