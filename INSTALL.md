# Install NAO Fair on Linux or Windows

NAO Fair consists of two public repositories: the server and the frontend.
The robot microphone bridge is installed separately on the NAO. A working
installation needs internet access for GitHub, PyPI and npm, and a current
browser. For a real conversation, also provide the Gladia and OpenAI keys,
network access to the NAO on TCP 9559 or 9561 and 8765, and install the
[robot bridge](https://github.com/icampus-wildau/nao-websocket-bridge#install-from-a-laptop).
No robot or cloud keys are needed to edit configuration offline.

## Linux

Use Linux x86-64 with Python 3.12 for robot support, [uv](https://docs.astral.sh/uv/getting-started/installation/), Git, and Node.js 20.19+ or 22.12+ with npm. The locked `qi` package does not provide a Linux ARM wheel. Clone both repositories beside each other:

```bash
mkdir -p nao-fair && cd nao-fair
git clone https://github.com/icampus-wildau/nao-fair-server.git
git clone https://github.com/icampus-wildau/nao-fair-frontend.git
cd nao-fair-server
uv sync --locked --extra dev --extra browser --extra robot
mkdir -p -m 700 .venv/nltk_data
uv run python -m nltk.downloader -d .venv/nltk_data punkt_tab
cd ../nao-fair-frontend
npm ci
npm run build
cd ../nao-fair-server
uv run nao-fair
```

Open http://127.0.0.1:8000. Stop with Ctrl+C. Rebuild the frontend after changing it. The server automatically finds the sibling `dist` directory; for another layout set `NAO_FAIR_FRONTEND_DIR` to its absolute path before starting. For API-only use, or to install only some features, see [README.md](README.md#setup).

For frontend development instead, leave the backend running and in a second terminal run `npm run dev` from `nao-fair-frontend`, then open http://127.0.0.1:5173.

## Windows with WSL 2

Native Windows can run the frontend and API-only server, but `qi` has no Windows wheel. For robot support use WSL 2 or the Dev Container below.

1. Install [WSL 2](https://learn.microsoft.com/windows/wsl/install), a Linux distribution, Git, Node.js and npm in that distribution, and [uv](https://docs.astral.sh/uv/getting-started/installation/) inside WSL. On Windows, VS Code with the WSL extension is optional.
2. Open a WSL terminal and follow the **Linux** commands above inside it. Keep both checkouts together in WSL. Open http://127.0.0.1:8000 in a Windows browser.
3. Install the bridge using its Linux/WSL instructions. Verify the Windows/WSL firewall and network permit access to the robot.

Alternatively run the frontend on Windows and use its dev server; `npm run dev` proxies to the WSL server at localhost:8000. The production server can serve the frontend only if its build directory is accessible from WSL.

## Windows with a Dev Container

Install Git, Node.js 20.19+ or 22.12+ and npm on Windows; install Docker Desktop 4.34 or newer, VS Code or Cursor, and the Dev Containers extension. Docker Desktop must run Linux containers. Sign in to Docker Desktop and enable **Settings → Resources → Network → Enable host networking**, then apply and restart. Enhanced Container Isolation is incompatible with host networking.

Clone both repositories as siblings in a host terminal:

```powershell
mkdir nao-fair
cd nao-fair
git clone https://github.com/icampus-wildau/nao-fair-server.git
git clone https://github.com/icampus-wildau/nao-fair-frontend.git
```

Open `nao-fair-server` in VS Code or Cursor and select **Dev Containers: Reopen in Container**. The container installs all three extras and NLTK data at start. In its terminal run:

```bash
uv run nao-fair
```

On the Windows host, in a second terminal:

```powershell
cd nao-fair-frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The host frontend proxies the API to port 8000. The container only mounts the server checkout, so a host build is not automatically visible at port 8000. To serve a production build from the container, first run `npm run build` on the host, then add this entry to `mounts` in the server's `.devcontainer/devcontainer.json`, rebuild the container, and set `NAO_FAIR_FRONTEND_DIR=/opt/nao-fair-frontend/dist` in `containerEnv`:

```json
"source=${localWorkspaceFolder}/../nao-fair-frontend/dist,target=/opt/nao-fair-frontend/dist,type=bind,readonly"
```

The host `dist` directory must exist before starting the container. Restart the server after rebuilding it. Install the robot bridge from WSL or directly on the NAO as described in the bridge README.

## Troubleshooting

- If the Python wheel cannot be installed, verify Python 3.12, Linux x86-64, and that the robot extra runs in WSL or a Linux container.
- If localhost:8000 is unavailable in Docker Desktop, enable host networking and check that port 8000 is free.
- If the robot is unreachable, check its address, network route, firewall and ports 9559/9561 and 8765. `uv run python -c "import qi; s = qi.Session(); print(s); s.close()"` checks the library without a robot.
- `uv sync` removes extras not supplied in that invocation; pass all extras you want to keep.
