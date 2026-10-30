# NAO Fair server

Operator backend for one NAO. One process, no login, and no simulator. It listens on `127.0.0.1:8000` unless `NAO_FAIR_HOST` or `NAO_FAIR_PORT` is set.

Requires Python 3.12 or newer. The robot extra depends on [`qi`](https://pypi.org/project/qi/), which has no Windows wheels. On Windows and macOS, run this server in the Dev Container described in [INSTALL.md](INSTALL.md).

## Setup

```bash
uv sync --locked --extra dev
```

Robot audio and NAOqi need `naoqi-control`, `nao-websocket-bridge-client`, and `nao-websocket-bridge-protocol` checked out next to this directory:

```bash
uv sync --locked --extra robot
```

OpenAI and Gladia keys are set in the console under **System → Schlüssel**, or with `OPENAI_API_KEY` and `GLADIA_API_KEY`. The console never receives the stored value. `NAO_BRIDGE_TOKEN` is the microphone-bridge path token.

## Start

```bash
uv run nao-fair
```

The API is at [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with Ctrl+C. Restart after backend changes, and stop any process already bound to the port first.

The process starts with no robot connected. Prompts, presets, setups, and settings can be edited and applied before a connection. A successful connection sends the applied configuration.

To serve the operator console from this process, build [nao-fair-frontend](https://github.com/icampus-wildau/nao-fair-frontend) and set `NAO_FAIR_FRONTEND_DIR` to its `dist` directory. The default is `../nao-fair-frontend/dist`. Without that build, the API still starts. For live frontend updates, run `npm run dev` in the frontend checkout and open [http://127.0.0.1:5173](http://127.0.0.1:5173).

## Test the conversation without a NAO

```bash
uv sync --locked --extra browser
mkdir -p -m 700 .venv/nltk_data
uv run python -m nltk.downloader -d .venv/nltk_data punkt_tab
```

Restart the server, open **System → Tests**, and click **Mikrofontest starten**. Browsers allow the microphone on `http://127.0.0.1:8000`, `http://localhost:8000`, or HTTPS. Set the Gladia key, and the OpenAI key when the LLM is enabled. Only one browser test can run at a time; disconnect a connected robot first.

```bash
uv run pytest
```

With the browser extra installed, `pytest` also runs a Pipecat smoke test that does not call cloud services.
