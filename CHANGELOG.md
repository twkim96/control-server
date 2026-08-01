# Changelog

All notable changes are documented here. The project follows semantic versioning from
the 1.5.0 public distribution release onward.

## [1.5.0] - 2026-08-01

### Added

- npm `control-server` installer/manager CLI for macOS
- versioned managed releases with external config, runtime, and logs
- `install`, `doctor`, `status`, `open`, `update`, `rollback`, `uninstall`, and read-only
  `migrate --plan` commands
- package content allowlist, MIT license, third-party notices, and release CI

### Changed

- backend and launchd accept explicit managed config/log/runtime/frontend paths while
  retaining checkout-compatible defaults
- PM2 remains pinned and isolated under a short dedicated `PM2_HOME`
- runtime and development Python requirements are separated
- public-beta commands use the npm `next` tag and document prerequisite installation
- generated-password output identifies the private `run.env` recovery location

### Removed

- project-specific credential passthrough from the generic Control Server LaunchAgent
- obsolete DevSpace tunnel launchers from the core distribution

## [1.4.3] - 2026-07-31

- Process-tree CPU history now tracks `(pid, create_time)` identities.
- Resource intervals use a monotonic clock and expose partial child measurements.
- The UI identifies RAM as a parent/child RSS sum and uses Pretendard for interface text.
- AI-facing service registration guidance documents Servers versus one-shot Services.
