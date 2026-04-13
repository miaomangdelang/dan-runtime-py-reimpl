from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class OAuthTokens:
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    session_token: str = ""
    token_type: str = ""
    expires_at: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OAuthTokens":
        return cls(
            access_token=str(data.get("access_token") or data.get("accessToken") or ""),
            refresh_token=str(data.get("refresh_token") or data.get("refreshToken") or ""),
            id_token=str(data.get("id_token") or data.get("idToken") or ""),
            session_token=str(data.get("session_token") or data.get("sessionToken") or ""),
            token_type=str(data.get("token_type") or data.get("tokenType") or ""),
            expires_at=int(
                data.get("expires_at")
                or data.get("expiresAt")
                or data.get("expires")
                or 0
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "session_token": self.session_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at,
        }

    def has_access_token(self) -> bool:
        return bool(self.access_token)

    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token)

    def has_session_token(self) -> bool:
        return bool(self.session_token)
