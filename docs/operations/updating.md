# Updating and rollback

The protections described here are those of the 1.5.3 implementation. Recorded local
acceptance is in [CHANGELOG](../../CHANGELOG.md), and current release and compatibility
gates are in [TODO](../TODO.md).

## Choose the management CLI

On 2026-10-06, the npm registry lists `latest` and `next` as **1.5.2**, with only
1.5.0/1.5.1/1.5.2 published. The locally installed app is 1.5.3. Recheck the registry
before choosing a future package:

```bash
npm view @twkim96/control-server dist-tags versions --json
```

`npx ...@latest` or `@next` executes the package selected by that tag, not the installed
app's CLI. A global `control-server` command may also be an older version. CLI version
and app version must be checked separately; a 1.5.3 app does not give a 1.5.2 CLI the
installation lock, metadata rollback or shutdown-failure safeguards added in 1.5.3.

For an existing default managed installation, inspect its bundled CLI version without
running a management operation:

```bash
node -p "require(process.env.HOME + '/.control-server/current/package.json').version"
```

The installed 1.5.3 management examples in this guide assume that this returns `1.5.3`
and `current` selects an intact validated release. Use
`node "$HOME/.control-server/current/bin/control-server.mjs"` with explicit
`--home "$HOME/.control-server"` to select that CLI and installation. Replace both paths
for a custom home. After rollback, `current` selects the older CLI; retain a verified
1.5.3 distribution outside the selected release when its management protections are
needed for further recovery.

## Application updates

For an installation intentionally targeting the currently published **1.5.2**, the
public package route is:

```bash
npx --yes @twkim96/control-server@1.5.2 update
```

This command cannot install the unpublished 1.5.3 candidate or provide its new management
protections. It rejects a downgrade from an already installed 1.5.3 app. When a candidate
is published later, choose and verify that exact package before using its updater.

To apply the already validated local 1.5.3 distribution, use its complete extracted
application directory (including frontend build and runtime locks), for example:

```bash
node /path/to/verified-1.5.3/bin/control-server.mjs update \
  --home "$HOME/.control-server"
```

This placeholder must be an existing, approved distribution, not an assumed npm download
or an incomplete checkout. The updater takes the target app from its own package root.
Running the installed CLI's `update` does not fetch a newer app from npm; it selects its
own version and can restart the controller even when that version is already installed.

The **1.5.3 updater** prepares the complete release, Python environment, initial bundled
PM2 fallback and frontend before changing app `current`. It restarts Control Server and
waits for `/api/meta`. Management mutations share an installation lock and metadata is
written atomically. Catchable activation/metadata failures restore the previous state.
Forced process death between app pointer and metadata writes is **not journaled**;
inspect both before recovery. These new protections are not claims about the public
1.5.2 updater.

Config, password, logs and runtime remain outside app releases. In 1.5.3 the cookie changes
from `session` to `server_control_session`: users log in again, without a legacy fallback.

## Application rollback

For a problem after the immediate health gate, use the verified installed 1.5.3 CLI:

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" rollback \
  --home "$HOME/.control-server"
```

`--to VERSION` selects a completed installed older release. The **1.5.3 CLI** checks
dependencies before the pointer switch, restarts and health-checks the target, and restores
the previous selection on catchable activation/metadata failure. If `current` is missing
or already changed, use an intact retained 1.5.3 CLI with the same `--home` after inspecting
the pointer and metadata; see [Recovery](recovery.md).

**Actual pre-1.5.3 apps may not understand the independent PM2 engine pointer, and their
rollback compatibility is unverified.** Choosing a 1.5.3 management CLI does not establish
that compatibility. App rollback does not guarantee an engine rollback.

## Independent PM2 updates (1.5.3)

Use the installed 1.5.3 Settings → PM2, or its bundled CLI:

```bash
node "$HOME/.control-server/current/bin/control-server.mjs" pm2 check \
  --home "$HOME/.control-server"
node "$HOME/.control-server/current/bin/control-server.mjs" pm2 update \
  --home "$HOME/.control-server"
```

Opening settings checks the latest engine once; installation/cutover requires an explicit
update action. The app and engine pointers are separate. See the
[engine specification](../SPEC/pm2-engine.md), [HTTP API](../../API.md#pm2-엔진-관리-153)
and [Recovery](recovery.md) for state preservation, job results and interrupted updates.
The public 1.5.2 CLI does not expose these commands.

## Backups and preview channel

Before important updates, keep a protected copy of config/runtime. Never start a copied
PM2_HOME while the original daemon is alive. `next` is a preview channel, not a guarantee
that an unpublished candidate is available; at the registry check above it also selects
1.5.2. Use the [release process](releasing.md) to publish and verify a candidate before
using npm to retrieve it.
