# dan-reimpl

This is a skeleton reimplementation matching the structure discovered in the binaries.
It is intentionally incomplete and does not execute network flows without explicit, authorized integration.

## Layout
- `cmd/dan` CLI register tool
- `cmd/dan-token-refresh` token refresh tool
- `cmd/dan-web` web UI/API
- `internal/danapp` core app and config
- `internal/sentinel` placeholder for sentinel solver

## Notes
- Replace stubbed interfaces in `internal/danapp` for real implementations.
- Avoid embedding secrets in source code.

