# Uninstalling

## Select the CLI before removal

Use the [management CLI selection](updating.md#choose-the-management-cli) procedure.
The removal commands below require an intact, verified **installed 1.5.3 CLI** at the
default managed home. For another home, replace both the CLI path and `--home` value.

The public 1.5.2 CLI's `stopDedicatedPm2` ignores failed `delete all` and `kill` calls
(`allowFailure: true`). Its uninstall can continue removing releases or purging the home
while PM2 is still alive. Running `npx ...@latest uninstall` executes that public CLI;
an installed 1.5.3 app does not add 1.5.3 safeguards to it. These commands therefore use
the installed CLI directly. If only 1.5.2 is available, first obtain a separately
validated 1.5.3 local distribution through the approved update process; the unpublished
candidate cannot be fetched by specifying `@1.5.3` on npm.

## Remove the app and preserve data

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" uninstall \
  --home "$HOME/.control-server"
```

1.5.3 checks the existing LaunchAgent's managed path, unloads the controller, then
attempts to stop its dedicated PM2 daemon and managed services. After successful
shutdown it removes the application releases, `current` and installation metadata.
Config, logs and runtime are preserved for recovery or reinstall.

If the attempted PM2 shutdown fails, the command aborts before deleting the app or
managed data, including when `--purge` was requested. The controller has already been
unloaded; an error does not mean the installation is still running unchanged. Keep the
files and resolve the shutdown problem through [Recovery](recovery.md) before retrying.
If the release, PM2 wrapper or bundled dependency directory is missing, recover the
installation first; the shutdown helper can skip its attempt when those files are absent.

The ownership check belongs to `lib/installer.mjs`; it rejects an existing plist that
does not contain this managed installation's `current` path. It does not protect direct
calls to [source `launchd/install.sh`](source-runtime.md#launchagent-소유권-사전-확인).

## Purge all managed data

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" uninstall --purge \
  --home "$HOME/.control-server"
```

`--purge` requires explicit confirmation and removes the managed home, including service
inventory, password, logs, checkpoints and PM2 state. It does not delete registered
projects or their working directories. Keep a private external backup before purge.
For an intentional non-interactive invocation, append the CLI's `--yes` option.
