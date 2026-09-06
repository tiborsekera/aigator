# Contributing

Use synthetic conversations for fixtures, bug reports, and screenshots. Never commit personal exports, tokens, or databases.

From a checkout with Python 3.10+ (SQLite FTS5) and Node 22:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tests/installed_smoke.py
npm ci --ignore-scripts
npm test
bash -n install.sh
```

On Windows, use `.venv\Scripts\python.exe`. Python has no core runtime dependencies; npm dependencies are only for DOM tests. Tests exercise synthetic parser payloads, HTTP authentication, WAL updates, shell installation failures, dashboard rendering, popup controls, and background relay. DOM tests do not establish compatibility with current SaaS layouts or browser local-network policies. Test capture manually before claiming a new browser/platform is supported.

Installers preserve previous marked installations as backups and refuse unmarked targets. Never test an installer against your real application/data directory.
