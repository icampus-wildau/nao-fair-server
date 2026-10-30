# Windows and macOS

The [`qi`](https://pypi.org/project/qi/) package has no Windows wheels, and the robot extra depends on it. Python for this server has to run on Linux. The Dev Container does that inside Docker. WSL is the fallback when Docker is not available.

Linux can skip this page and follow the [README](README.md).

The frontend stays on the host in every setup. It does not use this container.

This project pins Python 3.12. The container installs the robot libraries from PyPI. No neighboring checkouts are required.

## Dev Container

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) running, VS Code or Cursor, and the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension (`ms-vscode-remote.remote-containers`).

In Docker Desktop, sign in, open **Settings → Resources → Network**, enable **Enable host networking**, then apply. The container uses the host network (`--network=host`), so the API on `127.0.0.1:8000` is the host's port 8000, and the server reaches the robot on the same routes as the host.

1. Open the `nao-fair-server` folder in VS Code or Cursor.
2. Command Palette → **Dev Containers: Reopen in Container**.
3. Wait for the image build. On start the container runs `uv sync` with the dev, browser, and robot extras, and downloads the NLTK `punkt_tab` data when it is missing.
4. Use the editor terminal. It is inside the container. Python is `/opt/venv/bin/python`. `uv run` uses that environment. When `.venv` is absent, start creates a symlink to `/opt/venv`, so `.venv/bin/python` is the same interpreter.

Start the API:

```bash
uv run python -m nao_fair
```

Stop it with Ctrl+C. Restart after backend code changes. Stop an existing server on port 8000 before starting another.

Check `qi` alone:

```bash
uv run python -c "import qi; s = qi.Session(); print('OK:', s); s.close()"
```

`No Application was created` is harmless when that check runs off the robot.

### Frontend on the host

In a host terminal, from `nao-fair-frontend` (outside the container):

```bash
npm ci
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The dev server proxies `/api` to [http://127.0.0.1:8000](http://127.0.0.1:8000). `NAO_FAIR_API_URL` overrides that target.

For the console served by the API, build on the host instead:

```bash
npm ci
npm run build
```

The server reads `NAO_FAIR_FRONTEND_DIR`, or `../nao-fair-frontend/dist` when that variable is unset, and serves it at [http://127.0.0.1:8000](http://127.0.0.1:8000). The Dev Container workspace is only this repository, so that relative path is absent there. Use the Vite dev server, or set `NAO_FAIR_FRONTEND_DIR` to a build mounted into the container. Rebuild after frontend changes.

## WSL

Use this when Docker Desktop is not available.

Install WSL 2 (`wsl --install`), [uv](https://docs.astral.sh/uv/getting-started/installation/) inside the distro, and the [WSL extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl) (`ms-vscode-remote.remote-wsl`). Open this folder with **WSL: Reopen Folder in WSL**, then:

```bash
uv python pin 3.12
uv sync --locked --all-extras
mkdir -p -m 700 .venv/nltk_data
uv run python -m nltk.downloader -d .venv/nltk_data punkt_tab
uv run python -m nao_fair
```

Run the frontend on the Windows host or inside WSL with `npm run dev`. WSL 2 publishes the server's localhost port 8000 on the Windows host, so the Vite proxy still targets `127.0.0.1:8000`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Dev Container build fails | Start Docker Desktop and retry |
| Host cannot open port 8000 | Enable **host networking** in Docker Desktop, then rebuild the container |
| `no wheels with a matching platform tag` | Run Python in the Dev Container or WSL |
| `.venv/bin/python` is not the container interpreter | Remove a host `.venv` and reopen, or call `uv run python` |
| `connect()` hangs, then times out | Check the robot address, power, and ports 9559 and 9561 |
| `No Application was created` | Harmless when a `qi` check runs off the robot |

Day-to-day server use after this setup is in the [README](README.md).
