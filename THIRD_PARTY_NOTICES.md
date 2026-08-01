# Third-party notices

Control Server is distributed under the MIT License. Its runtime also installs or
embeds the following third-party components under their own licenses.

## PM2 7.0.3

- Project: <https://github.com/Unitech/pm2>
- Package: <https://www.npmjs.com/package/pm2/v/7.0.3>
- License: GNU Affero General Public License v3.0 (`AGPL-3.0`)

PM2 is installed as an unmodified, isolated runtime dependency. Control Server
communicates with its separate CLI and daemon and does not vendor PM2 source or
`node_modules` in the published application payload. The installed PM2 package
contains the complete AGPL-3.0 license and corresponding upstream source metadata.

## Pretendard 1.3.9

- Project: <https://github.com/orioncactus/pretendard>
- License: SIL Open Font License 1.1 (`OFL-1.1`)
- Reserved Font Name: Pretendard

The production frontend embeds unmodified Pretendard webfont subsets. The complete
font license is included at `LICENSES/PRETENDARD-OFL-1.1.txt`.

## JavaScript and Python dependencies

Remaining JavaScript and Python dependencies retain the licenses and notices shipped
with their respective npm and Python packages. Exact install versions are recorded in
the npm shrinkwrap/lockfiles and `backend/requirements-runtime.lock`.
