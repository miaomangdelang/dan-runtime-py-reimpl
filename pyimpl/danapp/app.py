import json
import logging
import os
import random
import secrets
import string
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

from .config import Config
from .http import HTTPClient, RequestOptions
from .mailbox import MailboxClient
from .oauth import OAuthTokens
from .sentinel import SentinelSolver
from .util import fixed_now_string, map_string


@dataclass
class AccountResult:
    email: str
    password: str
    mailbox_password: str = ""
    account_id: str = ""
    token_path: str = ""
    created_at: float = 0.0
    notes: str = ""
    oauth_ok: bool = False


class RegistrationRunner(Protocol):
    def register_one(self, app: "App", domain_options: Sequence[str]) -> AccountResult:
        ...


class App:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.http: Optional[HTTPClient] = None
        self.mailbox: Optional[MailboxClient] = None
        self.sentinel: Optional[SentinelSolver] = None
        self.logger = logging.getLogger("dan.pyimpl")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.output_path: str = "registered_accounts.txt"
        self.no_upload: bool = False
        self.no_oauth: bool = False
        self.oauth_not_required: bool = False
        self.proxy: str = ""
        self.disable_proxy: bool = False
        self.use_env_proxy: bool = False
        self.allow_network: bool = False
        self.registration_runner: Optional[RegistrationRunner] = None
        self.chatgpt_base_url: str = os.getenv("DAN_CHATGPT_BASE_URL", "https://chatgpt.com")

    def run(self, count: int, domains: Optional[Sequence[str]] = None) -> List[AccountResult]:
        if count <= 0:
            raise ValueError("count must be > 0")
        results: List[AccountResult] = []
        for _ in range(count):
            res = self.register_one(domains)
            if res:
                self.append_result(res)
                results.append(res)
        return results

    def register_one(self, domains: Optional[Sequence[str]] = None) -> Optional[AccountResult]:
        if self.registration_runner is None:
            if not self.allow_network:
                raise NotImplementedError(
                    "registration flow is disabled; configure a RegistrationRunner or enable an authorized implementation"
                )
            raise NotImplementedError("registration flow not implemented")
        return self.registration_runner.register_one(self, domains or [])

    def has_registration_runner(self) -> bool:
        return self.registration_runner is not None

    def use_mock_registration(self, seed: Optional[int] = None) -> None:
        self.registration_runner = MockRegistrationRunner(seed=seed)

    def use_live_registration(self, web_config_path: str = "config/web_config.json") -> None:
        from .register_flow import OpenAIRegistrationRunner

        self.registration_runner = OpenAIRegistrationRunner(self, web_config_path=web_config_path)

    def create_http_client(self) -> HTTPClient:
        self.http = HTTPClient(
            proxy=self.proxy,
            disable_proxy=self.disable_proxy,
            use_env_proxy=self.use_env_proxy,
        )
        return self.http

    def env_sentinel_token(self) -> str:
        return os.getenv("SENTINEL_TOKEN", "").strip()

    def append_result(self, res: AccountResult) -> None:
        parts = [res.email, res.password]
        if res.mailbox_password:
            parts.append(res.mailbox_password)
        status = "ok" if res.oauth_ok or self.no_oauth or not self.config.enable_oauth else "fail"
        append_line(self.output_path, "----".join(parts) + f"----oauth={status}\n")

    def save_token_json(self, email: str, data: bytes) -> str:
        token_dir = self.config.token_json_dir
        if not token_dir:
            raise ValueError("token_json_dir is empty")
        os.makedirs(token_dir, exist_ok=True)
        filename = f"{sanitize_filename(email)}.json"
        path = os.path.join(token_dir, filename)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _append_token_file(self, path: str, token: str) -> None:
        if not path or not token:
            return
        absolute = os.path.abspath(path)
        parent = os.path.dirname(absolute)
        if parent:
            os.makedirs(parent, exist_ok=True)
        append_line(absolute, token + "\n")

    def save_codex_tokens(
        self,
        *,
        email: str,
        token_json: Dict[str, Any],
        tokens: OAuthTokens,
    ) -> str:
        if tokens.access_token:
            self._append_token_file(self.config.ak_file, tokens.access_token)
        if tokens.refresh_token:
            self._append_token_file(self.config.rk_file, tokens.refresh_token)
        raw = json.dumps(token_json, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return self.save_token_json(email, raw)

    def pending_token_json_paths(self) -> List[str]:
        token_dir = self.config.token_json_dir
        if not token_dir or not os.path.isdir(token_dir):
            return []
        return [
            os.path.join(token_dir, name)
            for name in sorted(os.listdir(token_dir))
            if name.endswith(".json")
        ]

    def build_token_json_data(
        self,
        *,
        email: str,
        password: str,
        account_id: str,
        tokens: OAuthTokens,
        session_data: Optional[Dict[str, Any]] = None,
        trace: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        payload = {
            **tokens.to_dict(),
            "email": email,
            "password": password,
            "account_id": account_id,
            "created_at": fixed_now_string(),
            "source": "live_register" if self.allow_network else "mock_register",
            "session": session_data or {},
            "trace": list(trace or []),
        }
        if session_data:
            for key in ("chatgpt_account_id", "session_id"):
                value = map_string(session_data, key)
                if value and key not in payload:
                    payload[key] = value
        return payload

    def make_account_result(
        self,
        *,
        email: str,
        password: str,
        mailbox_password: str = "",
        account_id: str = "",
        token_path: str = "",
        notes: str = "",
        oauth_ok: bool = False,
    ) -> AccountResult:
        return AccountResult(
            email=email,
            password=password,
            mailbox_password=mailbox_password,
            account_id=account_id,
            token_path=token_path,
            created_at=time.time(),
            notes=notes,
            oauth_ok=oauth_ok,
        )

    def normalize_upload_endpoint(self) -> str:
        endpoint = (self.config.upload_api_url or "").strip()
        if not endpoint:
            return ""
        if endpoint.endswith("/v0/management/auth-files"):
            return endpoint
        if endpoint.endswith("/"):
            endpoint = endpoint[:-1]
        if endpoint.rsplit("/", 1)[-1] and "/v0/" in endpoint:
            return endpoint
        return endpoint + "/v0/management/auth-files"

    def _multipart_token_body(
        self,
        *,
        filename: str,
        payload: bytes,
        field_name: str = "file",
    ) -> tuple[str, bytes]:
        boundary = f"----dan-{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            ).encode("utf-8"),
            b"Content-Type: application/json\r\n\r\n",
            payload,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
        return boundary, b"".join(parts)

    def upload_token_json(self, email: str, token_json: Dict[str, Any], token_path: str = "") -> bool:
        if self.no_upload:
            return True
        endpoint = self.normalize_upload_endpoint()
        if not endpoint:
            return False
        client = self.http or self.create_http_client()
        raw = json.dumps(token_json, ensure_ascii=False, indent=2).encode("utf-8")
        filename = os.path.basename(token_path) if token_path else f"{sanitize_filename(email)}.json"
        boundary, multipart_body = self._multipart_token_body(filename=filename, payload=raw)
        headers = {
            "Authorization": f"Bearer {self.config.upload_api_token}" if self.config.upload_api_token else "",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": getattr(client, "user_agent", "dan-python/0.1"),
        }
        status, _hdrs, _body = client.request(
            RequestOptions("POST", endpoint, headers=headers, body=multipart_body)
        )
        if status < 400:
            return True
        fallback_headers = {
            "Authorization": headers["Authorization"],
            "Content-Type": "application/json",
            "User-Agent": headers["User-Agent"],
        }
        fallback_status, _hdrs, _body = client.request(
            RequestOptions("POST", endpoint, headers=fallback_headers, body=raw)
        )
        return fallback_status < 400

    def upload_token_for_email(self, email: str, token_path: str) -> bool:
        if self.no_upload:
            return True
        if not token_path or not os.path.isfile(token_path):
            return False
        with open(token_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return self.upload_token_json(email, payload, token_path=token_path)

    def upload_pending_tokens_detailed(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for path in self.pending_token_json_paths():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                email = map_string(payload, "email")
                ok = self.upload_token_json(email, payload, token_path=path)
                results.append({"path": path, "email": email, "ok": ok})
            except Exception as exc:
                results.append({"path": path, "ok": False, "error": str(exc)})
        return results


class MockRegistrationRunner:
    def __init__(self, seed: Optional[int] = None) -> None:
        self.random = random.Random(seed if seed is not None else time.time_ns())

    def register_one(self, app: App, domain_options: Sequence[str]) -> AccountResult:
        now = time.time()
        email = self._random_email(domain_options)
        password = self._random_password()
        mailbox_password = self._random_password()
        account_id = uuid.uuid4().hex
        tokens = OAuthTokens(
            access_token=self._random_token("atk"),
            refresh_token=self._random_token("rtk"),
            session_token=self._random_token("stk"),
            id_token=self._random_token("idk"),
            expires_at=int(now + 3600),
            token_type="Bearer",
        )
        token_payload = app.build_token_json_data(
            email=email,
            password=password,
            account_id=account_id,
            tokens=tokens,
            session_data={},
            trace=["mock registration"],
        )
        token_path = app.save_codex_tokens(
            email=email,
            token_json=token_payload,
            tokens=tokens,
        )
        return app.make_account_result(
            email=email,
            password=password,
            mailbox_password=mailbox_password,
            account_id=account_id,
            token_path=token_path,
            notes="mock",
            oauth_ok=True,
        )

    def _random_email(self, domain_options: Sequence[str]) -> str:
        domain = pick_domain(domain_options, self.random)
        local = f"{random_label(self.random, 8)}{self.random.randint(10, 99)}"
        return f"{local}@{domain}"

    def _random_password(self) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(self.random.choice(alphabet) for _ in range(14))

    def _random_token(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(24)}"


def append_line(path: str, line: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)



def sanitize_filename(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalnum() or ch in ("@", ".", "-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)



def pick_domain(domain_options: Sequence[str], rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    if not domain_options:
        return "example.invalid"
    raw = str(rng.choice(list(domain_options))).strip()
    if raw.startswith("*."):
        return f"{random_label(rng)}.{raw[2:]}"
    return raw.lstrip(".")



def random_label(rng: Optional[random.Random] = None, size: int = 6) -> str:
    rng = rng or random.Random()
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(size))
