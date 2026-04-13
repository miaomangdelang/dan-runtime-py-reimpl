#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danapp import OpenAITokenRefresher, load_config, refresh_token_json_directory
from danapp.http import HTTPClient


def main() -> int:
    ap = argparse.ArgumentParser(description="Token refresh tool (python reimpl)")
    ap.add_argument("-dir", dest="dir", default="", help="token json directory")
    ap.add_argument("--config", default="config.json", help="config path")
    ap.add_argument("-proxy", dest="proxy", default="", help="explicit proxy address")
    ap.add_argument("-no-proxy", dest="no_proxy", action="store_true", help="disable all proxies")
    ap.add_argument(
        "-use-env-proxy",
        dest="use_env_proxy",
        action="store_true",
        help="allow HTTPS_PROXY / ALL_PROXY",
    )
    ap.add_argument("--allow-network", action="store_true", help="enable authorized network flows")
    args = ap.parse_args()

    cfg = load_config(args.config)
    token_dir = args.dir or cfg.token_json_dir
    if not args.allow_network:
        print("token refresh flow disabled (provide an authorized refresher first)", file=sys.stderr)
        return 1

    http = HTTPClient(
        proxy=args.proxy,
        disable_proxy=args.no_proxy,
        use_env_proxy=args.use_env_proxy,
    )
    refresh_token_json_directory(token_dir, OpenAITokenRefresher(cfg, http))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
