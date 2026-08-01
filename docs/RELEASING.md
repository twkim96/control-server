# Release process

## Preconditions

- clean Git worktree on the intended release commit
- version equality across root, frontend and PM2 package metadata/lockfiles
- backend, frontend, CLI and package gates passing on arm64 and Intel macOS CI
- package archive inspected for private config, runtime, logs, personal paths and secrets
- npm scope ownership and trusted publishing configured for this exact repository

## Local release candidate

```bash
.venv/bin/pytest -q
npm test
npm run typecheck --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
npm run package:inspect
git diff --check
```

Test the generated tarball in a short temporary `CONTROL_SERVER_HOME` with a unique
LaunchAgent label and port. Validate install, login, PM2 initialization, doctor, status,
update rollback, uninstall data preservation and purge cleanup.

## Publish

Publish the first candidate under `next`, not `latest`:

```bash
npm publish --access public --tag next
```

After clean-machine validation and CI approval, promote the exact version:

```bash
npm dist-tag add @twkim96/control-server@1.5.2 latest
```

Create the matching Git tag and GitHub Release only from the verified commit. Attach
the package integrity and the arm64/x64 CI results to the release notes.

The first package publication requires an authenticated npm CLI because the package
does not exist yet. After that bootstrap, configure npm Trusted Publishing for this
exact GitHub repository and add a reviewed OIDC release workflow; enable provenance in
that workflow rather than forcing it in `publishConfig` before the trust relationship
exists. Do not store a long-lived npm token in the repository.
