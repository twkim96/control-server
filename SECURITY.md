# Security policy

## Supported versions

Security fixes are provided for the latest stable Control Server release. Release
candidates are for validation and should not replace a working installation without
a recovery point.

## Intended deployment

Control Server is designed for localhost, a trusted LAN, or a private overlay network
such as Tailscale. It is not an internet-facing multi-user control plane and does not
provide TLS termination, rate limiting, account recovery, or tenant isolation.

- Keep `controller.host` on `127.0.0.1` unless private-network access is intentional.
- Do not expose port 9000 directly to the public internet.
- Keep the generated `config/run.env`, config, runtime, logs, and installed LaunchAgent
  private to the local user.
- Never attach raw PM2 `jlist` output to an issue; it may contain inherited environment
  variables and credentials.

## Reporting a vulnerability

Do not include passwords, tokens, private paths, service inventories, logs, PM2 dumps,
or a real `config.yml` in a public issue. Prefer GitHub's private vulnerability report
for this repository. If private reporting is unavailable, contact the maintainer first
and share only a minimal redacted reproduction.

Useful safe evidence includes the Control Server version, macOS/CPU architecture,
Python and Node major versions, the failing command, HTTP status/error code, and a
synthetic config reproducer.
