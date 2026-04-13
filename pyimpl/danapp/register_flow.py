import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote, urlencode, urlparse

from .config import WebConfig, load_web_config
from .http import HTTPClient, RequestOptions
from .mailbox import CloudmailMailboxClient, Mailbox, MailboxClient, NullMailboxClient
from .oauth import OAuthTokens
from .sentinel import BrowserSentinelSolver, CompositeSentinelSolver, EnvironmentSentinelSolver, SentinelSolver, build_sentinel_payload
from .util import (
    decode_json_bytes,
    extract_code_from_url,
    first_present,
    generate_pkce,
    make_trace_headers,
    map_string,
    random_birthdate,
    random_delay_seconds,
    random_name,
    random_password,
)

CHATGPT_BASE_URL = "https://chatgpt.com"
AUTH_BASE_URL = "https://auth.openai.com"


class RegistrationError(RuntimeError):
    pass


class WholeFlowRestartError(RegistrationError):
    pass


class RetryableOAuthError(RegistrationError):
    pass


class IncompleteRegistrationError(RegistrationError):
    pass


@dataclass
class StepResult:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    data: Dict[str, Any] = field(default_factory=dict)
    raw: bytes = b""
    final_url: str = ""

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="ignore")


@dataclass
class OAuthState:
    url: str
    text: str
    has_code: bool = False
    requires_password: bool = False
    requires_otp: bool = False
    requires_about_you: bool = False
    requires_workspace_or_org: bool = False
    requires_consent: bool = False
    is_callback: bool = False
    looks_complete: bool = False


