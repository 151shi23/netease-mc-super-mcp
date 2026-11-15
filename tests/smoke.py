#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端冒烟：启动 super_mcp.py，验证聚合、转发、元工具。

用法:
    <带 mcp 包的 python> tests/smoke.py

退出码: 0 全部通过；1 失败
"""

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent.parent
FAILED = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))
    if not ok:
        FAILED.append(name)


async def main():
    env = dict(os.environ)
    env.setdefault("SUPER_PROJECT", str(HERE))
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "super_mcp.py")],
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            check("tools >= 40", len(names) >= 40, "count=%d" % len(names))
            check("meta tools present",
                  all(n in names for n in ("mc_capabilities", "mc_health", "mc_preflight",
                                           "mc_ledger_add", "mc_ledger_check")))

            health = await session.call_tool("mc_health", {})
            text = health.content[0].text
            check("mc_health runs", "官方 MCP" in text)
            official_ok = "官方 MCP: OK" in text
            check("official upstream OK", official_ok, "" if official_ok else "见 mc_health 输出")

            if official_ok:
                res = await session.call_tool("search_api", {"query": "GetEngineCompFactory", "limit": 1})
                body = res.content[0].text
                check("proxied search_api", "GetEngineCompFactory" in body, body[:60].replace("\n", " "))

            caps = await session.call_tool("mc_capabilities", {})
            check("mc_capabilities runs", "用法约定" in caps.content[0].text)

    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
