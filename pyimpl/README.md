# Python Reimplementation (dan)

This directory contains a Python-based reimplementation derived from the reverse engineering notes and symbol inventory.
It now includes:
- a stateful `dan-web` Python server with the inferred REST surface
- a batch/manager layer mirroring the `dan/internal/danweb.(*Manager)` shape
- an injectable registration runner for future real implementations
- an offline `--mock-register` mode for local UI / pipeline verification
- a best-effort live registration / token refresh path driven by static reverse-engineering evidence

## Layout
- `cmd/dan.py`: CLI registration entrypoint
- `cmd/dan_token_refresh.py`: token refresh entrypoint
- `cmd/dan_web.py`: web UI/API entrypoint
- `danapp/app.py`: core app + injectable registration runner + mock runner
- `danapp/web.py`: manager/server implementation for `dan-web`
- `danapp/register_flow.py`: best-effort live signup / OAuth workflow
- `danapp/http.py`: stdlib HTTP client wrapper
- `danapp/`: remaining config / oauth / token refresh helpers

## Notes
- Do not embed secrets in source. Use local config files or environment variables.
- Real signup / OAuth / mailbox polling behavior is now aligned much closer to the binary evidence. Sentinel solving now supports `env -> browser helper` fallback and flow-specific env keys, while still depending on local authorized browser / token access.
- Use `--mock-register` when you want to exercise the Python web flow without touching external services.
- Use `--allow-network` to switch from mock flow to the binary-calibrated live runner / token refresher.

## Example
```bash
python3 pyimpl/cmd/dan_web.py --mock-register
python3 pyimpl/cmd/dan.py --mock-register -n 3
python3 pyimpl/cmd/dan.py --allow-network --web-config config/web_config.json -n 1
python3 pyimpl/cmd/dan_token_refresh.py --allow-network
```
