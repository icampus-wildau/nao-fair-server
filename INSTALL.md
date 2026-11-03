# Install NAO Fair

The server and the frontend are installed and started from [nao-fair](https://github.com/icampus-wildau/nao-fair). That README is the install and usage guide, including Linux, WSL, the production container, and the dev container.

```bash
git clone --recurse-submodules https://github.com/icampus-wildau/nao-fair.git
cd nao-fair
./install.sh
./start-nao-fair.sh
```

An API-only setup of this repository, without the frontend, remains in [README.md](README.md#setup). The dev container in `.devcontainer/` is the editor environment for this server. After cloning nao-fair, open `nao-fair-server` and choose **Dev Containers: Reopen in Container**.
