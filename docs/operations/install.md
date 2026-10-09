# Installation

## Requirements

- macOS 13 or later
- Node.js 22 or later with npm
- Python 3.10 or later

Recorded Apple Silicon managed acceptance exists. The published 1.5.2 commit passed
arm64/x64 CI; this does not verify the uncommitted 1.5.3 candidate. Physical Intel
acceptance and current release gates are tracked in [TODO](../TODO.md).

With Homebrew, install the prerequisites and verify the resolved versions:

```bash
brew install node python

node --version
npm --version
python3 --version
```

## Managed npm installation

```bash
npx --yes @twkim96/control-server@latest install
```

General installation uses the `latest` dist-tag. The `next` tag is reserved for preview
releases before they are promoted to `latest`. Use
`npx --yes @twkim96/control-server@1.5.2 install` when an exact reproducible version is
preferred.

Use `install --port 9100` when the default port 9000 is already occupied. Port changes
after installation are an explicit config/LaunchAgent operation and are not performed
implicitly by `update`.

The installer verifies Node and Python, stages an immutable application release,
creates a Python virtual environment from the release runtime lock, installs pinned PM2
under the release, creates a
private password/config, installs a user LaunchAgent, and waits for `/api/meta`.

If no `CONTROL_PASSWORD` environment variable is supplied, a random initial password
is printed once. It is stored only in `~/.control-server/config/run.env` with mode 0600.

The default layout is:

```text
~/.control-server/
├── releases/          immutable application releases
├── current            symlink to the active release
├── config/            config.yml and private run.env
├── runtime/           checkpoint, appearance and isolated PM2_HOME
├── logs/              controller, service and action logs
└── install.json       active release metadata
```

Use a different short root only when necessary:

```bash
CONTROL_SERVER_HOME="$HOME/.cs" \
  npx --yes @twkim96/control-server@latest install
```

PM2 sockets have a macOS path-length limit. The installer rejects a home whose
`runtime/pm2/interactor.sock` path is too long.

For shorter management commands, install the CLI globally. Global npm installation
does not start a daemon by itself:

```bash
npm install -g @twkim96/control-server@latest
control-server status
```

## Existing checkout

The installer never replaces an existing `com.twkim.server-control` LaunchAgent that
points outside the managed home. Inspect the proposed one-time move first:

```bash
npx --yes @twkim96/control-server@latest migrate --plan \
  --source /path/to/control-server
```

The current CLI provides a read-only migration plan. Moving a live PM2_HOME
requires a separately approved cutover, service inventory, backup, downtime, health
verification, and rollback.

## Source/development installation

Follow [Development setup](../development/setup.md) and
[Source runtime operations](source-runtime.md).
