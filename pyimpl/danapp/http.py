import os
import ssl
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .util import decode_json_bytes


@dataclass
class RequestOptions:
    method: str
    url: str
    headers: Optional[Dict[str, str]] = None
    body: bytes = b""
    timeout_sec: float = 30.0


class HTTPClient:
    def __init__(
        self,
        *,
        proxy: str = "",
        disable_proxy: bool = False,
        use_env_proxy: bool = False,
        cookie_jar: Optional[CookieJar] = None,
        user_agent: str = "dan-python/0.1",
        insecure_tls: bool = False,
    ) -> None:
        self.proxy = proxy
        self.disable_proxy = disable_proxy
        self.use_env_proxy = use_env_proxy
        self.cookie_jar = cookie_jar or CookieJar()
        self.user_agent = user_agent
        self.insecure_tls = insecure_tls or os.getenv("DAN_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes", "on"}
        self._opener = self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers = [urllib.request.HTTPCookieProcessor(self.cookie_jar)]
        if self.insecure_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        proxy_handler = None
        if self.disable_proxy:
            proxy_handler = urllib.request.ProxyHandler({})
        elif self.proxy:
            proxy_handler = urllib.request.ProxyHandler(
                {"http": self.proxy, "https": self.proxy}
            )
        elif not self.use_env_proxy:
            proxy_handler = urllib.request.ProxyHandler({})
        if proxy_handler is not None:
            handlers.append(proxy_handler)
        return urllib.request.build_opener(*handlers)

    def request(self, opt: RequestOptions) -> Tuple[int, Dict[str, str], bytes]:
        headers = dict(opt.headers or {})
        headers.setdefault("User-Agent", self.user_agent)
        req = urllib.request.Request(
            opt.url,
            data=opt.body or None,
            headers=headers,
            method=opt.method.upper(),
        )
        try:
            with self._opener.open(req, timeout=opt.timeout_sec) as resp:
                out_headers = dict(resp.headers.items())
                out_headers["X-Final-URL"] = resp.geturl()
                return resp.status, out_headers, resp.read()
        except urllib.error.HTTPError as exc:
            out_headers = dict(exc.headers.items())
            out_headers["X-Final-URL"] = exc.geturl()
            return exc.code, out_headers, exc.read()

    def json_request(self, opt: RequestOptions) -> Tuple[int, Dict[str, str], bytes]:
        if opt.headers is None:
            opt.headers = {}
        opt.headers["Content-Type"] = "application/json"
        return self.request(opt)

    def form_request(self, opt: RequestOptions) -> Tuple[int, Dict[str, str], bytes]:
        if opt.headers is None:
            opt.headers = {}
        opt.headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self.request(opt)

    def request_json(self, opt: RequestOptions) -> Tuple[int, Dict[str, str], Dict[str, Any], bytes]:
        status, headers, body = self.request(opt)
        return status, headers, decode_json_bytes(body), body

    def json_post(self, url: str, payload: bytes, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], Dict[str, Any], bytes]:
        status, out_headers, body = self.json_request(
            RequestOptions(method="POST", url=url, headers=headers, body=payload)
        )
        return status, out_headers, decode_json_bytes(body), body

    def form_post(self, url: str, payload: bytes, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], Dict[str, Any], bytes]:
        status, out_headers, body = self.form_request(
            RequestOptions(method="POST", url=url, headers=headers, body=payload)
        )
        return status, out_headers, decode_json_bytes(body), body

    def cookie_value(self, name: str) -> str:
        for cookie in self.cookie_jar:
            if cookie.name == name:
                return cookie.value
        return ""
