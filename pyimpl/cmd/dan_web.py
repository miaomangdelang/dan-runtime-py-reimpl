#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from danapp import Server


def main() -> int:
    ap = argparse.ArgumentParser(description="dan-web (python reimplementation)")
    ap.add_argument("--config", default="config.json", help="app config path")
    ap.add_argument("--web-config", default="config/web_config.json", help="web config path")
    ap.add_argument("--host", default="0.0.0.0", help="listen host")
    ap.add_argument("--port", type=int, default=0, help="override web port")
    ap.add_argument(
        "--allow-network",
        action="store_true",
        help="enable best-effort live registration flow instead of mock/offline only",
    )
    ap.add_argument(
        "--mock-register",
        action="store_true",
        help="enable local mock registration so manual-register/fill can run offline",
    )
    args = ap.parse_args()

    server = Server(
        app_config_path=args.config,
        web_config_path=args.web_config,
        host=args.host,
        port=args.port or None,
        mock_register=args.mock_register,
        allow_network=args.allow_network,
    )
    try:
        server.listen_and_serve()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
