# Contributing

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
npm ci --prefix ops/pm2
npm install --ignore-scripts
```

Copy the public examples only for local development. Never commit `backend/config.yml`,
`launchd/run.env`, runtime state, logs, PM2 output, or real service credentials.

## Verification

```bash
.venv/bin/pytest -q
npm test
npm run typecheck --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run package:inspect
git diff --check
```

Lifecycle changes should also test start, stop, restart, crash recovery, config reload,
and rollback against the isolated PM2 runtime. A passing unit suite alone is not proof
that a destructive lifecycle path is safe.

## Pull requests

- Keep machine-specific integrations outside the core distribution.
- Preserve existing config/API/UI contracts unless the change includes a migration.
- Add regression coverage for rejected and partially successful process operations.
- Describe user-visible behavior, risk, and exact verification evidence.
