# Updating and rollback

Run the target package explicitly so the updater itself and the application release
come from the same version. General updates use the `latest` tag:

```bash
npx --yes @twkim96/control-server@latest update
```

The updater prepares the complete release, Python environment, PM2 dependency and
frontend before changing `current`. It then restarts the Control Server and waits for
`/api/meta`. A restart or health failure restores the previous `current` target and
attempts to restart the prior release.

Config, password, logs and runtime are outside release directories and are not replaced
by normal updates. Re-running the same version is idempotent.

If a problem appears after the immediate health gate passed, explicitly return to the
latest completed older release:

```bash
npx --yes @twkim96/control-server@latest rollback
```

Use `rollback --to 1.5.0` to select a particular installed older release. Rollback also
restarts and health-checks the selected release; failure restores the release that was
active before the rollback attempt.

Before an important update, keep a filesystem copy of `~/.control-server/config` and
`~/.control-server/runtime`. Do not copy a live PM2_HOME to a different location and
start both copies.

Release candidates should use the `next` tag:

```bash
npx --yes @twkim96/control-server@next update
```
