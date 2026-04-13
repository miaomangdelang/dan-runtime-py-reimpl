import email
import imaplib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set
from urllib.parse import quote, urlencode

from .http import HTTPClient, RequestOptions
from .util import extract_verification_code, first_present, map_string, random_password


@dataclass
class Mailbox:
    address: str = ""
    domain: str = ""
    mailbox_id: str = ""
    password: str = ""


@dataclass
class MailboxMessage:
    message_id: str = ""
    subject: str = ""
    sender: str = ""
    recipients: List[str] = field(default_factory=list)
    text: str = ""
    html: str = ""


class MailboxClient:
    def create_mailbox(self, domain_options: Sequence[str]) -> Mailbox:
        raise NotImplementedError("mailbox API not implemented")

    def snapshot_message_ids(self, mailbox: Mailbox) -> Set[str]:
        return set()

    def fetch_otp(
        self,
        mailbox: Mailbox,
        timeout_sec: int,
        *,
        after_ids: Optional[Set[str]] = None,
        disallow_codes: Optional[Set[str]] = None,
        expected_recipient: str = "",
    ) -> str:
        raise NotImplementedError("mailbox API not implemented")


class NullMailboxClient(MailboxClient):
    def create_mailbox(self, domain_options: Sequence[str]) -> Mailbox:
        raise NotImplementedError("mailbox API not configured")

    def fetch_otp(
        self,
        mailbox: Mailbox,
        timeout_sec: int,
        *,
        after_ids: Optional[Set[str]] = None,
        disallow_codes: Optional[Set[str]] = None,
        expected_recipient: str = "",
    ) -> str:
        raise NotImplementedError("mailbox API not configured")


