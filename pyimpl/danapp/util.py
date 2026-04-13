import base64
import hashlib
import json
import random
import re
import string
import time
import uuid
from datetime import date, timedelta
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

OTP_RE = re.compile(r"\b(\d{4,8})\b")

FIRST_NAMES = [
    "James",
    "John",
    "Robert",
    "Michael",
    "William",
    "David",
    "Joseph",
    "Thomas",
    "Charles",
    "Daniel",
    "Emma",
    "Olivia",
    "Ava",
    "Sophia",
    "Isabella",
    "Mia",
    "Evelyn",
    "Harper",
    "Amelia",
    "Charlotte",
]

LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Taylor",
    "Anderson",
    "Thomas",
    "Jackson",
    "White",
    "Harris",
    "Martin",
    "Thompson",
    "Moore",
    "Clark",
]


def random_name(rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def random_birthdate(rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    start = date(1980, 1, 1)
    end = date(2004, 12, 31)
    delta_days = (end - start).days
    return (start + timedelta(days=rng.randint(0, delta_days))).isoformat()


def random_password(rng: Optional[random.Random] = None, length: int = 14) -> str:
    rng = rng or random.Random()
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(rng.choice(alphabet) for _ in range(length))


def random_delay_seconds(
    min_seconds: float = 0.2,
    max_seconds: float = 0.8,
    rng: Optional[random.Random] = None,
) -> float:
    rng = rng or random.Random()
    return rng.uniform(min_seconds, max_seconds)


def token_urlsafe_from_uuid() -> str:
    raw = uuid.uuid4().bytes + uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def generate_pkce() -> Tuple[str, str]:
    verifier = token_urlsafe_from_uuid()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def extract_code_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("code", "authorization_code", "otp", "token"):
        value = qs.get(key)
        if value and value[0]:
            return value[0]
    if parsed.fragment:
        fs = parse_qs(parsed.fragment)
        for key in ("code", "authorization_code", "otp", "token"):
            value = fs.get(key)
            if value and value[0]:
                return value[0]
    return ""


def extract_verification_code(text: str) -> str:
    if not text:
        return ""
    match = OTP_RE.search(text)
    return match.group(1) if match else ""


def decode_json_bytes(data: bytes) -> Dict[str, Any]:
    if not data:
        return {}
    try:
        obj = json.loads(data.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def string_any(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def int_any(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def map_string(data: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in data:
            value = string_any(data.get(key))
            if value:
                return value
    return ""


def map_bool(data: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    for key in keys:
        if key in data:
            value = data.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(value, (int, float)):
                return bool(value)
    return default


def first_present(data: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return default


def make_trace_headers() -> Dict[str, str]:
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]
    return {
        "traceparent": f"00-{trace_id}-{span_id}-01",
        "x-request-id": uuid.uuid4().hex,
    }


def fixed_now_string() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
