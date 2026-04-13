import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


@dataclass
class SentinelPayload:
    kind: str
    url: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


FLOW_KIND_MAP = {
    "register.submit_password": "username_password_create",
    "register.submit_password.retry": "username_password_create",
    "username_password_create": "username_password_create",
    "register.create_account": "oauth_create_account",
    "oauth_create_account": "oauth_create_account",
    "authorize_continue": "authorize_continue",
    "oauth authorize/continue": "authorize_continue",
    "password_verify": "password_verify",
    "oauth password/verify": "password_verify",
    "register.validate_otp": "email_otp_validate",
    "register.validate_otp.retry": "email_otp_validate",
    "email_otp_validate": "email_otp_validate",
    "oauth otp/validate": "email_otp_validate",
}

DEFAULT_PAGE_URLS = {
    "username_password_create": "https://auth.openai.com/create-account/password",
    "oauth_create_account": "https://auth.openai.com/about-you",
    "authorize_continue": "https://auth.openai.com/log-in",
    "password_verify": "https://auth.openai.com/log-in/password",
    "email_otp_validate": "https://chatgpt.com/email-verification",
}

BROWSER_HELPER_SCRIPT = r'''#!/usr/bin/env python3
import os
import shutil
import sys

from playwright.sync_api import sync_playwright


def iter_system_chromium_paths():
    candidates = []
    if sys.platform.startswith("linux"):
        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
            "/snap/bin/chromium",
        ])
    elif sys.platform == "darwin":
        candidates.extend([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ])

    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
    ):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)

    seen = set()
    for path in candidates:
        path = (path or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            yield path


def launch_browser(playwright, launch_args):
    try:
        return playwright.chromium.launch(**launch_args)
    except Exception as exc:
        last_exc = exc
        for executable_path in iter_system_chromium_paths():
            fallback_args = dict(launch_args)
            fallback_args["executable_path"] = executable_path
            print(f"fallback browser: {executable_path}", file=sys.stderr)
            try:
                return playwright.chromium.launch(**fallback_args)
            except Exception as fallback_exc:
                last_exc = fallback_exc
        raise last_exc


def main():
    flow = sys.argv[1] if len(sys.argv) > 1 else "oauth_create_account"
    page_url = os.environ.get("SENTINEL_BROWSER_PAGE_URL", "").strip() or "https://auth.openai.com/about-you"
    proxy = os.environ.get("SENTINEL_BROWSER_PROXY", "").strip()
    user_agent = os.environ.get("SENTINEL_BROWSER_UA", "").strip()
    timeout_ms = int(os.environ.get("SENTINEL_BROWSER_TIMEOUT_MS", "45000"))

    with sync_playwright() as playwright:
        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if proxy:
            launch_args["proxy"] = {"server": proxy}

        browser = launch_browser(playwright, launch_args)
        try:
            context_kwargs = {
                "viewport": {"width": 1440, "height": 900},
                "ignore_https_errors": True,
            }
            if user_agent:
                context_kwargs["user_agent"] = user_agent
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function(
                "() => typeof window.SentinelSDK !== 'undefined' && typeof window.SentinelSDK.token === 'function'",
                timeout=min(timeout_ms, 15000),
            )
            token = page.evaluate(
                """async (flowName) => {
                    return await window.SentinelSDK.token(flowName);
                }""",
                flow,
            )
            token = (token or "").strip()
            if not token:
                print("sentinel token missing", file=sys.stderr)
                sys.exit(4)
            sys.stdout.write(token)
            sys.stdout.flush()
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
'''


class SentinelSolver:
    def solve(self, challenge: SentinelPayload | Dict[str, Any]) -> str:
        raise NotImplementedError("sentinel solver not implemented")


class NullSentinelSolver(SentinelSolver):
    def solve(self, challenge: SentinelPayload | Dict[str, Any]) -> str:
        _ = challenge
        return ""


@dataclass
class EnvironmentSentinelSolver(SentinelSolver):
    env_key: str = "SENTINEL_TOKEN"

    def solve(self, challenge: SentinelPayload | Dict[str, Any]) -> str:
        payload = coerce_payload(challenge)
        flow = flow_name_for_payload(payload)
        for env_key in sentinel_env_candidates(flow, self.env_key):
            token = os.getenv(env_key, "").strip()
            if token:
                return token
        return ""


