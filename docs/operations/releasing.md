# Release process

Current release work and pending acceptance are maintained in [TODO](../TODO.md).
Historical local and managed results stay in [CHANGELOG](../../CHANGELOG.md).
A prior successful CI run does not verify a different candidate or dirty checkout.

## Preconditions

- clean worktree on the intended release commit; preserve unrelated local changes
- app versions agree across root/frontend metadata and lockfiles
- bundled PM2 remains the frozen fallback; independent engines are not app versions
- backend/frontend/CLI/package gates pass for this commit on arm64 and x64 CI
- package inspected for private config, runtime, logs, personal paths and credentials
- the intended npm channel and exact package integrity/commit are identified

## Local release candidate

```bash
.venv/bin/pytest -q
npm test
npm run typecheck --prefix frontend
npm run lint --prefix frontend
npm run package:inspect
git diff --check
```

`package:inspect` includes the production build. Test the generated tarball in a short
isolated `CONTROL_SERVER_HOME` with a unique LaunchAgent label and port. Validate
install/login/PM2 initialization/doctor/status/update/rollback/uninstall preservation
and purge cleanup. Verify explicit PM2 check/update, final background job, actual daemon
version, stopped-state preservation, prehealthy health targets and engine recovery retry.

Record catchable app activation/metadata rollback separately from engine-journal
recovery; forced app pointer/metadata process death is not journaled. A fixture that only
lowers the version number is not an older app compatibility test. Intel physical-device
acceptance is separate from x64 CI.

## Publish

The npm package already exists. Confirm current tags and package integrity before
publishing an approved candidate under `next`:

```bash
npm view @twkim96/control-server dist-tags --json
npm publish --access public --tag next
```

After candidate acceptance and approval, promote the exact verified version. For the
current 1.5.3 candidate, this command applies only after that version is published:

```bash
npm dist-tag add @twkim96/control-server@1.5.3 latest
```

Create the matching Git tag and GitHub Release from the verified commit and attach
package integrity and arm64/x64 CI evidence. Do not retrofit the current release commit
as a missing historical release tag.

`.github/workflows/ci.yml` verifies packages; it is not a publishing workflow. Automated
OIDC publication needs npm Trusted Publishing configured for this exact repository
and a reviewed release workflow with provenance. Until then use the authorized npm
CLI publication process. Do not store long-lived npm tokens in the repository.
