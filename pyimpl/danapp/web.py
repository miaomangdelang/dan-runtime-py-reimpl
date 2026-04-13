import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from .app import App
from .config import WebConfig, load_config, load_web_config, save_web_config

COOKIE_NAME = "cpam_web_token"
LOG_LIMIT = 200


def _now() -> float:
    return time.time()


def to_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def to_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def optional_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def decode_payload(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = to_non_negative_int(handler.headers.get("Content-Length"), 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    content_type = handler.headers.get("Content-Type", "")
    if "application/json" in content_type:
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    if "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {k: v[-1] if len(v) == 1 else v for k, v in parsed.items()}
    return {}


def write_json(handler: BaseHTTPRequestHandler, obj: Dict[str, Any], code: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def write_html(handler: BaseHTTPRequestHandler, html: str, code: int = 200) -> None:
    body = html.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@dataclass
class ThreadState:
    thread_id: str
    status: str = "idle"
    step: str = ""
    email: str = ""
    note: str = ""
    updated_at: float = 0.0


@dataclass
class BatchRun:
    run_id: str
    mode: str
    total_accounts: int
    max_workers: int
    retries: int
    started_at: float
    finished_at: float = 0.0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    actual_active: int = 0
    status: str = "running"
    status_message: str = ""
    register_summary: str = ""
    register_trace: List[str] = field(default_factory=list)


class Manager:
    def __init__(
        self,
        app: App,
        web_config: WebConfig,
        *,
        web_config_path: str = "config/web_config.json",
    ) -> None:
        self.app = app
        self.web_config = web_config
        self.web_config_path = web_config_path
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.started_at = _now()
        self.last_fill = 0.0
        self.last_manual_register = 0.0
        self.status_message = "idle"
        self.display_log: List[Dict[str, Any]] = []
        self.thread_states: Dict[str, ThreadState] = {}
        self.current_run: Optional[BatchRun] = None
        self.pending_manual = False
        self.pending_reconcile = False
        self.scheduler_started = False
        self.retry_loop_started = False

    def public_config(self) -> Dict[str, Any]:
        return {
            "target_min_tokens": self.web_config.target_min_tokens,
            "auto_fill_start_gap": self.web_config.auto_fill_start_gap,
            "check_interval_minutes": self.web_config.check_interval_minutes,
            "manual_default_threads": self.web_config.manual_default_threads,
            "manual_register_retries": self.web_config.manual_register_retries,
            "client_notice": self.web_config.client_notice,
            "minimum_client_version": self.web_config.minimum_client_version,
            "enabled_email_domains": list(self.web_config.enabled_email_domains),
            "mail_domain_options": list(self.web_config.mail_domain_options),
            "default_proxy": self.web_config.default_proxy,
            "use_registration_proxy": self.web_config.use_registration_proxy,
            "port": self.web_config.port,
            "allow_network": self.app.allow_network,
            "has_web_token": bool(self.web_config.web_token),
            "has_client_api_token": bool(self.web_config.client_api_token),
            "has_cpa_token": bool(self.web_config.cpa_token),
            "has_mail_api_key": bool(self.web_config.mail_api_key),
        }

    def setup_required(self) -> bool:
        return not self.app.has_registration_runner() and not self.app.allow_network

    def add_log(self, event: str, message: str, **extra: Any) -> None:
        entry = {
            "ts": _now(),
            "event": event,
            "message": message,
            **extra,
        }
        with self.lock:
            self.display_log.append(entry)
            if len(self.display_log) > LOG_LIMIT:
                self.display_log = self.display_log[-LOG_LIMIT:]

    def set_display_log(self, message: str) -> None:
        self.add_log("display", message)

    def update_thread_state(
        self,
        thread_id: str,
        *,
        status: str,
        step: str,
        email: str = "",
        note: str = "",
    ) -> None:
        with self.lock:
            state = self.thread_states.get(thread_id) or ThreadState(thread_id=thread_id)
            state.status = status
            state.step = step
            state.email = email
            state.note = note
            state.updated_at = _now()
            self.thread_states[thread_id] = state

    def reset_batch_progress(self) -> None:
        with self.lock:
            self.thread_states = {}

    def _token_dir(self) -> Path:
        return Path(self.app.config.token_json_dir or "codex_tokens")

    def _token_files(self) -> List[Path]:
        token_dir = self._token_dir()
        if not token_dir.is_dir():
            return []
        return sorted(p for p in token_dir.iterdir() if p.is_file() and p.suffix == ".json")

    def count_cpa_files(self) -> int:
        return len(self._token_files())

    def cached_cpa_status(self) -> Dict[str, Any]:
        total = self.count_cpa_files()
        return {
            "ok": total > 0,
            "pending": total == 0,
            "total_accounts": total,
            "message": "cpa status pending" if total == 0 else "local token inventory ready",
        }

    def compute_stats(self) -> Dict[str, Any]:
        files = self._token_files()
        now = _now()
        total = len(files)
        started_24h = sum(1 for path in files if now - path.stat().st_mtime <= 86400)
        gap = max(self.web_config.target_min_tokens - total, 0)
        currentfill = self.current_run.total_accounts if self.current_run and self.current_run.status == "running" else 0
        return {
            "total_accounts": total,
            "started_24h": started_24h,
            "target_min_tokens": self.web_config.target_min_tokens,
            "currentfill": currentfill,
            "estimated_before": total,
            "estimated_after": total + currentfill,
            "status_message": self.status_message,
            "last_fill": self.last_fill,
            "last_manual_register": self.last_manual_register,
            "gap": gap,
            "scheduler_start": self.scheduler_started,
            "setup_required": self.setup_required(),
        }

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "stats": self.compute_stats(),
                "run": asdict(self.current_run) if self.current_run else None,
                "threads": [asdict(v) for _, v in sorted(self.thread_states.items())],
                "logs": list(self.display_log[-80:]),
                "pendingManual": self.pending_manual,
                "pendingRecon": self.pending_reconcile,
                "cpa": self.cached_cpa_status(),
            }

    def status(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "setup_required": self.setup_required(),
            "config": self.public_config(),
            **self.snapshot(),
        }

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self.web_config.target_min_tokens = to_non_negative_int(
                payload.get("target_min_tokens"), self.web_config.target_min_tokens
            )
            self.web_config.auto_fill_start_gap = to_non_negative_int(
                payload.get("auto_fill_start_gap"), self.web_config.auto_fill_start_gap
            )
            self.web_config.check_interval_minutes = to_positive_int(
                payload.get("check_interval_minutes"), self.web_config.check_interval_minutes
            )
            self.web_config.manual_default_threads = to_positive_int(
                payload.get("manual_default_threads"), self.web_config.manual_default_threads or 1
            )
            self.web_config.manual_register_retries = to_non_negative_int(
                payload.get("manual_register_retries"), self.web_config.manual_register_retries
            )
            self.web_config.client_notice = optional_string(
                payload.get("client_notice"), self.web_config.client_notice
            )
            self.web_config.minimum_client_version = optional_string(
                payload.get("minimum_client_version"), self.web_config.minimum_client_version
            )
            if isinstance(payload.get("enabled_email_domains"), list):
                self.web_config.enabled_email_domains = [
                    str(v).strip() for v in payload["enabled_email_domains"] if str(v).strip()
                ]
            if isinstance(payload.get("mail_domain_options"), list):
                self.web_config.mail_domain_options = [
                    str(v).strip() for v in payload["mail_domain_options"] if str(v).strip()
                ]
            if "default_proxy" in payload:
                self.web_config.default_proxy = optional_string(
                    payload.get("default_proxy"), self.web_config.default_proxy
                )
            if "use_registration_proxy" in payload:
                self.web_config.use_registration_proxy = bool(payload.get("use_registration_proxy"))
            save_web_config(self.web_config_path, self.web_config)
        self.add_log("config", "config updated")
        return self.public_config()

    def start_scheduler(self) -> None:
        with self.lock:
            if self.scheduler_started:
                return
            self.scheduler_started = True
        self.add_log("scheduler_start", "scheduler started")

        def loop() -> None:
            while not self.stop_event.wait(max(self.web_config.check_interval_minutes, 1) * 60):
                if self.setup_required():
                    continue
                stats = self.compute_stats()
                if stats["gap"] >= max(self.web_config.auto_fill_start_gap, 1):
                    self.fill_to_target(trigger="scheduler")

        threading.Thread(target=loop, name="dan-web-scheduler", daemon=True).start()

    def start_pending_token_retry_loop(self) -> None:
        with self.lock:
            if self.retry_loop_started:
                return
            self.retry_loop_started = True

        def loop() -> None:
            while not self.stop_event.wait(max(self.web_config.check_interval_minutes, 1) * 60):
                if self.stop_event.is_set():
                    return
                try:
                    details = self.app.upload_pending_tokens_detailed()
                    if details:
                        ok_count = sum(1 for item in details if item.get("ok"))
                        self.add_log("cpa_upload_retry", f"pending upload retry: {ok_count}/{len(details)}")
                    else:
                        self.add_log("reconcile_skip", "pending token retry loop heartbeat")
                except Exception as exc:
                    self.add_log("cpa_upload_fail", str(exc))

        threading.Thread(target=loop, name="dan-web-pending", daemon=True).start()

    def trigger_reconcile(self) -> Dict[str, Any]:
        with self.lock:
            if self.pending_reconcile:
                return {"ok": True, "message": "reconcile already running"}
            self.pending_reconcile = True
            self.status_message = "manual_reconcile"
        self.add_log("manual_reconcile", "reconcile started")

        def worker() -> None:
            try:
                details = self.app.upload_pending_tokens_detailed()
                total = self.count_cpa_files()
                ok_count = sum(1 for item in details if item.get("ok"))
                self.add_log(
                    "reconcile_done",
                    f"reconcile complete: {total} token files, uploads ok={ok_count}/{len(details)}",
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.add_log("reconcile_fail", str(exc))
            finally:
                with self.lock:
                    self.pending_reconcile = False
                    self.status_message = "idle"

        threading.Thread(target=worker, name="dan-web-reconcile", daemon=True).start()
        return {"ok": True, "message": "reconcile started"}

    def manual_register(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        total = to_positive_int(payload.get("count") or payload.get("total_accounts"), 1)
        max_workers = to_positive_int(
            payload.get("max_workers"), self.web_config.manual_default_threads or 1
        )
        retries = to_non_negative_int(
            payload.get("retries"), self.web_config.manual_register_retries
        )
        domains = payload.get("domains")
        if not isinstance(domains, list) or not domains:
            domains = self.web_config.enabled_email_domains or self.web_config.mail_domain_options
        self.last_manual_register = _now()
        return self._start_run(
            mode="manual_register",
            total_accounts=total,
            max_workers=max_workers,
            retries=retries,
            domains=domains,
        )

    def fill_to_target(self, *, trigger: str = "manual") -> Dict[str, Any]:
        stats = self.compute_stats()
        gap = max(stats["target_min_tokens"] - stats["total_accounts"], 0)
        if gap <= 0:
            self.add_log("fill", "target reached")
            return {"ok": True, "message": "target reached", "stats": stats}
        self.last_fill = _now()
        return self._start_run(
            mode="fill",
            total_accounts=gap,
            max_workers=1,
            retries=self.web_config.manual_register_retries,
            domains=self.web_config.enabled_email_domains or self.web_config.mail_domain_options,
            trigger=trigger,
        )

    def _start_run(
        self,
        *,
        mode: str,
        total_accounts: int,
        max_workers: int,
        retries: int,
        domains: Sequence[str],
        trigger: str = "",
    ) -> Dict[str, Any]:
        with self.lock:
            if self.current_run and self.current_run.status == "running":
                return {"ok": False, "message": "batch already running", "run": asdict(self.current_run)}
            if self.setup_required():
                return {"ok": False, "message": "setup required", "setup_required": True}
            run = BatchRun(
                run_id=uuid.uuid4().hex[:12],
                mode=mode,
                total_accounts=total_accounts,
                max_workers=max_workers,
                retries=retries,
                started_at=_now(),
                status_message=mode,
            )
            self.current_run = run
            self.status_message = mode
            self.pending_manual = mode == "manual_register"
            self.reset_batch_progress()
        self.add_log(mode, f"{mode} started", total_accounts=total_accounts, trigger=trigger)
        threading.Thread(
            target=self._run_batch,
            args=(run, list(domains)),
            name=f"dan-web-{mode}",
            daemon=True,
        ).start()
        return {"ok": True, "message": "started", "run": asdict(run)}

    def _run_batch(self, run: BatchRun, domains: Sequence[str]) -> None:
        thread_id = "T01"
        try:
            for index in range(run.total_accounts):
                attempt = 0
                while True:
                    self.update_thread_state(
                        thread_id,
                        status="running",
                        step="register",
                        note=f"{index + 1}/{run.total_accounts}",
                    )
                    run.actual_active = 1
                    try:
                        result = self.app.register_one(domains)
                        run.success += 1
                        self.update_thread_state(
                            thread_id,
                            status="success",
                            step="register_success",
                            email=result.email if result else "",
                            note=result.token_path if result else "",
                        )
                        self.add_log(
                            "register_success",
                            "register success",
                            email=result.email if result else "",
                            token_path=result.token_path if result else "",
                        )
                        break
                    except Exception as exc:
                        attempt += 1
                        trace = f"item={index + 1} attempt={attempt} err={exc}"
                        run.register_trace.append(trace)
                        self.add_log("register_trace", trace)
                        if attempt > run.retries:
                            run.failed += 1
                            self.update_thread_state(
                                thread_id,
                                status="failed",
                                step="register_failed",
                                note=str(exc),
                            )
                            break
                run.actual_active = 0
        finally:
            self.finish_run(run)

    def finish_run(self, run: BatchRun) -> None:
        with self.lock:
            run.finished_at = _now()
            run.status = "done"
            run.status_message = "register_summary"
            run.register_summary = (
                f"success={run.success} failed={run.failed} skipped={run.skipped}"
            )
            self.status_message = run.register_summary
            self.pending_manual = False
            self.current_run = run
        self.add_log("register_summary", run.register_summary, run_id=run.run_id)

    def close(self) -> None:
        self.stop_event.set()


class Server:
    def __init__(
        self,
        *,
        app_config_path: str = "config.json",
        web_config_path: str = "config/web_config.json",
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        mock_register: bool = False,
        allow_network: bool = False,
    ) -> None:
        self.app_config_path = app_config_path
        self.web_config_path = web_config_path
        self.app = App(load_config(app_config_path))
        self.app.allow_network = allow_network
        if mock_register:
            self.app.use_mock_registration()
        elif allow_network:
            self.app.use_live_registration(web_config_path)
        self.manager = Manager(
            self.app,
            load_web_config(web_config_path),
            web_config_path=web_config_path,
        )
        self.host = host
        self.port = port or self.manager.web_config.port

    def auth_ok(self, headers: Dict[str, str]) -> bool:
        expected = self.manager.web_config.web_token
        if not expected:
            return True
        authz = headers.get("Authorization", "")
        if authz.startswith("Bearer ") and authz.split(" ", 1)[1] == expected:
            return True
        header_token = headers.get("X-Auth-Token", "")
        if header_token == expected:
            return True
        cookie_header = headers.get("Cookie", "")
        if cookie_header:
            jar = cookies.SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(COOKIE_NAME)
            if morsel and morsel.value == expected:
                return True
        return False

    def page_auth_ok(self, headers: Dict[str, str]) -> bool:
        return self.auth_ok(headers)

    def bootstrap_payload(self, headers: Dict[str, str]) -> Dict[str, Any]:
        return {
            "ok": True,
            "authenticated": self.auth_ok(headers),
            "management_root": "/management.html",
            "setup_required": self.manager.setup_required(),
            "client_notice": self.manager.web_config.client_notice,
            "minimum_client_version": self.manager.web_config.minimum_client_version,
            "config": self.manager.public_config(),
            "status": self.manager.status(),
        }

    def render_management_html(self) -> str:
        return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>dan-web (python reimpl)</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; background: #111827; color: #e5e7eb; }
    textarea, input, button { font: inherit; }
    .row { display: flex; gap: 1rem; margin: 0.5rem 0; }
    .card { background: #1f2937; padding: 1rem; border-radius: 12px; margin-bottom: 1rem; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0b1220; padding: 1rem; border-radius: 8px; }
    input { padding: 0.5rem; width: 18rem; }
    button { padding: 0.5rem 0.8rem; }
  </style>
</head>
<body>
  <h1>dan-web (python reimpl)</h1>
  <div class="card">
    <div class="row">
      <input id="token" placeholder="web token">
      <button onclick="login()">登录</button>
      <button onclick="refreshStatus()">刷新状态</button>
      <button onclick="fill()">Fill</button>
      <button onclick="manualRegister()">Manual Register</button>
      <button onclick="reconcile()">Reconcile</button>
    </div>
    <div id="message"></div>
  </div>
  <div class="card">
    <pre id="status">loading...</pre>
  </div>
  <script>
    async function post(url, payload) {
      const res = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify(payload || {})
      });
      return await res.json();
    }
    async function login() {
      const token = document.getElementById("token").value;
      const data = await post("/api/login", {token});
      document.getElementById("message").textContent = JSON.stringify(data, null, 2);
      await refreshStatus();
    }
    async function refreshStatus() {
      const res = await fetch("/api/status", {credentials: "same-origin"});
      const data = await res.json();
      document.getElementById("status").textContent = JSON.stringify(data, null, 2);
    }
    async function fill() {
      document.getElementById("message").textContent = JSON.stringify(await post("/api/fill", {}), null, 2);
      await refreshStatus();
    }
    async function manualRegister() {
      document.getElementById("message").textContent = JSON.stringify(await post("/api/manual-register", {count: 1}), null, 2);
      await refreshStatus();
    }
    async function reconcile() {
      document.getElementById("message").textContent = JSON.stringify(await post("/api/reconcile", {}), null, 2);
      await refreshStatus();
    }
    refreshStatus();
  </script>
</body>
</html>"""

    def make_handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "dan-web/pyimpl"

            @property
            def app_server(self) -> "Server":
                return parent

            def log_message(self, fmt: str, *args: Any) -> None:
                parent.manager.add_log("http", fmt % args)

            def _json(self, obj: Dict[str, Any], code: int = 200) -> None:
                write_json(self, obj, code=code)

            def _html(self, html: str, code: int = 200) -> None:
                write_html(self, html, code=code)

            def _set_auth_cookie(self, token: str) -> None:
                cookie = cookies.SimpleCookie()
                cookie[COOKIE_NAME] = token
                cookie[COOKIE_NAME]["path"] = "/"
                cookie[COOKIE_NAME]["httponly"] = True
                self.send_header("Set-Cookie", cookie.output(header="").strip())

            def _clear_auth_cookie(self) -> None:
                cookie = cookies.SimpleCookie()
                cookie[COOKIE_NAME] = ""
                cookie[COOKIE_NAME]["path"] = "/"
                cookie[COOKIE_NAME]["max-age"] = 0
                self.send_header("Set-Cookie", cookie.output(header="").strip())

            def _require_auth(self) -> bool:
                if parent.auth_ok(self.headers):
                    return True
                self._json({"ok": False, "message": "access denied"}, code=401)
                return False

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path in ("/", "/management.html"):
                    return self._html(parent.render_management_html())
                if path == "/api/status":
                    if not self._require_auth():
                        return
                    return self._json(parent.manager.status())
                if path == "/favicon.ico":
                    return self._json({"ok": False, "message": "not found"}, code=404)
                return self._json({"ok": False, "message": "not found"}, code=404)

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                payload = decode_payload(self)
                if path == "/api/bootstrap":
                    return self._json(parent.bootstrap_payload(self.headers))
                if path == "/api/login":
                    token = optional_string(
                        payload.get("token") or payload.get("web_token"),
                        "",
                    )
                    if token != parent.manager.web_config.web_token:
                        return self._json({"ok": False, "message": "access denied"}, code=403)
                    self.send_response(200)
                    self._set_auth_cookie(token)
                    body = json.dumps({"ok": True, "message": "login success"}).encode("utf-8")
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/logout":
                    self.send_response(200)
                    self._clear_auth_cookie()
                    body = json.dumps({"ok": True, "message": "logout success"}).encode("utf-8")
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/config":
                    if not self._require_auth():
                        return
                    config = parent.manager.update_config(payload)
                    return self._json({"ok": True, "message": "config updated", "config": config})
                if path == "/api/manual-register":
                    if not self._require_auth():
                        return
                    return self._json(parent.manager.manual_register(payload))
                if path == "/api/reconcile":
                    if not self._require_auth():
                        return
                    return self._json(parent.manager.trigger_reconcile())
                if path == "/api/fill":
                    if not self._require_auth():
                        return
                    return self._json(parent.manager.fill_to_target())
                return self._json({"ok": False, "message": "not found"}, code=404)

        return Handler

    def listen_and_serve(self) -> None:
        self.manager.start_scheduler()
        self.manager.start_pending_token_retry_loop()
        httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        print(f"[dan-web] listening on http://{self.host}:{self.port}")
        try:
            httpd.serve_forever()
        finally:
            self.manager.close()