@dataclass
class RegisterSession:
    app: "App"
    runner: "OpenAIRegistrationRunner"
    http: HTTPClient
    mailbox_client: MailboxClient
    sentinel: SentinelSolver
    web_config: Optional[WebConfig] = None
    mailbox: Optional[Mailbox] = None
    email: str = ""
    password: str = ""
    profile_name: str = ""
    birthdate: str = ""
    authorize_url: str = ""
    callback_url: str = ""
    csrf_token: str = ""
    account_id: str = ""
    session_data: Dict[str, Any] = field(default_factory=dict)
    pkce_verifier: str = ""
    pkce_challenge: str = ""
    oauth_state: str = ""
    ext_oai_did: str = ""
    auth_session_logging_id: str = ""
    oauth_tried_codes: Set[str] = field(default_factory=set)
    oauth_seen_message_ids: Set[str] = field(default_factory=set)
    trace: List[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        self.trace.append(message)
        if getattr(self.app, "logger", None):
            self.app.logger.info(message)

    @property
    def chatgpt_base(self) -> str:
        return getattr(self.app, "chatgpt_base_url", CHATGPT_BASE_URL).rstrip("/")

    @property
    def oauth_base(self) -> str:
        return (self.app.config.oauth_issuer or AUTH_BASE_URL).rstrip("/")

    def update_task(self, task: str) -> None:
        self.log(f"[Task] {task}")

    def print(self, message: str) -> None:
        self.log(message)

    def page_headers(self, referer: str = "") -> Dict[str, str]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
            **make_trace_headers(),
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def json_headers(self, referer: str = "") -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.chatgpt_base,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
            **make_trace_headers(),
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def form_headers(self, referer: str = "") -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": self.chatgpt_base,
            **make_trace_headers(),
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def oauth_json_headers(self, referer: str = "") -> Dict[str, str]:
        headers = self.json_headers(referer)
        headers["Accept"] = "application/json"
        return headers

    def _sleep(self, min_delay: float = 0.2, max_delay: float = 0.8) -> None:
        time.sleep(random_delay_seconds(min_delay, max_delay))

    def _absolute(self, maybe_url: str, base: str = "") -> str:
        maybe_url = (maybe_url or "").strip()
        if not maybe_url:
            return ""
        if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
            return maybe_url
        root = (base or self.chatgpt_base).rstrip("/")
        if maybe_url.startswith("/"):
            return root + maybe_url
        return root + "/" + maybe_url

    def _final_url(self, headers: Dict[str, str], fallback: str) -> str:
        return headers.get("X-Final-URL") or headers.get("Location") or fallback

    def _pick_next_url(self, result: StepResult, fallback: str = "") -> str:
        for container in (result.data, result.data.get("data", {}), result.data.get("payload", {})):
            if not isinstance(container, dict):
                continue
            value = map_string(container, "continue_url", "redirect_url", "callback_url", "url", "final_url")
            if value:
                return self._absolute(value)
        location = result.headers.get("Location", "")
        if location:
            return self._absolute(location)
        return self._absolute(result.final_url or fallback)

    def _request(self, method: str, url: str, headers: Dict[str, str], body: bytes = b"") -> StepResult:
        status, out_headers, raw = self.http.request(
            RequestOptions(method=method, url=url, headers=headers, body=body)
        )
        return StepResult(
            status=status,
            headers=out_headers,
            data=decode_json_bytes(raw),
            raw=raw,
            final_url=self._final_url(out_headers, url),
        )

    def _json_post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> StepResult:
        return self._request("POST", url, headers, json.dumps(payload).encode("utf-8"))

    def _form_post(self, url: str, headers: Dict[str, str], payload: Dict[str, str]) -> StepResult:
        return self._request("POST", url, headers, urlencode(payload).encode("utf-8"))

    def _sentinel_token(self, kind: str, url: str = "") -> str:
        token = self.sentinel.solve(
            build_sentinel_payload(
                kind,
                url or self.authorize_url or self.chatgpt_base,
                data={
                    "proxy": self.app.proxy,
                    "user_agent": getattr(self.http, "user_agent", ""),
                },
            )
        )
        return token.strip()

    def _state_text(self, current_url: str, payload: Optional[Dict[str, Any]] = None, raw: bytes = b"") -> str:
        parts = [current_url or ""]
        if payload:
            try:
                parts.append(json.dumps(payload, ensure_ascii=False))
            except TypeError:
                parts.append(str(payload))
        if raw:
            parts.append(raw.decode("utf-8", errors="ignore"))
        return "\n".join(parts).lower()

    def _analyze_oauth_state(self, current_url: str, payload: Optional[Dict[str, Any]] = None, raw: bytes = b"") -> OAuthState:
        text = self._state_text(current_url, payload, raw)
        parsed = urlparse(current_url or "")
        path = parsed.path.lower()
        host = (parsed.hostname or "").lower()
        has_code = bool(extract_code_from_url(current_url))
        requires_otp = any(
            marker in text
            for marker in (
                "email_verification_mode",
                "email_otp_verification",
                "email-verification",
                "email-otp",
            )
        )
        requires_about = "about-you" in text or '"about"' in text or " /about-you" in text
        requires_workspace = any(
            marker in text for marker in ("workspace", "organization", "workspaces", "orgs", "projects")
        )
        requires_consent = "consent" in text or "sign-in-with-chatgpt" in text
        requires_password = (
            "/log-in" in path
            or "/log-in/password" in text
            or "password_verify" in text
        ) and not requires_otp
        is_callback = "callback" in path or "callback" in text or host == "chatgpt.com"
        looks_complete = has_code or (is_callback and not requires_otp and not requires_about)
        return OAuthState(
            url=current_url,
            text=text,
            has_code=has_code,
            requires_password=requires_password,
            requires_otp=requires_otp,
            requires_about_you=requires_about,
            requires_workspace_or_org=requires_workspace,
            requires_consent=requires_consent,
            is_callback=is_callback,
            looks_complete=looks_complete,
        )

    def visit_homepage(self) -> str:
        url = self.chatgpt_base + "/"
        result = self._request("GET", url, self.page_headers())
        if result.status == 403:
            raise WholeFlowRestartError("homepage returned 403")
        if result.status >= 400:
            raise RegistrationError(f"homepage failed: {result.status} {result.text[:200]}")
        return result.final_url

    def get_csrf(self) -> str:
        result = self._request(
            "GET",
            self.chatgpt_base + "/api/auth/csrf",
            {
                "Accept": "application/json",
                "Referer": self.chatgpt_base + "/",
                **make_trace_headers(),
            },
        )
        if result.status >= 400:
            raise RegistrationError(f"csrf request failed: {result.status} {result.text[:200]}")
        token = map_string(result.data, "csrfToken")
        if not token:
            raise RegistrationError("csrf token missing")
        self.csrf_token = token
        return token

    def signin(self) -> str:
        self.ext_oai_did = self.ext_oai_did or uuid.uuid4().hex
        self.auth_session_logging_id = self.auth_session_logging_id or uuid.uuid4().hex
        payload = {
            "callbackUrl": self.chatgpt_base + "/",
            "csrfToken": self.csrf_token,
            "json": "true",
            "prompt": "login",
            "ext-oai-did": self.ext_oai_did,
            "auth_session_logging_id": self.auth_session_logging_id,
            "screen_hint": "signup",
            "login_hint": self.email,
        }
        result = self._form_post(
            self.chatgpt_base + "/api/auth/signin/openai?",
            self.form_headers(self.chatgpt_base + "/"),
            payload,
        )
        if result.status >= 400:
            raise RegistrationError(f"signin failed: {result.status} {result.text[:200]}")
        self.authorize_url = map_string(result.data, "url") or self._pick_next_url(result)
        if not self.authorize_url:
            raise RegistrationError("authorize URL missing")
        return self.authorize_url

    def authorize(self) -> str:
        target = self.authorize_url or self.runner.build_oauth_authorize_url(self)
        result = self._request("GET", target, self.page_headers(self.chatgpt_base + "/"))
        if result.status == 403:
            raise WholeFlowRestartError("authorize returned 403")
        if result.status >= 400:
            raise RegistrationError(f"authorize failed: {result.status} {result.text[:200]}")
        self.authorize_url = result.final_url
        return result.final_url

    def register(self) -> StepResult:
        payload = {
            "username": self.email,
            "password": self.password,
        }
        referer = self.chatgpt_base + "/create-account/password"
        token = self._sentinel_token("register.submit_password", referer)
        if token:
            payload["openai-sentinel-token"] = token
        result = self._json_post(
            self.chatgpt_base + "/api/accounts/user/register",
            self.json_headers(referer),
            payload,
        )
        if self.runner.should_retry_sentinel_request(result):
            self.print(
                f"[Sentinel] register.submit_password returned {result.status}, retrying with refreshed HTTP token"
            )
            token = self._sentinel_token("register.submit_password.retry", referer)
            if token:
                payload["openai-sentinel-token"] = token
            result = self._json_post(
                self.chatgpt_base + "/api/accounts/user/register",
                self.json_headers(referer),
                payload,
            )
        return result

    def create_account(self) -> StepResult:
        payload = {
            "name": self.profile_name,
            "birthdate": self.birthdate,
        }
        referer = self.chatgpt_base + "/about-you"
        token = self._sentinel_token("oauth_create_account", referer)
        if token:
            payload["openai-sentinel-token"] = token
        result = self._json_post(
            self.chatgpt_base + "/api/accounts/create_account",
            self.json_headers(referer),
            payload,
        )
        self.account_id = (
            map_string(result.data, "account_id", "chatgpt_account_id", "id")
            or map_string(result.data.get("data", {}), "account_id", "chatgpt_account_id", "id")
            or self.account_id
        )
        next_url = self._pick_next_url(result)
        if next_url:
            self.callback_url = next_url
        return result

    def send_otp(self) -> StepResult:
        return self._request(
            "GET",
            self.chatgpt_base + "/api/accounts/email-otp/send",
            self.page_headers(self.chatgpt_base + "/create-account/password"),
        )

    def validate_otp(self, code: str) -> StepResult:
        result = self._json_post(
            self.chatgpt_base + "/api/accounts/email-otp/validate",
            self.json_headers(self.chatgpt_base + "/email-verification"),
            {"code": code},
        )
        next_url = self._pick_next_url(result)
        if next_url:
            self.callback_url = next_url
        return result

    def snapshot_mailbox_message_ids(self) -> Set[str]:
        if not self.mailbox:
            return set()
        return self.mailbox_client.snapshot_message_ids(self.mailbox)

    def wait_for_verification_email(
        self,
        timeout_sec: int = 180,
        *,
        after_ids: Optional[Set[str]] = None,
        disallow_codes: Optional[Set[str]] = None,
        oauth_mode: bool = False,
    ) -> str:
        if not self.mailbox:
            raise RegistrationError("mailbox missing")
        provider = "mailapi"
        if oauth_mode:
            self.print(f"[OAuth] OTP waiting... (0/{timeout_sec}s)")
        else:
            self.print(f"[OTP] Waiting for verification email (timeout={timeout_sec}s, provider={provider})...")
        return self.mailbox_client.fetch_otp(
            self.mailbox,
            timeout_sec,
            after_ids=set(after_ids or set()),
            disallow_codes=set(disallow_codes or set()),
            expected_recipient=self.email,
        )

    def callback_and_get_session(self) -> Dict[str, Any]:
        callback = self.callback_url or self.authorize_url
        if not callback:
            return self.session_data
        result = self._request(
            "GET",
            self._absolute(callback),
            self.page_headers(self.chatgpt_base + "/callback"),
        )
        if result.status >= 400:
            raise RegistrationError(f"[Session] callback request error: {result.status} {result.text[:200]}")
        self.callback_url = result.final_url
        session_result = self._request(
            "GET",
            self.chatgpt_base + "/api/auth/session",
            {
                "Accept": "application/json",
                "Referer": self.callback_url,
                **make_trace_headers(),
            },
        )
        if session_result.status < 400 and session_result.data:
            self.session_data = session_result.data
        else:
            self.print(f"[Session] /api/auth/session failed: {session_result.status} {session_result.text[:160]}")
            self.session_data = {"callback_url": self.callback_url}
        account_id = map_string(self.session_data, "chatgpt_account_id") or map_string(
            self.session_data.get("user", {}), "id", "chatgpt_account_id"
        )
        if account_id:
            self.account_id = account_id
        session_token = self.http.cookie_value("__Secure-next-auth.session-token") or self.http.cookie_value(
            "__Secure-authjs.session-token"
        )
        if session_token and "sessionToken" not in self.session_data:
            self.session_data["sessionToken"] = session_token
        return self.session_data

    def run_register(self) -> None:
        self.update_task("register.homepage")
        self.visit_homepage()
        self._sleep(0.3, 0.8)
        self.update_task("csrf")
        self.get_csrf()
        self._sleep(0.2, 0.5)
        self.update_task("signin")
        self.signin()
        self._sleep(0.3, 0.8)
        self.update_task("authorize")
        current_url = self.authorize()
        path = (urlparse(current_url).path or "").lower()
        host = (urlparse(current_url).hostname or "").lower()
        self._sleep(0.3, 0.8)

        if "create-account/password" in path:
            self.print("New registration flow")
        elif "email-verification" in path or "email-otp" in path:
            self.print("Jumped directly to OTP verification")
            self._register_wait_validate_and_create_account()
            return
        elif "about-you" in path:
            self.print("Jumped directly to profile setup")
            self._create_account_and_callback()
            return
        elif "callback" in path or host == "chatgpt.com":
            self.print("Account already completed")
            return
        else:
            self.print(f"Unknown redirect: {current_url}")

        self.update_task("submit password")
        register_result = self.register()
        if register_result.status != 200:
            raise RegistrationError(f"register: {register_result.status} {register_result.text[:200]}")
        self._sleep(0.3, 0.8)
        self.update_task("send otp")
        send_result = self.send_otp()
        if send_result.status >= 400:
            raise RegistrationError(f"register.send_otp: {send_result.status} {send_result.text[:200]}")
        self._register_wait_validate_and_create_account()

    def _register_wait_validate_and_create_account(self) -> None:
        self.update_task("wait otp")
        try:
            code = self.wait_for_verification_email(timeout_sec=180)
        except TimeoutError:
            raise WholeFlowRestartError("verification code not received within 180s") from None
        validate_result = self.validate_otp(code)
        if validate_result.status == 403:
            raise WholeFlowRestartError("register.validate_otp returned 403")
        if validate_result.status != 200:
            self.print("[OTP] Validation failed, requesting a new code...")
            self.update_task("resend otp")
            resend_result = self.send_otp()
            if resend_result.status >= 400:
                raise RegistrationError(f"register.send_otp_retry: {resend_result.status} {resend_result.text[:200]}")
            try:
                retry_code = self.wait_for_verification_email(timeout_sec=180, disallow_codes={code})
            except TimeoutError:
                raise WholeFlowRestartError("verification code retry not received within 180s") from None
            validate_result = self.validate_otp(retry_code)
            if validate_result.status == 403:
                raise WholeFlowRestartError("register.validate_otp retry returned 403")
            if validate_result.status != 200:
                raise RegistrationError(
                    f"verification after retry: {validate_result.status} {validate_result.text[:200]}"
                )
        self._create_account_and_callback()

    def _create_account_and_callback(self) -> None:
        self._sleep(0.5, 1.5)
        self.update_task("create account")
        create_result = self.create_account()
        if create_result.status != 200:
            raise RegistrationError(
                f"create account: {create_result.status} {create_result.text[:200]}"
            )
        self._sleep(0.2, 0.5)
        self.update_task("callback")
        self.callback_and_get_session()

    def _tokens_from_session(self) -> OAuthTokens:
        data = self.session_data or {}
        return OAuthTokens.from_dict(
            {
                "access_token": map_string(data, "access_token", "accessToken"),
                "refresh_token": map_string(data, "refresh_token", "refreshToken"),
                "id_token": map_string(data, "id_token", "idToken"),
                "session_token": map_string(data, "session_token", "sessionToken"),
                "token_type": map_string(data, "token_type", "tokenType") or "Bearer",
                "expires_at": first_present(data, ["expires_at", "expiresAt", "expires"], 0),
            }
        )


class OpenAIRegistrationRunner:
    def __init__(self, app: "App", web_config_path: str = "config/web_config.json") -> None:
        self.app = app
        self.web_config_path = web_config_path
        self._web_config: Optional[WebConfig] = None

    @property
    def web_config(self) -> Optional[WebConfig]:
        if self._web_config is None:
            try:
                self._web_config = load_web_config(self.web_config_path)
            except FileNotFoundError:
                self._web_config = None
        return self._web_config

    def _mailbox_client(self, http: HTTPClient) -> MailboxClient:
        cfg = self.web_config
        if not cfg or not cfg.mail_api_url or not cfg.mail_api_key:
            return NullMailboxClient()
        return CloudmailMailboxClient(
            cfg.mail_api_url,
            cfg.mail_api_key,
            http=http,
            logger=self.app.logger.info,
        )

    def _sentinel_solver(self) -> SentinelSolver:
        return CompositeSentinelSolver(
            [
                EnvironmentSentinelSolver(),
                BrowserSentinelSolver(logger=self.app.logger.info),
            ]
        )

    def build_oauth_authorize_url(self, session: RegisterSession) -> str:
        session.pkce_verifier, session.pkce_challenge = generate_pkce()
        session.oauth_state = uuid.uuid4().hex
        params = {
            "client_id": self.app.config.oauth_client_id,
            "redirect_uri": self.app.config.oauth_redirect_uri,
            "response_type": "code",
            "scope": "openid profile email offline_access",
            "code_challenge": session.pkce_challenge,
            "code_challenge_method": "S256",
            "state": session.oauth_state,
            "screen_hint": "signup",
            "login_hint": session.email,
        }
        return f"{session.oauth_base}/oauth/authorize?{urlencode(params)}"

    def should_retry_sentinel_request(self, result: StepResult) -> bool:
        text = result.text.lower()
        if result.status == 403:
            return True
        return any(marker in text for marker in ("sentinel", "turnstile", "proofofwork", "challenge"))

    def is_incomplete_registration_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "about-you",
                "email-verification",
                "authorization code not obtained",
                "oauth create_account",
                "workspace",
                "organization",
                "consent",
            )
        )

    def is_retryable_oauth_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return isinstance(exc, (RetryableOAuthError, WholeFlowRestartError)) or any(
            marker in text
            for marker in (
                "returned 403",
                "invalid_state",
                "please start over",
                "invalid_auth_step",
                "timed out",
                "authorize/continue",
                "password/verify",
            )
        )

    def new_session(
        self,
        *,
        app: "App",
        http: Optional[HTTPClient] = None,
        mailbox_client: Optional[MailboxClient] = None,
        sentinel: Optional[SentinelSolver] = None,
    ) -> RegisterSession:
        http = http or app.create_http_client()
        mailbox_client = mailbox_client or self._mailbox_client(http)
        sentinel = sentinel or self._sentinel_solver()
        return RegisterSession(
            app=app,
            runner=self,
            http=http,
            mailbox_client=mailbox_client,
            sentinel=sentinel,
            web_config=self.web_config,
        )

    def inspect_account_state(self, session: RegisterSession) -> Tuple[str, bool]:
        probe = self.new_session(app=session.app)
        probe.email = session.email
        probe.password = session.password
        probe.visit_homepage()
        probe.get_csrf()
        probe.signin()
        current_url = probe.authorize()
        path = (urlparse(current_url).path or "").lower()
        host = (urlparse(current_url).hostname or "").lower()
        if any(marker in path for marker in ("create-account/password", "email-verification", "email-otp", "about-you")):
            return current_url, False
        if "callback" in path or host == "chatgpt.com":
            return current_url, True
        return current_url, host == "chatgpt.com"

    def ensure_account_ready(self, session: RegisterSession) -> None:
        for attempt in range(1, 3):
            _current_url, ready = self.inspect_account_state(session)
            if ready:
                return
            repair = self.new_session(app=session.app)
            repair.mailbox = session.mailbox
            repair.email = session.email
            repair.password = session.password
            repair.profile_name = session.profile_name
            repair.birthdate = session.birthdate
            repair.print(f"[OAuth] ensureAccountReady replay attempt={attempt}")
            repair.run_register()
            time.sleep(attempt * 2)

    def bootstrap_oauth_session(self, session: RegisterSession, authorize_url: str) -> Tuple[str, Dict[str, Any], bytes]:
        session.print("[OAuth] 1/7 GET /oauth/authorize")
        result = session._request(
            "GET",
            authorize_url,
            session.page_headers(session.chatgpt_base + "/"),
        )
        if result.status >= 400:
            raise RetryableOAuthError(f"oauth bootstrap failed after replay: {result.status} {result.text[:160]}")
        login_session = session.http.cookie_value("login_session")
        if login_session:
            result = session._request(
                "GET",
                f"{session.oauth_base}/api/oauth/oauth2/auth?{urlencode({'login_session': login_session})}",
                session.page_headers(authorize_url),
            )
            if result.status >= 400:
                raise RetryableOAuthError(f"oauth bootstrap failed after replay: {result.status} {result.text[:160]}")
        return result.final_url, result.data, result.raw

    def post_authorize_continue(self, session: RegisterSession) -> Tuple[str, Dict[str, Any], bytes]:
        session.update_task("oauth continue")
        session.print("[OAuth] 2/7 POST /api/accounts/authorize/continue")
        body = {
            "kind": "username",
            "value": session.email,
            "username": session.email,
            "screen_hint": "signup",
        }
        headers = session.oauth_json_headers(session.chatgpt_base + "/log-in")
        token = session._sentinel_token("authorize_continue", session.chatgpt_base + "/log-in")
        if token:
            headers["openai-sentinel-token"] = token
        result = session._json_post(
            session.chatgpt_base + "/api/accounts/authorize/continue",
            headers,
            body,
        )
        if result.status >= 400:
            text = result.text.lower()
            if "invalid_auth_step" in text:
                raise RetryableOAuthError("oauth authorize/continue returned invalid_auth_step")
            if result.status == 403:
                raise RetryableOAuthError("oauth authorize/continue returned 403")
            raise RetryableOAuthError(f"oauth authorize/continue failed: {result.status} {result.text[:160]}")
        next_url = session._pick_next_url(result, session.chatgpt_base + "/log-in")
        return next_url, result.data, result.raw

    def oauth_password_verify(self, session: RegisterSession) -> Tuple[str, Dict[str, Any], bytes]:
        session.update_task("oauth password")
        session.print("[OAuth] 3/7 POST /api/accounts/password/verify")
        headers = session.oauth_json_headers(session.chatgpt_base + "/log-in/password")
        token = session._sentinel_token("password_verify", session.chatgpt_base + "/log-in/password")
        if token:
            headers["openai-sentinel-token"] = token
        else:
            session.print("sentinel token for password_verify missing: env fallback empty")
        result = session._json_post(
            session.chatgpt_base + "/api/accounts/password/verify",
            headers,
            {"password": session.password},
        )
        text = result.text.lower()
        if result.status >= 400:
            if result.status == 403:
                raise RetryableOAuthError("oauth password/verify returned 403")
            if "invalid_state" in text or "invalid session" in text or "please start over" in text:
                raise RetryableOAuthError("oauth password/verify returned invalid_state")
            raise RetryableOAuthError(f"oauth password/verify failed: {result.status} {result.text[:160]}")
        next_url = session._pick_next_url(result, session.chatgpt_base + "/log-in/password")
        return next_url, result.data, result.raw

    def oauth_wait_and_validate_otp(self, session: RegisterSession) -> Tuple[str, Dict[str, Any], bytes]:
        session.update_task("oauth wait otp")
        session.print("[OAuth] Email OTP required")
        deadline = time.time() + 180
        while time.time() < deadline:
            elapsed = int(180 - max(deadline - time.time(), 0))
            session.print(f"[OAuth] OTP waiting... ({elapsed}s/180s)")
            remaining = max(int(deadline - time.time()), 1)
            try:
                code = session.wait_for_verification_email(
                    timeout_sec=min(remaining, 25),
                    after_ids=session.oauth_seen_message_ids,
                    disallow_codes=session.oauth_tried_codes,
                    oauth_mode=True,
                )
            except TimeoutError:
                continue
            session.oauth_tried_codes.add(code)
            session.update_task("oauth validate otp")
            session.print(f"[OAuth] Trying OTP: {code}")
            headers = session.oauth_json_headers(session.chatgpt_base + "/email-verification")
            token = session._sentinel_token("email_otp_validate", session.chatgpt_base + "/email-verification")
            if token:
                headers["openai-sentinel-token"] = token
            result = session._json_post(
                session.chatgpt_base + "/api/accounts/email-otp/validate",
                headers,
                {"code": code},
            )
            if result.status == 403:
                raise RetryableOAuthError("oauth otp validate returned 403")
            next_url = session._pick_next_url(result, session.chatgpt_base + "/email-verification")
            state = session._analyze_oauth_state(next_url, result.data, result.raw)
            if state.requires_otp:
                elapsed = int(180 - max(deadline - time.time(), 0))
                session.print(
                    f"[OAuth] Current OTP {code} already tried, waiting for a new one... ({elapsed}s/180s)"
                )
                time.sleep(2)
                continue
            return next_url, result.data, result.raw
        raise RetryableOAuthError("oauth OTP validation timed out waiting for a new code")

    def complete_oauth_account_setup(self, session: RegisterSession, current_url: str) -> str:
        session.update_task("oauth create account")
        session.print("[OAuth] Account still needs about-you, finalizing registration...")
        result = session._request(
            "GET",
            current_url,
            session.page_headers(session.chatgpt_base + "/about-you"),
        )
        if extract_code_from_url(result.final_url):
            return result.final_url
        state = session._analyze_oauth_state(result.final_url, result.data, result.raw)
        if state.requires_consent or state.requires_workspace_or_org or state.is_callback:
            return result.final_url
        create_result = session.create_account()
        if create_result.status == 400 and "already_exists" in create_result.text.lower():
            return session.chatgpt_base + "/sign-in-with-chatgpt/codex/consent"
        if create_result.status != 200:
            raise IncompleteRegistrationError(
                f"oauth create_account failed: {create_result.status} {create_result.text[:160]}"
            )
        return session._pick_next_url(create_result, session.chatgpt_base + "/sign-in-with-chatgpt/codex/consent")

    def oauth_follow_for_code(self, session: RegisterSession, current_url: str) -> str:
        session.update_task("oauth follow code")
        session.print("[OAuth] 5/7 Following continue_url for code")
        result = session._request(
            "GET",
            current_url,
            session.page_headers(session.chatgpt_base + "/log-in/password"),
        )
        if result.status >= 400:
            raise RetryableOAuthError(f"oauth follow code failed: {result.status} {result.text[:160]}")
        return result.final_url

    def _first_id(self, value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = map_string(item, "id", "workspace_id", "org_id", "project_id")
                    if found:
                        return found
        if isinstance(value, dict):
            return self._first_id(value.get("items") or value.get("data") or [])
        return ""

    def oauth_submit_workspace_and_org(
        self,
        session: RegisterSession,
        current_url: str,
        payload: Dict[str, Any],
        *,
        fallback: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        session.update_task("oauth select workspace")
        session.print(
            "[OAuth] 6/7 Fallback consent retry" if fallback else "[OAuth] 6/7 Selecting workspace/org"
        )
        workspaces = first_present(payload, ["workspaces"], default=[])
        if isinstance(payload.get("data"), dict):
            nested = payload["data"]
            workspaces = workspaces or nested.get("workspaces") or []
        workspace_id = self._first_id(workspaces)
        if not workspace_id:
            return current_url, payload
        headers = session.oauth_json_headers(current_url)
        headers["Referer"] = current_url
        workspace_result = session._json_post(
            session.chatgpt_base + "/api/accounts/workspace/select",
            headers,
            {"workspace_id": workspace_id},
        )
        if workspace_result.status >= 400:
            raise RetryableOAuthError(
                f"workspace/select failed: {workspace_result.status} {workspace_result.text[:160]}"
            )
        next_payload = workspace_result.data
        next_url = session._pick_next_url(workspace_result, current_url)
        nested = next_payload.get("data", {}) if isinstance(next_payload.get("data"), dict) else {}
        org_id = self._first_id(next_payload.get("orgs") or nested.get("orgs") or [])
        project_id = self._first_id(next_payload.get("projects") or nested.get("projects") or [])
        if not org_id:
            return next_url, next_payload
        headers = session.oauth_json_headers(next_url)
        headers["Referer"] = next_url
        org_payload = {"org_id": org_id}
        if project_id:
            org_payload["project_id"] = project_id
        org_result = session._json_post(
            session.chatgpt_base + "/api/accounts/organization/select",
            headers,
            org_payload,
        )
        if org_result.status >= 400:
            raise RetryableOAuthError(
                f"organization/select failed: {org_result.status} {org_result.text[:160]}"
            )
        return session._pick_next_url(org_result, next_url), org_result.data

    def exchange_oauth_code(self, session: RegisterSession, code: str) -> OAuthTokens:
        session.update_task("oauth token exchange")
        session.print("[OAuth] 7/7 POST /oauth/token")
        payload = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.app.config.oauth_redirect_uri,
                "client_id": self.app.config.oauth_client_id,
                "code_verifier": session.pkce_verifier,
            }
        ).encode("utf-8")
        result = session._request(
            "POST",
            session.oauth_base + "/oauth/token",
            {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                **make_trace_headers(),
            },
            payload,
        )
        if result.status == 403:
            raise RetryableOAuthError("oauth token exchange returned 403")
        if result.status >= 400:
            raise RetryableOAuthError(f"oauth token exchange failed: {result.status} {result.text[:160]}")
        tokens = OAuthTokens.from_dict(result.data)
        if not tokens.access_token:
            raise IncompleteRegistrationError("oauth token response missing access_token")
        session.print("[OAuth] Codex token acquired")
        return tokens

    def perform_codex_oauth(self, session: RegisterSession) -> OAuthTokens:
        session.print("[OAuth] Starting Codex OAuth protocol flow...")
        authorize_url = self.build_oauth_authorize_url(session)
        current_url, payload, raw = self.bootstrap_oauth_session(session, authorize_url)
        if extract_code_from_url(current_url):
            session.print("[OAuth] Existing authenticated session detected, skipping password and email OTP")
            return self.exchange_oauth_code(session, extract_code_from_url(current_url))

        session.oauth_seen_message_ids = session.snapshot_mailbox_message_ids()
        state = session._analyze_oauth_state(current_url, payload, raw)
        if not state.requires_otp and not state.requires_about_you and not state.requires_workspace_or_org and not state.requires_consent:
            current_url, payload, raw = self.post_authorize_continue(session)
            state = session._analyze_oauth_state(current_url, payload, raw)
            if state.requires_otp:
                session.print("[OAuth] authorize/continue requested email OTP, skipping password/verify")

        while True:
            code = extract_code_from_url(current_url)
            if code:
                return self.exchange_oauth_code(session, code)
            state = session._analyze_oauth_state(current_url, payload, raw)
            if state.requires_otp:
                current_url, payload, raw = self.oauth_wait_and_validate_otp(session)
                continue
            if state.requires_about_you:
                current_url = self.complete_oauth_account_setup(session, current_url)
                payload = {}
                raw = b""
                continue
            if state.requires_password:
                current_url, payload, raw = self.oauth_password_verify(session)
                continue
            if state.requires_workspace_or_org or state.requires_consent:
                current_url, payload = self.oauth_submit_workspace_and_org(session, current_url, payload)
                current_url = self.oauth_follow_for_code(session, current_url)
                payload = {}
                raw = b""
                continue
            if state.is_callback or state.looks_complete:
                current_url = self.oauth_follow_for_code(session, current_url)
                payload = {}
                raw = b""
                continue
            raise IncompleteRegistrationError("authorization code not obtained")

    def perform_codex_oauth_with_retry(self, session: RegisterSession) -> OAuthTokens:
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                return self.perform_codex_oauth(session)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self.is_incomplete_registration_error(exc):
                    self.ensure_account_ready(session)
                if attempt >= 3 or not self.is_retryable_oauth_error(exc):
                    break
                backoff = attempt * 3
                session.print(f"[OAuth] retry attempt={attempt} backoff={backoff}s err={exc}")
                time.sleep(backoff)
        raise last_exc if last_exc else RetryableOAuthError("oauth retry exhausted")

    def register_one(self, app: "App", domain_options: Sequence[str]) -> "AccountResult":
        cfg = self.web_config
        if cfg and cfg.use_registration_proxy and cfg.default_proxy and not app.proxy:
            app.proxy = cfg.default_proxy
        http = app.create_http_client()
        mailbox_client = self._mailbox_client(http)
        sentinel = self._sentinel_solver()
        session = self.new_session(app=app, http=http, mailbox_client=mailbox_client, sentinel=sentinel)
        app.logger.info("[run] starting live registration")
        session.mailbox = mailbox_client.create_mailbox(
            domain_options or (cfg.enabled_email_domains if cfg else [])
        )
        session.email = session.mailbox.address
        session.password = random_password()
        session.profile_name = random_name()
        session.birthdate = random_birthdate()
        session.log(f"[run] mailbox={session.email}")
        session.log(f"[run] profile={session.profile_name} birthdate={session.birthdate}")

        last_register_exc = None
        for attempt in range(1, 4):
            try:
                session.run_register()
                last_register_exc = None
                break
            except WholeFlowRestartError as exc:
                last_register_exc = exc
                session.print(f"[Register] whole-flow restart ({attempt}/3)")
                if attempt >= 3:
                    raise
                time.sleep(attempt)
        if last_register_exc is not None:
            raise last_register_exc

        tokens = session._tokens_from_session()
        oauth_ok = tokens.has_access_token()
        if not app.no_oauth and app.config.enable_oauth:
            session.print("[OAuth] Registration completed, waiting 5s before same-session Codex OAuth...")
            time.sleep(5)
            try:
                tokens = self.perform_codex_oauth_with_retry(session)
                oauth_ok = tokens.has_access_token()
            except Exception:
                if app.oauth_not_required or not app.config.oauth_required:
                    tokens = session._tokens_from_session()
                    oauth_ok = tokens.has_access_token()
                else:
                    raise

        token_payload = app.build_token_json_data(
            email=session.email,
            password=session.password,
            account_id=session.account_id,
            tokens=tokens,
            session_data=session.session_data,
            trace=session.trace,
        )
        token_path = app.save_codex_tokens(
            email=session.email,
            token_json=token_payload,
            tokens=tokens,
        )
        if not app.no_upload:
            app.upload_token_for_email(session.email, token_path)
        return app.make_account_result(
            email=session.email,
            password=session.password,
            mailbox_password=session.mailbox.password if session.mailbox else "",
            account_id=session.account_id,
            token_path=token_path,
            notes="live",
            oauth_ok=oauth_ok,
        )


from .app import AccountResult, App  # noqa: E402  # isort:skip
