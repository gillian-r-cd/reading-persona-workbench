#!/usr/bin/env python3
"""Vault export / import / stats for moving between machines."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wb import admin
from wb.config import Config
from wb.db import DB


def main() -> int:
    ap = argparse.ArgumentParser(description="阅读人格工作台 · 数据管理")
    ap.add_argument("--vault", default=None, help="vault 目录（默认 ./vault）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export", help="导出 vault 为 zip")
    ex.add_argument("--out", required=True)
    ex.add_argument("--with-secrets", action="store_true",
                    help="包含 API key（仅用于可信设备之间迁移）")

    im = sub.add_parser("import", help="从 zip 导入 vault")
    im.add_argument("--zip", required=True)
    im.add_argument("--force", action="store_true", help="覆盖非空 vault")

    sub.add_parser("stats", help="查看 vault 统计")

    args = ap.parse_args()
    cfg = Config(args.vault) if args.vault else Config()
    if args.cmd == "export":
        DB(cfg.db_path)
        r = admin.export_vault(cfg, args.out, include_secrets=args.with_secrets)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "import":
        r = admin.import_vault(cfg, args.zip, force=args.force)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "stats":
        db = DB(cfg.db_path)
        print(json.dumps(admin.vault_stats(db, cfg), ensure_ascii=False, indent=2))
        print(json.dumps(admin.integrity_check(db, cfg), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
