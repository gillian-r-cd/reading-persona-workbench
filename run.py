#!/usr/bin/env python3
"""Start the local workbench server."""
from __future__ import annotations

import argparse

from wb.api.server import serve


def main() -> None:
    ap = argparse.ArgumentParser(description="非虚构阅读人格工作台（本地）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--vault", default=None)
    args = ap.parse_args()
    serve(args.host, args.port, args.vault)


if __name__ == "__main__":
    main()
