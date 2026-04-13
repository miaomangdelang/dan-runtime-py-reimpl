# Registration Flow (Inferred)

This describes the high-level flow observed via symbol names, log strings, and configuration. It is intended as a target for reimplementation and test planning.

## Inputs
- Config from `config.json`
- Mail domain list from `config/web_config.json` or CLI `--domains`
- Proxy options (`--proxy`, `--no-proxy`, `--use-env-proxy`)

## Flow Outline
1. Initialize `App` with config and HTTP session.
2. Create mailbox (Mail API).
3. Visit homepage to establish cookies and CSRF.
4. Begin auth flow (`/oauth/authorize` and related redirects).
5. Create account (`/api/accounts/create_account`).
6. Send OTP (`/api/accounts/email-otp/send`).
7. Poll mailbox and extract verification code.
8. Validate OTP (`/api/accounts/email-otp/validate`).
9. Follow auth callback to obtain session tokens.
10. Complete OAuth flow to get Codex token (if enabled).
11. Save token JSON to `token_json_dir`.
12. Upload token JSON to CPA endpoint (unless disabled).
13. Append summary to output file.

## Error and Retry Patterns (from strings)
- Retry on HTTP status 403 for specific steps (password/verify, OTP validate).
- Replay logic on request failures.
- Whole-flow restart logic after repeated failures.
- Optional OAuth skip or non-fatal OAuth failure (`--no-oauth`, `--oauth-not-required`).

## Token Refresh Flow
1. Enumerate token JSON files in directory.
2. For each file, request refreshed tokens via auth/session endpoint.
3. Replace or update stored JSON.

## Binary-calibrated endpoint details
- `GET https://chatgpt.com/api/auth/csrf` -> `csrfToken`
- `POST https://chatgpt.com/api/auth/signin/openai?` (form)
  - `callbackUrl`, `csrfToken`, `json`, `prompt`, `ext-oai-did`, `auth_session_logging_id`, `screen_hint`, `login_hint`
- `POST /api/accounts/user/register` (JSON)
  - `username`, `password`, optional `openai-sentinel-token`
- `GET /api/accounts/email-otp/send`
- `POST /api/accounts/email-otp/validate` (JSON)
  - `code`
- `POST /api/accounts/create_account` (JSON)
  - `name`, `birthdate`, optional `openai-sentinel-token`
- OAuth follow-up:
  - `GET /oauth/authorize`
  - `POST /api/accounts/authorize/continue`
  - `POST /api/accounts/password/verify`
  - conditional `POST /api/accounts/email-otp/validate`
  - `POST /api/accounts/workspace/select`
  - `POST /api/accounts/organization/select`
  - `POST /oauth/token`