class CloudmailMailboxClient(MailboxClient):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        http: HTTPClient,
        *,
        poll_interval_sec: int = 5,
        logger=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_base = (
            self.base_url if self.base_url.endswith("/api/v1") else self.base_url + "/api/v1"
        )
        self.api_key = api_key
        self.http = http
        self.poll_interval_sec = poll_interval_sec
        self._domains_cache: Optional[List[str]] = None
        self.logger = logger

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
        }

    def create_mailbox(self, domain_options: Sequence[str]) -> Mailbox:
        domain = self.pick_real_domain(domain_options)
        self._log(f"[MailAPI] using domain {domain}")
        for _ in range(8):
            local = random_local_part()
            password = random_password(length=18)
            address = f"{local}@{domain}"
            payload = {
                "email": address,
                "raw_password": password,
                "quota_bytes": 1073741824,
                "enabled": True,
                "enable_imap": True,
                "enable_pop": True,
            }
            status, _, _data, _raw = self.http.json_post(
                self.api_base + "/user",
                json.dumps(payload).encode("utf-8"),
                headers={**self._headers, "content-type": "application/json"},
            )
            if status < 400:
                self._log(f"[MailAPI] mailbox created: {address}")
                return Mailbox(
                    address=address,
                    domain=domain,
                    mailbox_id=address,
                    password=password,
                )
            if status == 409:
                continue
        raise RuntimeError("mailbox create failed")

    def snapshot_message_ids(self, mailbox: Mailbox) -> Set[str]:
        return {msg.message_id for msg in self.fetch_messages(mailbox) if msg.message_id}

    def fetch_otp(
        self,
        mailbox: Mailbox,
        timeout_sec: int,
        *,
        after_ids: Optional[Set[str]] = None,
        disallow_codes: Optional[Set[str]] = None,
        expected_recipient: str = "",
    ) -> str:
        after_ids = set(after_ids or set())
        disallow_codes = set(disallow_codes or set())
        expected_targets = mailbox_recipient_variants(expected_recipient or mailbox.address)
        deadline = time.time() + timeout_sec
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                code = self._fetch_otp_http(
                    mailbox,
                    after_ids=after_ids,
                    disallow_codes=disallow_codes,
                    expected_targets=expected_targets,
                )
                if code:
                    self._log(f"[MailAPI] otp received for {mailbox.address}: {code}")
                    return code
            except Exception as exc:
                self._log(f"[MailAPI] HTTP poll failed for {mailbox.address}: {exc}")
            try:
                code = self._fetch_otp_imap(mailbox, disallow_codes=disallow_codes)
                if code:
                    self._log(f"[MailAPI] otp received via IMAP for {mailbox.address}: {code}")
                    return code
            except Exception as exc:
                self._log(f"[MailAPI] imap poll failed for {mailbox.address}: {exc}")
            self._log(f"[MailAPI] otp poll #{attempt} no code yet for {mailbox.address}")
            time.sleep(self.poll_interval_sec)
        raise TimeoutError("verification code not received after polling window")

    def fetch_messages(self, mailbox: Mailbox) -> List[MailboxMessage]:
        mailbox_id = mailbox.mailbox_id or mailbox.address
        query = urlencode({"mailbox_id": mailbox_id, "address": mailbox.address})
        quoted_id = quote(mailbox_id, safe="")
        candidates = [
            self.api_base + f"/mailbox/{quoted_id}/messages",
            self.api_base + f"/mailbox/{quoted_id}",
            self.base_url + f"/api/v1/mailbox/{quoted_id}/messages",
            self.base_url + f"/api/v1/mailbox/{quoted_id}",
            self.base_url + f"/api/mailboxes/{quoted_id}/messages",
            self.base_url + f"/mailboxes/{quoted_id}/messages",
            self.base_url + f"/api/messages?{query}",
            self.base_url + f"/messages?{query}",
        ]
        for url in candidates:
            status, _, data, _ = self.http.request_json(
                RequestOptions("GET", url, headers=dict(self._headers))
            )
            items = self._normalize_messages(data)
            if status < 400 and items is not None:
                return items
        return []

    def fetch_message_detail(self, mailbox: Mailbox, message_id: str) -> Optional[MailboxMessage]:
        mailbox_id = mailbox.mailbox_id or mailbox.address
        query = urlencode({"mailbox_id": mailbox_id, "address": mailbox.address})
        quoted_id = quote(message_id, safe="")
        candidates = [
            self.api_base + f"/mailbox/{quote(mailbox_id, safe='')}/message/{quoted_id}",
            self.base_url + f"/api/messages/{quoted_id}",
            self.base_url + f"/messages/{quoted_id}",
            self.base_url + f"/api/mailboxes/{quote(mailbox_id, safe='')}/messages/{quoted_id}",
            self.base_url + f"/mailboxes/{quote(mailbox_id, safe='')}/messages/{quoted_id}",
            self.base_url + f"/api/message/{quoted_id}?{query}",
        ]
        for url in candidates:
            status, _, data, body = self.http.request_json(
                RequestOptions("GET", url, headers=dict(self._headers))
            )
            if status >= 400:
                continue
            if data:
                items = self._normalize_messages(data)
                if items:
                    return items[0]
                return self._normalize_message(data)
            raw = body.decode("utf-8", errors="ignore")
            if raw:
                return MailboxMessage(message_id=message_id, text=raw, html=raw)
        return None

    def fetch_message_text(self, mailbox: Mailbox, message_id: str) -> str:
        detail = self.fetch_message_detail(mailbox, message_id)
        if not detail:
            return ""
        return detail.text or detail.html

    def _normalize_message(self, raw: Dict[str, object]) -> MailboxMessage:
        if not isinstance(raw, dict):
            return MailboxMessage()
        nested = raw.get("data")
        if isinstance(nested, dict):
            raw = {**raw, **nested}
        message = raw.get("message")
        if isinstance(message, dict):
            raw = {**raw, **message}
        recipients = string_list(
            raw.get("to")
            or raw.get("recipients")
            or raw.get("recipient")
            or raw.get("address")
        )
        return MailboxMessage(
            message_id=map_string(raw, "id", "message_id", "messageId"),
            subject=map_string(raw, "subject", "title"),
            sender=map_string(raw, "from", "sender", "from_email"),
            recipients=recipients,
            text=map_string(raw, "text", "body", "content"),
            html=map_string(raw, "html"),
        )

    def _normalize_messages(self, data: Dict[str, object]) -> Optional[List[MailboxMessage]]:
        if not data:
            return []
        items = first_present(
            data,
            ["messages", "items", "list", "data", "results"],
            default=[],
        )
        if isinstance(items, dict):
            if any(key in items for key in ("id", "message_id", "messageId")):
                return [self._normalize_message(items)]
            items = items.get("messages") or items.get("items") or []
        if not isinstance(items, list):
            return []
        return [self._normalize_message(raw) for raw in items if isinstance(raw, dict)]

    def list_domains(self) -> List[str]:
        if self._domains_cache is not None:
            return self._domains_cache
        status, _, raw = self.http.request(
            RequestOptions("GET", self.api_base + "/domain", headers=dict(self._headers))
        )
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            data = []
        domains: List[str] = []
        if status < 400 and isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    name = map_string(item, "name")
                    if name:
                        domains.append(name)
        elif status < 400 and isinstance(data, dict):
            items = data.get("items") or data.get("domains") or []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        name = map_string(item, "name")
                        if name:
                            domains.append(name)
        self._domains_cache = domains
        return domains

    def pick_real_domain(self, requested: Sequence[str]) -> str:
        available = self.list_domains()
        if not available:
            raw = pick_mail_domain(requested)
            return raw.lstrip("*.") if raw.startswith("*.") else raw
        normalized = []
        for item in requested:
            value = str(item).strip()
            if value.startswith("*."):
                value = value[2:]
            normalized.append(value)
        for candidate in normalized:
            if candidate in available:
                return candidate
        return available[0]

    def _fetch_otp_http(
        self,
        mailbox: Mailbox,
        *,
        after_ids: Set[str],
        disallow_codes: Set[str],
        expected_targets: Sequence[str],
    ) -> str:
        for message in self.fetch_messages(mailbox):
            if not message.message_id:
                continue
            if message.message_id in after_ids:
                continue
            after_ids.add(message.message_id)
            detail = self.fetch_message_detail(mailbox, message.message_id) or message
            if expected_targets and not message_targets_email(detail, expected_targets):
                continue
            text = detail.text or detail.html or self.fetch_message_text(mailbox, message.message_id)
            code = extract_verification_code(
                f"{detail.subject}\n{text}"
            )
            if code and code not in disallow_codes:
                return code
        return ""

    def _fetch_otp_imap(self, mailbox: Mailbox, *, disallow_codes: Set[str]) -> str:
        host = self.base_url.split("://", 1)[-1].split("/", 1)[0]
        with imaplib.IMAP4_SSL(host, 993, timeout=15) as client:
            client.login(mailbox.address, mailbox.password)
            client.select("INBOX")
            typ, data = client.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                self._log(f"[MailAPI] inbox empty for {mailbox.address}")
                return ""
            ids = data[0].split()[-10:]
            self._log(f"[MailAPI] inbox has {len(ids)} messages for {mailbox.address}")
            ids.reverse()
            for msg_id in ids:
                typ, fetched = client.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not fetched:
                    continue
                raw = b""
                for part in fetched:
                    if isinstance(part, tuple) and part[1]:
                        raw += part[1]
                if not raw:
                    continue
                message = email.message_from_bytes(raw)
                text = _message_text(message)
                code = extract_verification_code(f"{message.get('Subject','')}\n{text}")
                if code and code not in disallow_codes:
                    return code
        return ""



