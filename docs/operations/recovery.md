# Managed installation recovery

This runbook covers npm-managed installations under `~/.control-server`. The older
source-checkout v1.4.0 cutover procedure and backup evidence are archived in
[Historical PM2 migration recovery](../history/pm2-migration-recovery.md).

## Safe first checks

Select the [management CLI](updating.md#choose-the-management-cli) before recovery.
For an intact installed 1.5.3 release at the default home, use its bundled CLI:

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" doctor \
  --home "$HOME/.control-server"
node "$HOME/.control-server/current/bin/control-server.mjs" status \
  --home "$HOME/.control-server"
```

If `current` is absent, inconsistent or selects an older app, inspect the pointer and
metadata first and use an intact retained, verified 1.5.3 distribution for management.
Replace both the CLI path and `--home` for a custom home. Do not use a registry tag as a
substitute for checking CLI version. Public 1.5.2 `doctor`/`status` can inspect an
installation but do not validate the independent engine or supply 1.5.3 recovery safeguards.

Inspect controller logs without printing `config/run.env`, service environment values,
or raw PM2 `jlist` output:

```bash
tail -80 ~/.control-server/logs/_launchd.err
tail -80 ~/.control-server/logs/_launchd.out
```

## Failed application update

An update prepares a complete release before switching `current`. In 1.5.3, an
installation lock serializes management mutations, rollback prerequisites are checked
before the switch, and metadata writes are atomic. Catchable restart, health or metadata
failures restore the prior state. Forced process death between app pointer and metadata
writes is not journaled. After an interruption, confirm both the symlink and metadata:

```bash
readlink ~/.control-server/current
sed -n '1,80p' ~/.control-server/install.json
```

For a problem found after the immediate health gate, and only when `current` still
selects the verified 1.5.3 CLI:

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" rollback \
  --home "$HOME/.control-server"
```

The 1.5.3 CLI rollback selects a completed older release and performs a restart/health
gate, restoring the originally active release on catchable activation failure.
Pre-1.5.3 apps may not understand an independently selected PM2 engine; backward app
rollback compatibility is unverified. Do not infer that an app rollback restores the engine.

## Failed or interrupted PM2 engine update (1.5.3)

The [engine specification](../SPEC/pm2-engine.md) defines normal update and rollback
semantics. This procedure handles a failed/interrupted background job. A failed job
is not a successful upgrade, even when `rolled_back=true` confirms recovery.

Use the installed 1.5.3 settings update/recovery button, or its verified bundled CLI:

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" pm2 check \
  --home "$HOME/.control-server"
node "$HOME/.control-server/current/bin/control-server.mjs" pm2 update \
  --home "$HOME/.control-server"
```

An existing `runtime/pm2-engine/recovery.json` journal causes the update worker to attempt
recovery of the previous engine and service snapshot first. A recovery-only attempt can
report `failed` with `rolled_back=true`; after recovery, a further explicit update starts
a fresh upgrade. If recovery still fails, retain the journal and private backups for
investigation. Do not delete recovery files or manually switch pointers to dismiss an error.

Keep the dedicated PM2_HOME unchanged. Review actual daemon version and service
state as well as the job result. Do not print raw PM2 dumps, recovery snapshots or
environment values: they may contain service credentials.

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

The [uninstall procedure](uninstall.md) uses a verified installed 1.5.3 CLI and defines
shutdown-failure preservation. Its default removal retains config/runtime/logs for
reinstallation; purge needs an external backup for recovery. A 1.5.3 PM2 shutdown failure
leaves app/data available, but the controller has already been unloaded. Resolve shutdown
before retrying; do not delete a runtime used by a live daemon.

The public 1.5.2 CLI can ignore that shutdown failure and continue removal, even when the
installed app was 1.5.3. Once its release files have been removed, a `current` CLI path
cannot be used. Retain the surviving data and private backups and obtain a validated
management distribution; do not assume `npx ...@latest` restores the 1.5.3 protection.
