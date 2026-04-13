#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danapp import App, load_config


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mailbox API single-thread registration tool (python reimpl)"
    )
    ap.add_argument("-n", "--count", type=int, default=1, help="number of accounts to register")
    ap.add_argument("--output", default="registered_accounts.txt", help="output file")
    ap.add_argument("--config", default="config.json", help="config path")
    ap.add_argument("--web-config", default="config/web_config.json", help="web config path")
    ap.add_argument("--proxy", default="", help="explicit proxy address")
    ap.add_argument("--no-proxy", action="store_true", help="disable all proxies")
    ap.add_argument("--use-env-proxy", action="store_true", help="allow HTTPS_PROXY / ALL_PROXY")
    ap.add_argument("--domains", nargs="*", default=[], help="mail domains")
    ap.add_argument("--no-upload", action="store_true", help="skip CPA upload")
    ap.add_argument("--no-oauth", action="store_true", help="skip OAuth flow")
    ap.add_argument("--oauth-not-required", action="store_true", help="do not abort if OAuth fails")
    ap.add_argument("--allow-network", action="store_true", help="enable authorized network flows")
    ap.add_argument(
        "--mock-register",
        action="store_true",
        help="generate local mock accounts/tokens for testing dan-web and pipeline glue",
    )
    args = ap.parse_args()

    cfg = load_config(args.config)
    app = App(cfg)
    app.output_path = args.output
    app.proxy = args.proxy
    app.disable_proxy = args.no_proxy
    app.use_env_proxy = args.use_env_proxy
    app.no_upload = args.no_upload
    app.no_oauth = args.no_oauth
    app.oauth_not_required = args.oauth_not_required
    app.allow_network = args.allow_network

    if args.mock_register:
        app.use_mock_registration()
    elif app.allow_network:
        app.use_live_registration(args.web_config)
    else:
        print(
            "registration flow disabled (use --mock-register for offline testing or provide an authorized implementation)",
            file=sys.stderr,
        )
        return 1

    app.run(args.count, domains=args.domains)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
