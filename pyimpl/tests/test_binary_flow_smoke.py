import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from pyimpl.danapp.app import App
from pyimpl.danapp.config import Config
from pyimpl.danapp.mailbox import Mailbox, MailboxClient
from pyimpl.danapp.register_flow import OpenAIRegistrationRunner


class FakeMailboxClient(MailboxClient):
    def create_mailbox(self, domain_options):
        return Mailbox(
            address="demo@example.com",
            domain="example.com",
            mailbox_id="demo@example.com",
            password="mailbox-pass",
        )

    def snapshot_message_ids(self, mailbox):
        return set()

    def fetch_otp(self, mailbox, timeout_sec, *, after_ids=None, disallow_codes=None, expected_recipient=""):
        return "123456"


class RegistrationHTTPStub:
    def __init__(self):
        self.calls = []
        self.cookies = {"login_session": ""}

    def cookie_value(self, name: str) -> str:
        return self.cookies.get(name, "")

    def request(self, opt):
        self.calls.append((opt.method, opt.url, dict(opt.headers or {}), opt.body))
        path = urlparse(opt.url).path
        if opt.url == "https://chatgpt.com/":
            return 200, {"X-Final-URL": opt.url}, b"<html></html>"
        if opt.url.endswith("/api/auth/csrf"):
            return 200, {"X-Final-URL": opt.url}, b'{"csrfToken":"csrf-123"}'
        if "/api/auth/signin/openai" in opt.url:
            return 200, {"X-Final-URL": opt.url}, b'{"url":"https://auth.openai.com/oauth/authorize?x=1"}'
        if "/oauth/authorize" in opt.url and "code=" not in opt.url:
            return 200, {"X-Final-URL": "https://auth.openai.com/log-in"}, b""
        if path == "/api/accounts/user/register":
            return 200, {"X-Final-URL": opt.url}, b"{}"
        if path == "/api/accounts/email-otp/send":
            return 200, {"X-Final-URL": opt.url}, b'{"status":"ok"}'
        if path == "/api/accounts/email-otp/validate":
            return 200, {"X-Final-URL": opt.url}, b'{"continue_url":"https://chatgpt.com/about-you"}'
        if path == "/api/accounts/create_account":
            return 200, {"X-Final-URL": opt.url}, b'{"redirect_url":"https://chatgpt.com/callback?code=reg-code","account_id":"acct-1"}'
        if path == "/callback":
            return 200, {"X-Final-URL": opt.url}, b""
        if path == "/api/auth/session":
            return 200, {"X-Final-URL": opt.url}, json.dumps(
                {"sessionToken": "stk-reg", "user": {"id": "acct-1"}}
            ).encode()
        raise AssertionError(f"unexpected request: {opt.method} {opt.url}")


class OAuthHTTPStub(RegistrationHTTPStub):
    def request(self, opt):
        path = urlparse(opt.url).path
        if path == "/api/accounts/authorize/continue":
            self.calls.append((opt.method, opt.url, dict(opt.headers or {}), opt.body))
            return 200, {"X-Final-URL": opt.url}, b'{"continue_url":"https://auth.openai.com/log-in/password"}'
        if path == "/api/accounts/password/verify":
            self.calls.append((opt.method, opt.url, dict(opt.headers or {}), opt.body))
            return 200, {"X-Final-URL": opt.url}, b'{"continue_url":"https://chatgpt.com/callback?code=oauth-code"}'
        if path == "/oauth/token":
            self.calls.append((opt.method, opt.url, dict(opt.headers or {}), opt.body))
            return 200, {"X-Final-URL": opt.url}, b'{"access_token":"atk-oauth","refresh_token":"rtk-oauth","id_token":"idk-oauth","token_type":"Bearer"}'
        return super().request(opt)


class BinaryFlowSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="dan-pyimpl-test-"))
        self._sentinel_env = {
            "SENTINEL_TOKEN_USERNAME_PASSWORD_CREATE": "tok-register",
            "SENTINEL_TOKEN_OAUTH_CREATE_ACCOUNT": "tok-about",
            "SENTINEL_TOKEN_AUTHORIZE_CONTINUE": "tok-continue",
            "SENTINEL_TOKEN_PASSWORD_VERIFY": "tok-password",
            "SENTINEL_TOKEN_EMAIL_OTP_VALIDATE": "tok-otp",
        }
        self._old_env = {key: os.environ.get(key) for key in self._sentinel_env}
        os.environ.update(self._sentinel_env)
        self.cfg = Config(
            ak_file=str(self.tmpdir / "ak.txt"),
            rk_file=str(self.tmpdir / "rk.txt"),
            token_json_dir=str(self.tmpdir / "tokens"),
            upload_api_url="",
            oauth_issuer="https://auth.openai.com",
            oauth_client_id="app_test",
            oauth_redirect_uri="http://localhost:1455/auth/callback",
            enable_oauth=True,
            oauth_required=True,
        )
        self.app = App(self.cfg)
        self.app.allow_network = True
        self.runner = OpenAIRegistrationRunner(self.app)
        self.mailbox = FakeMailboxClient()

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmpdir)

    def _build_session(self, http_stub):
        session = self.runner.new_session(app=self.app, http=http_stub, mailbox_client=self.mailbox)
        session.mailbox = self.mailbox.create_mailbox([])
        session.email = session.mailbox.address
        session.password = "openai-pass"
        session.profile_name = "Demo User"
        session.birthdate = "1990-01-01"
        return session

    def test_registration_flow_matches_binary_shape(self):
        http = RegistrationHTTPStub()
        session = self._build_session(http)
        session.run_register()
        tokens = session._tokens_from_session()
        token_payload = self.app.build_token_json_data(
            email=session.email,
            password=session.password,
            account_id=session.account_id,
            tokens=tokens,
            session_data=session.session_data,
            trace=session.trace,
        )
        token_path = self.app.save_codex_tokens(email=session.email, token_json=token_payload, tokens=tokens)
        result = self.app.make_account_result(
            email=session.email,
            password=session.password,
            mailbox_password=session.mailbox.password,
            account_id=session.account_id,
            token_path=token_path,
            oauth_ok=True,
        )
        self.app.output_path = str(self.tmpdir / "registered_accounts.txt")
        self.app.append_result(result)

        self.assertEqual(session.account_id, "acct-1")
        self.assertTrue(Path(token_path).exists())
        self.assertIn(
            "demo@example.com----openai-pass----mailbox-pass----oauth=ok",
            Path(self.app.output_path).read_text(encoding="utf-8"),
        )
        register_payload = json.loads(http.calls[4][3].decode("utf-8"))
        create_payload = json.loads(http.calls[7][3].decode("utf-8"))
        self.assertEqual(register_payload["username"], "demo@example.com")
        self.assertEqual(register_payload["password"], "openai-pass")
        self.assertEqual(create_payload["name"], "Demo User")
        self.assertEqual(create_payload["birthdate"], "1990-01-01")
        self.assertEqual(create_payload["openai-sentinel-token"], "tok-about")
        self.assertEqual(http.calls[5][0], "GET")
        self.assertTrue(http.calls[5][1].endswith("/api/accounts/email-otp/send"))

    def test_oauth_flow_exchanges_code_after_password_verify(self):
        http = OAuthHTTPStub()
        session = self._build_session(http)
        session.run_register()
        tokens = self.runner.perform_codex_oauth_with_retry(session)

        self.assertEqual(tokens.access_token, "atk-oauth")
        self.assertEqual(tokens.refresh_token, "rtk-oauth")
        self.assertEqual(tokens.id_token, "idk-oauth")
        oauth_urls = [url for _method, url, _headers, _body in http.calls if "/oauth/" in url or "/api/accounts/authorize/continue" in url or "/api/accounts/password/verify" in url]
        self.assertIn("https://chatgpt.com/api/accounts/authorize/continue", oauth_urls)
        self.assertIn("https://chatgpt.com/api/accounts/password/verify", oauth_urls)
        self.assertIn("https://auth.openai.com/oauth/token", oauth_urls)


if __name__ == "__main__":
    unittest.main()
