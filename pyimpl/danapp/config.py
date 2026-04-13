import json
from dataclasses import asdict, dataclass, field
from typing import List


@dataclass
class Config:
    ak_file: str = ""
    rk_file: str = ""
    token_json_dir: str = ""
    server_config_url: str = ""
    server_api_token: str = ""
    domain_report_url: str = ""
    upload_api_url: str = ""
    upload_api_token: str = ""
    oauth_issuer: str = ""
    oauth_client_id: str = ""
    oauth_redirect_uri: str = ""
    enable_oauth: bool = False
    oauth_required: bool = False

    def validate(self) -> None:
        if not self.token_json_dir:
            raise ValueError("token_json_dir is required")
        if self.enable_oauth and not self.oauth_issuer:
            raise ValueError("oauth_issuer is required when enable_oauth is true")


@dataclass
class WebConfig:
    target_min_tokens: int = 0
    auto_fill_start_gap: int = 0
    check_interval_minutes: int = 0
    manual_default_threads: int = 0
    manual_register_retries: int = 0
    web_token: str = ""
    client_api_token: str = ""
    client_notice: str = ""
    minimum_client_version: str = ""
    enabled_email_domains: List[str] = field(default_factory=list)
    mail_domain_options: List[str] = field(default_factory=list)
    default_proxy: str = ""
    use_registration_proxy: bool = False
    cpa_base_url: str = ""
    cpa_token: str = ""
    mail_api_url: str = ""
    mail_api_key: str = ""
    port: int = 0

    def validate(self) -> None:
        if self.port <= 0:
            raise ValueError("port is required")
        self.enabled_email_domains = [d for d in self.enabled_email_domains if d]
        self.mail_domain_options = [d for d in self.mail_domain_options if d]

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = Config(**data)
    cfg.validate()
    return cfg


def load_web_config(path: str) -> WebConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = WebConfig(**data)
    cfg.validate()
    return cfg


def save_web_config(path: str, cfg: WebConfig) -> None:
    cfg.validate()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, indent=2, sort_keys=False, ensure_ascii=False)
        f.write("\n")
