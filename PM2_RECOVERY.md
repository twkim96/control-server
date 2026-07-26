# PM2 migration recovery

This runbook restores Control Server to the pre-PM2 runtime after a failed v1.4.0
cutover. It deliberately keeps secrets and machine-specific paths out of Git.
The exact backup directory, running-service list, ports, PIDs, and checksums are
recorded in the private snapshot copied with each backup.

## Recovery assets

- Git restore tag: `pre-pm2-v1.4.0-20260724`
- Git restore commit: `bf6009ff38196636646359a21ce8ab8bc7daf70c`
- Private backup: locate the timestamped `pre-pm2-v1.4.0-*` directory recorded
  in `backend/runtime/pre_pm2_snapshot_*.md` or the external backup inventory.
- Original supervisor: `com.twkim.server-control` launchd LaunchAgent.
- Original config source: `backend/config.yml`.

Never copy `launchd/run.env`, `backend/config.yml`, a PM2 dump, or runtime secret
files into Git. Keep the external backup readable only by the current user.

## Before changing runtime state

1. Stop the migration and do not start another service.
2. Record the current PM2 application list and listening ports without printing
   application environments.
3. Ensure the Git worktree is clean or preserve unfinished source changes on a
   separate branch/commit.
4. Verify the private backup checksums before overwriting any local file.
5. Read the private snapshot to identify which services were running before the
   cutover. Do not infer the list from PM2 after a failed migration.

## Roll back managed services

Use only the dedicated Control Server `PM2_HOME`. Never run `pm2 delete all` or
`pm2 kill` against the user's default `~/.pm2` instance.

1. Stop each Control Server PM2 application by its namespaced service name.
2. Confirm that its configured listener port is free.
3. Confirm that child processes such as ffmpeg, Bun, or shell wrapper children
   are no longer running.
4. Delete only the namespaced Control Server PM2 entries.
5. Stop the dedicated PM2 daemon after all managed services are down.
6. Leave Sunshine stopped unless it was explicitly running in the private
   pre-cutover snapshot.

Use the repository wrapper so every command targets the dedicated PM2 instance:

```bash
scripts/pm2ctl.sh status
scripts/pm2ctl.sh stop server-control--SERVICE_ID
scripts/pm2ctl.sh delete server-control--SERVICE_ID
scripts/pm2ctl.sh kill
```

Run stop/delete once per namespaced service and verify its port after each stop.
The final `kill` is allowed only after every dedicated application is stopped and
deleted. The wrapper forces the private `PM2_HOME`; do not replace it with a bare
global `pm2` command during recovery.

Do not use raw `jlist` for manual inspection: it includes each application's
full `pm2_env` and can leave credentials in terminal scrollback.

## Restore source code

The pre-migration source is permanently identified by the Git tag above.

```bash
cd /path/to/control-server
git status --short
git switch -c recovery/pre-pm2-v1.4.0 pre-pm2-v1.4.0-20260724
```

If that recovery branch already exists, switch to it instead of recreating it.
Do not use a destructive reset when uncommitted user work is present.

Reinstall the original Python and frontend dependencies only if the migration
changed them. The backed-up Git bundle can restore the tag even if the normal
working repository is damaged or the remote is unavailable.

## Restore private operational files

Restore the following from the verified external backup while preserving file
permissions:

- `backend/config.yml`
- `launchd/run.env`
- `backend/runtime/`
- `backend/logs/` when historical logs are required
- the private pre-PM2 snapshot and update plan

The installed LaunchAgent plist is also backed up. Prefer regenerating it from
the restored repository template and `launchd/run.env`; use the copied installed
plist only if regeneration is impossible.

Ensure secret-bearing files and directories remain private after the copy.

## Restore the original Control Server

From the restored source checkout:

```bash
bash launchd/install.sh unload
bash launchd/install.sh load
bash launchd/install.sh status
```

Then confirm that the Control Server is the only process listening on port 9000
and that its local health/UI surface responds before restoring child services.

## Restore the pre-cutover service set

1. Open the restored Control Server.
2. Compare the service list with the private snapshot.
3. Start only services recorded as running before cutover.
4. After each start, verify its PID, configured port, health result, URL, and log
   stream before starting the next service.
5. Confirm that `autostart: false` services did not start implicitly.
6. Confirm that Sunshine remains stopped when it was absent from the snapshot.

Do not start a service when its port is already occupied. Identify and resolve
the existing holder first.

## Recovery verification

Recovery is complete only when all of the following hold:

- The checkout resolves to the tagged pre-PM2 source or an intentional fix based
  on that source.
- The original launchd Control Server is loaded and port 9000 responds.
- No dedicated Control Server PM2 application still owns a managed-service port.
- Every service that was running in the private snapshot is healthy on the same
  port, and services that were stopped remain stopped.
- Service and Action logs are readable through the existing UI/SSE endpoints.
- Tailscale HTTPS services work through their original private URL.
- No unexpected child process or duplicate listener remains.
- Backend tests and frontend typecheck/lint/build pass on the restored source.

## Backup integrity

Each physical backup must contain:

- a Git bundle containing the restore tag;
- a private operational-file copy;
- the installed LaunchAgent plist;
- the ignored v1.4.0 plan;
- the private runtime snapshot;
- `SHA256SUMS` covering regular backup files.

Test the Git bundle with `git bundle verify` and verify file checksums before the
PM2 cutover. A backup that has merely been copied but not read back is not a
completed restore point.
