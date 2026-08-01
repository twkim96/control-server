# AI service registration contract

When a user asks to add a web server to Control Server, the default meaning is to
register a long-running service in **Servers**. Do not create a service-specific Control
Server tab, route or iframe, and do not put a long-running process in the one-shot
**Services** page.

| Target | Location | API |
| --- | --- | --- |
| HTTP/TCP server, worker, gateway or tunnel that stays alive | Servers | `POST /api/config/services` |
| update, deploy, sync, scan or build command that exits | Services action group | `POST /api/config/actions` |
| server plus deployment/update command | Split across both | Both APIs |

Language is not the classifier. Python, Go, Node, Bun and shell wrappers all belong in
Servers when the process must remain alive.

## API-first workflow

1. Inspect the real cwd, argv, port, user URL, health URL and shutdown behavior.
2. Use `GET /api/config/services` to detect duplicate IDs and ports.
3. Obtain `csrf_token` from the authenticated `GET /api/auth/me` response.
4. Create with `POST /api/config/services`; replace with the complete payload through
   `PUT /api/config/services/<id>`.
5. A 2xx mutation has already saved, reconciled and reloaded the registry. Do not call
   `/api/config/reload` again.
6. Verify config and runtime through `GET /api/config/services` and
   `GET /api/services/<id>`.
7. Registration does not authorize starting the service. Start only when requested.

Direct YAML editing remains a fallback. Only that path requires `POST /api/config/reload`.
Do not bypass a `409 config_reload_blocked` response or restart the controller to hide
an online orphan.

## Canonical actions

| ID | type | label |
| --- | --- | --- |
| `start` | `process_start` | `시작` |
| `stop` | `process_stop` | `중지` |
| `restart` | `process_restart` | `재시작` |
| `health` | `health_check` | `상태 확인` |
| `logs` | `show_logs` | `로그` |
| `open` | `open_url` | `URL 열기` |

Use an argv array rather than a shell string. Keep secrets out of documentation and
commits. Default manual services use `autostart: false`, `unmanaged_policy: status_only`
and the PM2 stop contract `SIGINT` then `SIGTERM` then `SIGKILL`.
