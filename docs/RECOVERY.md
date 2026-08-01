# Managed installation recovery

This runbook covers npm-managed installations under `~/.control-server`. The older
source-checkout PM2 migration rollback remains in [PM2_RECOVERY.md](../PM2_RECOVERY.md).

## Safe first checks

```bash
npx --yes @twkim96/control-server@next doctor
npx --yes @twkim96/control-server@next status
```

If npm is temporarily unavailable but `current` is intact, run the bundled CLI:

```bash
node ~/.control-server/current/bin/control-server.mjs doctor
```

Inspect controller logs without printing `config/run.env`, service environment values,
or raw PM2 `jlist` output:

```bash
tail -80 ~/.control-server/logs/_launchd.err
tail -80 ~/.control-server/logs/_launchd.out
```

## Failed update

An update prepares a complete release before switching `current`. If launchd restart or
`/api/meta` fails, the updater restores the former symlink and restarts the prior release.
Confirm both the symlink and metadata:

```bash
readlink ~/.control-server/current
sed -n '1,80p' ~/.control-server/install.json
```

For a problem found after the immediate health gate:

```bash
npx --yes @twkim96/control-server@next rollback
```

Rollback only selects an older release whose completion marker exists. It also performs
a restart and health gate, restoring the originally active release if activation fails.

## Data recovery

Application releases are disposable. The recovery-critical data is:

- `config/config.yml`
- `config/run.env`
- `runtime/`, especially the last-good config and isolated PM2_HOME
- `logs/` when operational history is required
- `install.json`
- the installed `~/Library/LaunchAgents/com.twkim.server-control.plist`

Keep backups mode 0600/0700. Never start a copied PM2_HOME while the original daemon is
still alive. Moving PM2_HOME requires all managed services and the dedicated daemon to
be stopped first.

## Uninstall recovery

Default uninstall preserves config, runtime and logs. Reinstalling reconstructs the app
release and uses the preserved data. `uninstall --purge` is irreversible unless an
external backup exists.
