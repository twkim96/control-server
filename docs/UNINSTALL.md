# Uninstalling

The default uninstall stops the dedicated managed services and Control Server,
unloads its LaunchAgent, and removes application releases. Config, logs and runtime are
preserved for recovery or reinstall:

```bash
npx --yes @twkim96/control-server@latest uninstall
```

For non-interactive use, add `--yes`.

Complete deletion requires an explicit purge confirmation:

```bash
npx --yes @twkim96/control-server@latest uninstall --purge
```

`--purge` removes the managed home including service inventory, password, logs,
checkpoints and PM2 state. It does not delete the projects or working directories of
registered services.

The uninstaller refuses to unload a same-named LaunchAgent owned by another installation
path.
