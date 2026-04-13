import os
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from pyimpl.danapp.sentinel import (
    BrowserSentinelSolver,
    CompositeSentinelSolver,
    EnvironmentSentinelSolver,
    SentinelPayload,
    build_sentinel_payload,
    flow_name_for_payload,
)


class SentinelSolverTests(unittest.TestCase):
    def test_flow_name_mapping_matches_binary_aliases(self):
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("register.submit_password")), "username_password_create")
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("register.submit_password.retry")), "username_password_create")
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("register.create_account")), "oauth_create_account")
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("authorize_continue")), "authorize_continue")
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("password_verify")), "password_verify")
        self.assertEqual(flow_name_for_payload(build_sentinel_payload("email_otp_validate")), "email_otp_validate")

    def test_environment_solver_prefers_flow_specific_key(self):
        payload = build_sentinel_payload("register.submit_password")
        with mock.patch.dict(
            os.environ,
            {
                "SENTINEL_TOKEN": "generic-token",
                "SENTINEL_TOKEN_USERNAME_PASSWORD_CREATE": "flow-token",
            },
            clear=False,
        ):
            solver = EnvironmentSentinelSolver()
            self.assertEqual(solver.solve(payload), "flow-token")

    def test_browser_solver_builds_helper_command_and_env(self):
        logs = []
        solver = BrowserSentinelSolver(logger=logs.append)
        payload = SentinelPayload(
            kind="password_verify",
            url="https://auth.openai.com/log-in/password",
            data={"proxy": "http://127.0.0.1:7890", "user_agent": "UA/1.0", "timeout_ms": 12345},
        )

        captured = {}

        def fake_run(cmd, capture_output, text, env, timeout):
            captured["cmd"] = cmd
            captured["env"] = env
            captured["timeout"] = timeout
            self.assertTrue(Path(cmd[1]).name.startswith("dan-sentinel-browser-"))
            self.assertTrue(Path(cmd[1]).suffix == ".py")
            return CompletedProcess(cmd, 0, stdout="browser-token\n", stderr="")

        with mock.patch.object(solver, "_find_python", return_value="/usr/bin/python3"):
            with mock.patch("subprocess.run", side_effect=fake_run):
                token = solver.solve(payload)

        self.assertEqual(token, "browser-token")
        self.assertEqual(captured["cmd"][2], "password_verify")
        self.assertEqual(captured["env"]["SENTINEL_BROWSER_FLOW"], "password_verify")
        self.assertEqual(captured["env"]["SENTINEL_BROWSER_PAGE_URL"], "https://auth.openai.com/log-in/password")
        self.assertEqual(captured["env"]["SENTINEL_BROWSER_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(captured["env"]["SENTINEL_BROWSER_UA"], "UA/1.0")
        self.assertEqual(captured["env"]["SENTINEL_BROWSER_TIMEOUT_MS"], "12345")
        self.assertGreaterEqual(captured["timeout"], 20)
        self.assertEqual(logs, [])

    def test_composite_solver_falls_back_after_empty_env(self):
        payload = build_sentinel_payload("oauth_create_account")
        env_solver = EnvironmentSentinelSolver()
        browser_solver = mock.Mock()
        browser_solver.solve.return_value = "browser-token"
        solver = CompositeSentinelSolver([env_solver, browser_solver])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(solver.solve(payload), "browser-token")
        browser_solver.solve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
