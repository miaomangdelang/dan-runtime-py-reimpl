#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", help="Path to Go binary")
    args = ap.parse_args()

    if not shutil.which("go"):
        print("go not found in PATH", file=sys.stderr)
        return 2

    cp = subprocess.run(
        ["go", "version", "-m", args.binary],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(cp.stdout.strip())
    return cp.returncode


if __name__ == "__main__":
    sys.exit(main())

