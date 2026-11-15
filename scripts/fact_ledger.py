#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API 事实台账：记录已查证的 ModSDK API，并检查代码是否脱离台账。

台账是本地 Markdown 文件（默认 <project>/docs/fact-ledger.md），
既是给模型看的记忆外置，也是可被脚本校验的证据链。

用法:
    # 追加一条（同一 标识符+端 已存在则跳过）
    python fact_ledger.py add --project . --name GetEngineCompFactory \
        --side server --purpose "获取引擎组件工厂" \
        --usage "comp = serverApi.GetEngineCompFactory().CreateItem(playerId)" \
        --source "get_api_detail"

    # 检查代码里出现的 ModSDK 风格调用是否都在台账中
    python fact_ledger.py check --project . [--strict]

    # 列出全部
    python fact_ledger.py list --project .

退出码: 0 正常；1 check 有缺失且 --strict；2 参数/路径错误
"""

import argparse
import datetime
import os
import re
import sys

HEADER = "| 标识符 | 端 | 用途 | 用法/签名 | 来源 | 时间 |"
SEP = "| --- | --- | --- | --- | --- | --- |"

API_PREFIX = (
    "Get", "Set", "Add", "Remove", "Create", "Destroy", "Spawn", "Play",
    "Show", "Hide", "Open", "Close", "Register", "UnRegister", "Unregister",
    "Listen", "Broadcast", "Notify", "Trigger", "Call", "Is", "Has", "Can",
    "Enable", "Disable", "Reset", "Update", "Sync", "Send", "Do", "Use",
)
CALL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{3,})\s*\(")
MOD_IMPORT_RE = re.compile(r"(?:from|import)\s+(mod\.[A-Za-z0-9_.]+)")


def ledger_path(project, explicit=None):
    return explicit or os.path.join(project, "docs", "fact-ledger.md")


def load(path):
    """返回 {(name, side): [cells]}。"""
    entries = {}
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            if cells[0] in ("标识符", "---") or set(cells[0]) <= set("-: "):
                continue
            entries[(cells[0], cells[1] if len(cells) > 1 else "")] = cells
    return entries


def esc(text):
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def cmd_add(args):
    path = ledger_path(args.project, args.ledger)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entries = load(path)

    key = (args.name, args.side)
    if key in entries:
        print("[SKIP] 已存在: %s (%s)" % key)
        return 0

    new_file = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as fh:
        if new_file:
            fh.write("# API 事实台账\n\n")
            fh.write("> 由 `scripts/fact_ledger.py` 维护。每条必须是 MCP 返回过的内容。\n\n")
            fh.write(HEADER + "\n" + SEP + "\n")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        cells = [args.name, args.side, args.purpose, args.usage, args.source, stamp]
        fh.write("| " + " | ".join(esc(c) for c in cells) + " |\n")

    print("[ADD] %s (%s) -> %s" % (args.name, args.side, path))
    return 0


def cmd_list(args):
    path = ledger_path(args.project, args.ledger)
    entries = load(path)
    if not entries:
        print("[EMPTY] %s 不存在或没有条目" % path)
        return 0
    print("台账: %s  共 %d 条" % (path, len(entries)))
    for (name, side), cells in sorted(entries.items()):
        usage = cells[3] if len(cells) > 3 else ""
        print("  %-40s %-8s %s" % (name, side, usage[:60]))
    return 0


def _scan_project(project):
    found = set()
    modules = set()
    for base, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "__pycache__", "docs", "node_modules")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            try:
                text = open(path, "r", encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for mod in MOD_IMPORT_RE.findall(text):
                modules.add(mod)
            for tok in CALL_RE.findall(text):
                if tok.startswith(API_PREFIX) and len(tok) >= 5:
                    found.add(tok)
    return found, modules


def cmd_check(args):
    path = ledger_path(args.project, args.ledger)
    entries = load(path)
    known = {k[0] for k in entries}
    known_modules = {k[0] for k in entries}

    calls, modules = _scan_project(args.project)
    missing_calls = sorted(calls - known)
    missing_modules = sorted(modules - known_modules)

    print("台账: %s  条目 %d" % (path, len(entries)))
    print("扫描: ModSDK 风格调用 %d，mod.* 导入 %d" % (len(calls), len(modules)))

    if missing_calls:
        print("\n[WARN] 以下调用不在台账中（确认是否 ModSDK API，若是则先查再补录）:")
        for name in missing_calls:
            print("  - %s" % name)
    if missing_modules:
        print("\n[WARN] 以下模块导入不在台账中:")
        for name in missing_modules:
            print("  - %s" % name)

    if not missing_calls and not missing_modules:
        print("[OK] 所有可识别的 ModSDK 调用都能在台账中找到")
        return 0

    print("\n补录示例:")
    print("  python scripts/fact_ledger.py add --project %s --name <名称> --side server \\" % args.project)
    print("      --purpose '<用途>' --usage '<签名/示例>' --source 'get_api_detail'")
    return 1 if args.strict else 0


def main():
    parser = argparse.ArgumentParser(description="ModSDK API fact ledger")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("add", "check", "list"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True, help="工程根目录")
        p.add_argument("--ledger", help="台账路径，默认 <project>/docs/fact-ledger.md")
        if name == "add":
            p.add_argument("--name", required=True)
            p.add_argument("--side", default="common", choices=["server", "client", "common"])
            p.add_argument("--purpose", default="")
            p.add_argument("--usage", default="")
            p.add_argument("--source", default="get_api_detail")
        if name == "check":
            p.add_argument("--strict", action="store_true", help="有缺失时返回 1")

    args = parser.parse_args()
    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        print("[FATAL] 不是目录: %s" % root, file=sys.stderr)
        return 2
    args.project = root

    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "list":
        return cmd_list(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
