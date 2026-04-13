#!/usr/bin/env python3
import argparse
import re
import sys


TEXT_RE = re.compile(r"^TEXT\s+(.+?)\(")
CALL_RE = re.compile(r"\bCALL\s+(.+)$")


def parse_callgraph(path):
    edges = set()
    current = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip()
            m = TEXT_RE.search(line)
            if m:
                current = m.group(1)
                continue
            m = CALL_RE.search(line)
            if m and current:
                callee = m.group(1).strip()
                edges.add((current, callee))
    return edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("objdump", help="Path to go tool objdump output")
    args = ap.parse_args()

    edges = parse_callgraph(args.objdump)
    for caller, callee in sorted(edges):
        print(f"{caller} -> {callee}")


if __name__ == "__main__":
    sys.exit(main())

