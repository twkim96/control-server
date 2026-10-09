# Contributing

Start with [the documentation index](docs/SPEC.md) and relevant [unfinished work](docs/TODO.md).
Development prerequisites, local execution and verification commands have one maintained
home in [Development setup](docs/development/setup.md).

## Pull requests

- Keep machine-specific integrations outside the core distribution.
- Never commit real config, run.env, runtime/logs, PM2 output or service credentials.
- Preserve existing config/API/UI contracts unless the change includes a migration.
- Add regression coverage for concrete behavior bugs and material partial-operation risks.
- For small reversible docs/style/config changes, review the diff and relevant links or UI;
  do not add tests that merely mirror tunable values.
- Describe user-visible behavior, risk, and exact verification evidence.
- Use project-wiki to update affected specifications, guides and TODO entries with the change.

Lifecycle changes also require the relevant isolated PM2 behavior checks and acceptance
in [Development setup](docs/development/setup.md#pm2-통합과-수용-구분).