def mailbox_recipient_variants(address: str) -> List[str]:
    address = (address or "").strip().lower()
    if not address:
        return []
    local, _, domain = address.partition("@")
    variants = {address, local}
    if domain:
        variants.add(f"<{address}>")
        variants.add(domain)
    return [item for item in variants if item]



def message_targets_email(message: MailboxMessage, targets: Sequence[str]) -> bool:
    if not targets:
        return True
    lowered_targets = [item.lower() for item in targets if item]
    haystack_parts = [message.subject, message.sender, message.text, message.html] + list(message.recipients)
    haystack = "\n".join(part for part in haystack_parts if part).lower()
    return any(target in haystack for target in lowered_targets)



def pick_mail_domain(domain_options: Sequence[str]) -> str:
    cleaned = [str(item).strip() for item in domain_options if str(item).strip()]
    if not cleaned:
        return "example.invalid"
    raw = cleaned[0]
    if raw.startswith("*."):
        return f"mail.{raw[2:]}"
    return raw



def random_local_part(size: int = 10) -> str:
    rng = random.Random()
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choice(alphabet) for _ in range(size))



def string_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
        return [part for part in parts if part]
    return []



def _message_text(message: email.message.Message) -> str:
    if message.is_multipart():
        parts: List[str] = []
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            try:
                parts.append(payload.decode(charset, errors="ignore"))
            except LookupError:
                parts.append(payload.decode("utf-8", errors="ignore"))
        return "\n".join(parts)
    payload = message.get_payload(decode=True) or b""
    charset = message.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except LookupError:
        return payload.decode("utf-8", errors="ignore")
