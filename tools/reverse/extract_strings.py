#!/usr/bin/env python3
import argparse
import re
import sys


PRINTABLE = re.compile(rb"[\x20-\x7E]{4,}")

MODES = {
    "urls": re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE),
    "routes": re.compile(r"/api/[a-z0-9/_\-]+", re.IGNORECASE),
    "config": re.compile(r"\b(ak_file|rk_file|token_json_dir|upload_api_url|upload_api_token|oauth_issuer|oauth_client_id|oauth_redirect_uri|enable_oauth|oauth_required|mail_api_url|mail_api_key|cpa_base_url|cpa_token|web_token|client_api_token|minimum_client_version)\b", re.IGNORECASE),
    "sentinel": re.compile(r"\bSENTINEL_[A-Z0-9_]+\b"),
    "oauth": re.compile(r"\b(oauth|authorization|access_token|refresh_token|callback)\b", re.IGNORECASE),
}


def extract_strings(path):
    with open(path, "rb") as f:
        data = f.read()
    return [m.group(0).decode("utf-8", "ignore") for m in PRINTABLE.finditer(data)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", help="Path to binary")
    ap.add_argument("--mode", choices=sorted(MODES.keys()), default="urls")
    args = ap.parse_args()

    strings = extract_strings(args.binary)
    rx = MODES[args.mode]

    hits = []
    for s in strings:
        if rx.search(s):
            hits.append(s.strip())

    for line in sorted(set(hits)):
        print(line)


if __name__ == "__main__":
    sys.exit(main())

