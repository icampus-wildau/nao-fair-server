# NAO Fair server

For domain changes, read [GLOSSARY.md](GLOSSARY.md) and the affected section of
[docs/behavior.md](docs/behavior.md). For an architectural change, consult the
[ADR index](docs/adr/README.md). Setup and test commands are in [README.md](README.md).

## Change boundaries

- `app.py` exposes the HTTP/WebSocket API; `models.py` defines strict payloads.
  Update frontend API types and consumers when changing that contract.
- `runtime.py` owns applied state, connection and conversation lifecycle, event
  publication, and action dispatch. `store.py` owns SQLite persistence and legacy
  scenario migration; `compose.py` builds prompt sections.
- `robot/live.py` adapts real NAOqi and the microphone stream; `booth/` owns the
  fair conversation. The standalone `nao-pipe-cat-devcontainer` is not imported.
- `touch.py` and `dialog.py` have Python 2.7 counterparts in
  `nao-fair-autostart`. Assignment changes also affect `robot/autostart.py` and
  the robot's `contract.py`, `engine.py`, and `service.py`.
- Built-in defaults in `seed.py`, `defaults.py`, and `booth/config.py` affect new
  databases only. Use temporary test data to verify them;
  preserve the operator's `data/` directory and secrets.
- `active_setup_id`, legacy preset/setup models, and store methods remain for
  compatibility. The current API exposes scenarios; these names are not a reason
  to reintroduce setup activation.

## Verification

Use the existing tests for the affected behavior, then the server suite for
runtime/API changes. API tests use temporary stores and the test driver;
`test_server.py` is a test-only entry point. A browser test runs the real cloud
pipeline and is distinct from that driver. Report hardware or cloud validation
only when it was actually performed.

Dependencies are registry releases in `pyproject.toml` and `uv.lock`. From this
checkout, a temporary sibling overlay is `uv add --editable ../../naoqi-control`
(likewise for the bridge client or protocol); restore both files afterward.
