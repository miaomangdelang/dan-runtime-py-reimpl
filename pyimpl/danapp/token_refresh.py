import json
import os
import time
from typing import Any, Dict, Protocol
from urllib.parse import urlencode

from .config import Config
from .http import HTTPClient
from .oauth import OAuthTokens
from .util import first_present, make_trace_headers, map_string


class TokenRefresher(Protocol):
    def refresh(self, token: OAuthTokens) -> OAuthTokens:
        ...


class OpenAITokenRefresher:
    def __init__(self, config: Config, http: HTTPClient) -> None:
        self.config = config
        self.http = http
        self.chatgpt_base = os.getenv("DAN_CHATGPT_BASE_URL", "https://chatgpt.com").rstrip("/")

    def refresh(self, token: OAuthTokens) -> OAuthTokens:
        refreshed = self._oauth_refresh(token)
        if refreshed and refreshed.has_access_token():
            return refreshed
        refreshed = self._chatgpt_refresh(token)
        if refreshed and refreshed.has_access_token():
            return refreshed
        raise RuntimeError("token refresh not implemented for current token set")

    def _oauth_refresh(self, token: OAuthTokens) -> OAuthTokens:
        if not token.refresh_token:
            return OAuthTokens()
        issuer = (self.config.oauth_issuer or "").rstrip("/")
        if not issuer:
            return OAuthTokens()
        payload = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": self.config.oauth_client_id,
            }
        ).encode("utf-8")
        status, _, data, _ = self.http.form_post(
            f"{issuer}/oauth/token",
            payload,
            headers={"accept": "application/json", **make_trace_headers()},
        )
        if status >= 400:
            return OAuthTokens()
        return OAuthTokens.from_dict(
            {
                "access_token": map_string(data, "access_token", "accessToken"),
                "refresh_token": map_string(data, "refresh_token", "refreshToken") or token.refresh_token,
                "id_token": map_string(data, "id_token", "idToken") or token.id_token,
                "session_token": map_string(data, "session_token", "sessionToken") or token.session_token,
                "token_type": map_string(data, "token_type", "tokenType") or token.token_type,
                "expires_at": first_present(data, ["expires_at", "expiresAt"], int(time.time()) + 3600),
            }
        )

    def _chatgpt_refresh(self, token: OAuthTokens) -> OAuthTokens:
        candidates = [
            (
                "/api/auth/session",
                urlencode({
                    "refresh_token": token.refresh_token or "",
                    "access_token": token.access_token or "",
                }).encode("utf-8"),
                "form",
            ),
            (
                "/api/auth/refresh",
                json.dumps(
                    {
                        "refresh_token": token.refresh_token,
                        "access_token": token.access_token,
                    }
                ).encode("utf-8"),
                "json",
            ),
        ]
        for path, body, body_type in candidates:
            headers = {"accept": "application/json", **make_trace_headers()}
            if token.session_token:
                headers["authorization"] = f"Bearer {token.session_token}"
            if body_type == "json":
                status, _, data, _ = self.http.json_post(self.chatgpt_base + path, body, headers=headers)
            else:
                status, _, data, _ = self.http.form_post(self.chatgpt_base + path, body, headers=headers)
            if status >= 400 or not data:
                continue
            refreshed = OAuthTokens.from_dict(
                {
                    "access_token": map_string(data, "access_token", "accessToken"),
                    "refresh_token": map_string(data, "refresh_token", "refreshToken") or token.refresh_token,
                    "id_token": map_string(data, "id_token", "idToken") or token.id_token,
                    "session_token": map_string(data, "session_token", "sessionToken") or token.session_token,
                    "token_type": map_string(data, "token_type", "tokenType") or token.token_type,
                    "expires_at": first_present(data, ["expires_at", "expiresAt"], int(time.time()) + 3600),
                }
            )
            if refreshed.has_access_token():
                return refreshed
        return OAuthTokens()



def refresh_token_json_directory(token_dir: str, refresher: TokenRefresher) -> None:
    if refresher is None:
        raise NotImplementedError("token refresher is required")
    if not os.path.isdir(token_dir):
        raise FileNotFoundError(token_dir)
    for name in os.listdir(token_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(token_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
        token = OAuthTokens.from_dict(data)
        new_token = refresher.refresh(token)
        data.update(new_token.to_dict())
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