@dataclass
class BrowserSentinelSolver(SentinelSolver):
    timeout_ms: int = 45000
    logger: Optional[callable] = None
    python_env_key: str = "SENTINEL_PYTHON"

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)

    def _find_python(self) -> str:
        configured = os.getenv(self.python_env_key, "").strip()
        if configured:
            if os.path.exists(configured) or shutil.which(configured):
                return configured
        for candidate in (sys.executable, shutil.which("python3"), shutil.which("python")):
            if candidate:
                return candidate
        raise RuntimeError("python runtime not found for sentinel browser helper")

    def _page_url(self, payload: SentinelPayload) -> str:
        override = os.getenv("SENTINEL_BROWSER_PAGE_URL", "").strip()
        if override:
            return override
        explicit = str(payload.data.get("page_url") or payload.url or "").strip()
        if explicit:
            return explicit
        return DEFAULT_PAGE_URLS.get(flow_name_for_payload(payload), DEFAULT_PAGE_URLS["oauth_create_account"])

    def solve(self, challenge: SentinelPayload | Dict[str, Any]) -> str:
        payload = coerce_payload(challenge)
        flow = flow_name_for_payload(payload)
        page_url = self._page_url(payload)
        timeout_ms = int(payload.data.get("timeout_ms") or os.getenv("SENTINEL_BROWSER_TIMEOUT_MS", self.timeout_ms))
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["SENTINEL_BROWSER_FLOW"] = flow
        env["SENTINEL_BROWSER_PAGE_URL"] = page_url
        if payload.data.get("proxy"):
            env["SENTINEL_BROWSER_PROXY"] = str(payload.data["proxy"])
        if payload.data.get("user_agent"):
            env["SENTINEL_BROWSER_UA"] = str(payload.data["user_agent"])
        env.setdefault("SENTINEL_BROWSER_TIMEOUT_MS", str(timeout_ms))

        script_path = ""
        try:
            python_runtime = self._find_python()
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="dan-sentinel-browser-",
                suffix=".py",
                delete=False,
            ) as tmp:
                tmp.write(BROWSER_HELPER_SCRIPT)
                script_path = tmp.name
            proc = subprocess.run(
                [python_runtime, script_path, flow],
                capture_output=True,
                text=True,
                env=env,
                timeout=max(int(timeout_ms / 1000) + 10, 20),
            )
            token = (proc.stdout or "").strip()
            if proc.returncode == 0 and token:
                return token
            stderr = (proc.stderr or "").strip()
            if stderr:
                self._log(f"sentinel browser helper failed: {stderr}")
            return ""
        except Exception as exc:
            self._log(f"sentinel browser helper failed: {exc}")
            return ""
        finally:
            if script_path:
                try:
                    os.remove(script_path)
                except OSError:
                    pass


@dataclass
class CompositeSentinelSolver(SentinelSolver):
    solvers: Sequence[SentinelSolver]

    def solve(self, challenge: SentinelPayload | Dict[str, Any]) -> str:
        payload = coerce_payload(challenge)
        for solver in self.solvers:
            token = (solver.solve(payload) or "").strip()
            if token:
                return token
        return ""



def coerce_payload(challenge: SentinelPayload | Dict[str, Any]) -> SentinelPayload:
    if isinstance(challenge, SentinelPayload):
        return challenge
    if isinstance(challenge, dict):
        return SentinelPayload(
            kind=str(challenge.get("kind") or ""),
            url=str(challenge.get("url") or ""),
            data=dict(challenge.get("data") or {}),
        )
    raise TypeError(f"unsupported sentinel payload: {type(challenge)!r}")



def normalize_flow_name(kind: str) -> str:
    raw = (kind or "").strip()
    if not raw:
        return "oauth_create_account"
    return FLOW_KIND_MAP.get(raw, raw)



def flow_name_for_payload(payload: SentinelPayload) -> str:
    return normalize_flow_name(payload.kind)



def sentinel_env_candidates(flow: str, base_key: str = "SENTINEL_TOKEN") -> List[str]:
    flow_key = flow.upper().replace("-", "_").replace(".", "_").replace("/", "_")
    return [f"{base_key}_{flow_key}", base_key]



def build_sentinel_payload(kind: str, url: str = "", data: Dict[str, Any] | None = None) -> SentinelPayload:
    return SentinelPayload(kind=kind, url=url, data=data or {})
