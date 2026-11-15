#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线检索网易 ModSDK API/事件索引。

用于官方 MCP 不可用时的降级核对：名称是否存在、在哪个端、参数长什么样。
数据来源: EaseCation/netease-modsdk-wiki 的 api-index.json

用法:
    python api_index_search.py "ServerItemUseOnEvent"
    python api_index_search.py "传送" --side server --limit 10
    python api_index_search.py "GetEngineCompFactory" --type api --json

注意:
    索引是派生数据，不是权威签名。正式开发仍应通过官方 MCP 的
    search_api + get_api_detail 校验。
"""

import argparse
import json
import os
import sys

def _find_vendor_file(rel):
    """从 Skill 目录向上查找 vendor/<rel>。"""
    cur = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidate = os.path.join(cur, "vendor", rel)
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", rel)


DEFAULT_INDEX = os.environ.get("NEMC_API_INDEX") or _find_vendor_file(
    os.path.join("netease-modsdk-wiki", "api-index.json")
)
FALLBACKS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "assets", "api-index.json"),
]


def load_index(path):
    candidates = [path] + [p for p in FALLBACKS if p != path]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            with open(candidate, "r", encoding="utf-8") as fh:
                return json.load(fh), candidate
    return None, None


def score(entry, query):
    q = query.lower()
    name = (entry.get("name") or "").lower()
    method = (entry.get("method") or "").lower()
    desc = (entry.get("description") or "").lower()

    if name == q or method == q:
        return 100
    if name.startswith(q) or method.startswith(q):
        return 80
    if q in name or q in method:
        return 60
    if q in desc or q in (entry.get("remark") or "").lower():
        return 25
    # 多关键词：全部命中才给分
    parts = [p for p in q.replace(",", " ").split() if p]
    if len(parts) > 1 and all(p in name or p in desc or p in method for p in parts):
        return 40
    return 0


def main():
    parser = argparse.ArgumentParser(description="Offline ModSDK API index search")
    parser.add_argument("query", help="关键词（支持中文）")
    parser.add_argument("--side", choices=["server", "client"], help="限定端侧")
    parser.add_argument("--type", choices=["api", "event"], help="限定类型")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--index", default=DEFAULT_INDEX, help="api-index.json 路径")
    args = parser.parse_args()

    data, used = load_index(args.index)
    if data is None:
        print("[FATAL] 找不到索引文件。请设置 NEMC_API_INDEX 或拉取 netease-modsdk-wiki。", file=sys.stderr)
        print("        已尝试: %s" % args.index, file=sys.stderr)
        return 2

    hits = []
    for entry in data:
        if args.side and entry.get("side") != args.side:
            continue
        if args.type and entry.get("type") != args.type:
            continue
        s = score(entry, args.query)
        if s > 0:
            hits.append((s, entry))

    hits.sort(key=lambda x: (-x[0], x[1].get("name") or ""))
    hits = hits[: args.limit]

    if args.json:
        print(json.dumps([e for _, e in hits], ensure_ascii=False, indent=2))
        return 0 if hits else 1

    print("索引: %s  (共 %d 条)" % (used, len(data)))
    print("查询: %s | side=%s type=%s" % (args.query, args.side or "any", args.type or "any"))
    print("-" * 64)

    if not hits:
        print("无结果。建议：")
        print("  1) 换关键词（中文功能词 -> 英文 API 片段）")
        print("  2) 去掉 --side / --type 限制")
        print("  3) 回到官方 MCP 使用 browse_api_category")
        return 1

    for s, e in hits:
        print("[%3d] %-8s %-10s %s" % (s, e.get("type"), e.get("side"), e.get("name")))
        if e.get("method"):
            print("      method: %s" % e["method"])
        if e.get("description"):
            print("      desc  : %s" % e["description"][:100])
        params = e.get("params") or []
        if params:
            shape = ", ".join("%s:%s" % (p.get("name"), p.get("type")) for p in params[:8])
            print("      params: %s" % shape)
        if e.get("link"):
            print("      doc   : %s" % e["link"])

    print("-" * 64)
    print("提醒: 索引非权威签名，正式开发请用官方 MCP 的 get_api_detail 复核。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
