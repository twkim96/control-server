# Changelog

All notable changes are documented here. The project follows semantic versioning from
the 1.5.0 public distribution release onward.

## [Unreleased]

### Fixed

- enforce durable intent before process mutations, migrate legacy stopped state before
  autostart, and apply the same intent/conflict checks to automatic startup
- reject stale forced snapshots after concurrent refresh failures, conservatively block
  shebang launchers, and verify daemon lifetime using OS process creation time
- preserve validated external process identity through termination, exclude other managed
  PIDs, and require process exit rather than listener closure for success
- provide signal-free automatic recovery cancellation and observation-only automatic-start mode

- persist service running/stopped intent and recover missing managed services after a
  confirmed dedicated PM2 daemon replacement, with dependency order, bounded retries,
  manual-stop precedence and duplicate-process checks
- distinguish verified healthy external instances, unhealthy external instances and
  unverified responses; show recovery blockers and reject misleading managed actions
- pin bundled PM2 to 7.1.1; installed independent engines still require an explicit update

### Verification boundary

- isolated daemon-signal recovery and focused backend/frontend regressions are verified
  separately from production deployment; live service restart and browser acceptance remain pending
- automatic recovery blocks portless workers and ambiguous exec wrappers for manual verification

## [1.6.0] - Unreleased

### Added

- persistent, bounded service observations and explicit action history, with PM2 restart
  counts distinguished from crash counts and observed times distinguished from exact exit times
- per-service PM2 restart policies and optional memory-triggered restart
- opt-in unhealthy recovery with fresh identity/health checks, durable cooldown/hourly budget,
  and health-verified recovery completion
- asynchronous service group actions and dependency ordering; stopped services stay stopped
  during group restart, and intervening manual actions cancel queued intent
- opt-in live PM2 7.1+ log rotation with fd handoff checks, gzip retention, failure-preserved
  pending logs, and explicit SSE gaps when a slow reader misses retained generations
- lightweight display-only filtering of successful configured health access logs, with a
  show toggle; errors and original stored logs are preserved

### Fixed

- supported macOS installations without `/usr/bin/lockf` use an inherited-descriptor
  Python flock helper, preserving management and PM2 command exclusion

### Changed

- runtime snapshots pass only required process fields to Python, omitting inherited environment
- service editing preserves existing log retention and HTTP/TCP health settings
- disabled PM2 service logging routes output to `/dev/null`; changing the output target
  or restart policy requires the service to be stopped first

Local automated and isolated PM2/browser checks are separate from managed deployment and
long-running operational acceptance. npm publication and release tags are not included in
local managed deployment.

### Managed acceptance (2026-10-22)

- [arm64 and x64 CI](https://github.com/twkim96/control-server/actions/runs/39786593771)
  passed for `f8a7a00`; the preceding feature commit is `b2c3176`
- managed app updated from 1.5.3 to 1.6.0 with only the launchd controller restarted
- all eight running PM2 services kept the same PIDs and normal responses; four stopped
  entries remained stopped; the selected daemon and engine stayed at PM2 7.1.0
- config/environment bytes, deployed application files and served frontend assets verified;
  history API and installed CLI doctor passed
- the existing `cos_web` manifest warning remains; no unrelated service definition was changed

This is deployment acceptance with the new automation/rotation policies left disabled.
Service-specific opt-in recovery/rotation and long-running browser acceptance remain pending.

## [1.5.3] - Unreleased

Implementation and local macOS acceptance completed on 2026-09-06. The managed app
was updated to 1.5.3 and its PM2 engine from 7.0.3 to 7.0.4, with 10 healthy running
services and one stopped service preserved. Public npm publication and release CI are pending.

### Fixed

- actions with logging disabled discard output instead of blocking on an unread pipe
- action history retention preserves running and cancellation-in-progress processes
- concurrent action log retention preserves active captures and bounds completed logs
- all YAML writes serialize saving, reconciliation, rollback and response snapshots
- session defaults explicitly use `server_control_session`, HttpOnly, SameSite=Lax and
  a 90-day lifetime; existing users must log in once again
- concurrent app management operations cannot let an older failed update undo a later
  successful update through stale rollback state
- app rollback checks dependencies before switching the active release pointer
- app installation metadata uses atomic writes, and catchable metadata failures after
  activation participate in rollback
- uninstall stops controller polling before PM2 shutdown and preserves installation
  files if shutdown fails; ordinary PM2 calls wait for other polls

### Added

- Settings PM2 status, one automatic latest check on opening, manual check refresh,
  explicit background update and recovery retry
- `control-server pm2 check/update [--home PATH]` and authenticated system PM2 APIs
- independent engine releases/current pointer while retaining the existing PM2_HOME;
  bundled PM2 7.0.3 remains the frozen initial fallback
- engine cutover recovery journal, stopped-state preservation, actual daemon version
  verification and health verification for services healthy before cutover
- updater failures before the first status write remain visible in settings

### Limits

- Control Server remains under launchd during engine updates; managed services can
  briefly restart
- pre-1.5.3 app rollback compatibility with the independent engine pointer is unverified
- forced process death between app pointer and metadata writes is not journaled; the
  engine cutover journal is a separate mechanism

### Recorded local acceptance (2026-09-06)

- backend aggregate: 335 passed, 4 optional integration tests skipped; the subsequent
  updater early-exit fix passed its affected API file (15 tests including the new case)
- CLI: 36 passed, including concurrent update, metadata rollback and failed-uninstall preservation
- frontend typecheck/lint/production build and package allowlist/secret inspection
- isolated real 7.0.3 → 7.0.4: running/stopped intent, health and repeated-update PID preservation
- packed install/login/update/doctor/fixture rollback/uninstall/purge, including cleanup
- managed app/engine update: running and stopped intent, prehealthy targets and private
  config/password bytes preserved; no remaining recovery journal or lock owner recorded
- fixture UI update/progress/completion and installed Settings re-login/version/job display

Failures were injected into isolated fixtures, not operational services. The packed
rollback fixture lowered only the version of the 1.5.3 code. It does not establish
actual older-app compatibility. These are recorded prior results, not checks rerun by
later documentation changes.

## [1.5.2] - 2026-08-01

### Changed

- new installations and appearance reset now use the verified light palette from the
  production Control Server
- the former dark default is seeded once as the `다크모드` appearance preset without
  replacing existing user presets

## [1.5.1] - 2026-08-01

### Changed

- general installation, management, recovery, and uninstall commands now use the npm
  `latest` tag
- the `next` tag is documented as the preview channel for release candidates
- fixed-version installation examples now point to `1.5.1`

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
