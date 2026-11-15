#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网易 ModSDK 超级 MCP：把官方 MCP 与社区运行时整合成一个 stdio 服务。

上游:
  1. 官方 modsdk_mcp_server  -> 39 个文档/生成/审查工具（跨平台）
  2. MCDevTool mcdk bridge   -> 运行时工具（reload_scripts / reload_game /
                                execute_code / get_latest_logs / capture_game_window ...）
本层自有:
  mc_capabilities / mc_health / mc_preflight / mc_ledger_add /
  mc_ledger_check / mc_api_lookup_local

运行（必须用带 mcp 包的 Python，例如官方仓库的 .venv）:
  vendor/modsdk_mcp_server/.venv/bin/python super-mcp/super_mcp.py

环境变量:
  SUPER_PROJECT        项目目录（含 .mcdev.json）
  SUPER_OFFICIAL_ROOT  官方仓库根目录
  SUPER_MCDK_BRIDGE    mcdk_stdio_bridge 可执行文件路径（不配则运行时层不可用）
  SUPER_MCDK_HOST / SUPER_MCDK_PORT  默认 localhost / 19133

设计原则:
  - 启动不依赖游戏：上游连不上只标记为 unavailable，不阻塞 tools/list。
  - 运行时调用失败必须返回“为什么”和“下一步”，不返回空。
  - 名称冲突时官方工具优先，社区工具加 mcdk__ 前缀。
"""

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

REPO = Path(__file__).resolve().parent
WORKSPACE = REPO.parent


def _scripts_dir():
    candidates = []
    if os.environ.get("SUPER_SCRIPTS"):
        candidates.append(Path(os.environ["SUPER_SCRIPTS"]))
    candidates += [REPO / "scripts", WORKSPACE / "skills" / "netease-mc-modsdk-super" / "scripts"]
    for candidate in candidates:
        if (candidate / "preflight.py").exists():
            return candidate
    return REPO / "scripts"


def _official_root():
    if os.environ.get("SUPER_OFFICIAL_ROOT"):
        return Path(os.environ["SUPER_OFFICIAL_ROOT"])
    for candidate in (WORKSPACE / "vendor" / "modsdk_mcp_server", REPO / "vendor" / "modsdk_mcp_server"):
        if (candidate / "start_mcp.py").exists():
            return candidate
    return WORKSPACE / "vendor" / "modsdk_mcp_server"


SCRIPTS = _scripts_dir()


def _project_dir(explicit=None):
    candidate = explicit or os.environ.get("SUPER_PROJECT") or os.getcwd()
    return Path(candidate).expanduser().resolve()


class Upstream:
    def __init__(self, key, label, params):
        self.key = key
        self.label = label
        self.params = params
        self.session = None
        self.tools = []
        self.ok = False
        self.error = ""

    async def start(self, stack):
        if self.params is None:
            self.error = "未配置"
            return
        try:
            read, write = await stack.enter_async_context(stdio_client(self.params))
            self.session = await stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            result = await self.session.list_tools()
            self.tools = list(result.tools)
            self.ok = True
            print("[super-mcp] 上游就绪: %s (%d tools)" % (self.label, len(self.tools)), file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)
            print("[super-mcp] 上游不可用: %s -> %s" % (self.label, self.error), file=sys.stderr)


def build_upstreams():
    official_root = _official_root()
    official = Upstream(
        "official",
        "modsdk_mcp_server",
        StdioServerParameters(
            command=sys.executable,
            args=[str(official_root / "start_mcp.py")],
            env={**os.environ, "MODSDK_DOCS_PATH": str(official_root / "docs")},
        ) if (official_root / "start_mcp.py").exists() else None,
    )

    bridge = os.environ.get("SUPER_MCDK_BRIDGE", "").strip()
    project = os.environ.get("SUPER_PROJECT", "").strip()
    if bridge:
        args = ["--host", os.environ.get("SUPER_MCDK_HOST", "localhost"),
                "--port", os.environ.get("SUPER_MCDK_PORT", "19133")]
        if project:
            args += ["--project", project]
        runtime = Upstream("mcdk", "mcdk_stdio_bridge", StdioServerParameters(command=bridge, args=args))
    else:
        runtime = Upstream(
            "mcdk", "mcdk_stdio_bridge", None
        )
        runtime.error = ("未配置 SUPER_MCDK_BRIDGE。Windows 下指向 mcdk_stdio_bridge.exe，"
                         "并在项目 mcdev.json 开启 mcp_server_config.enabled")

    return [official, runtime]


META_TOOLS = [
    types.Tool(
        name="mc_capabilities",
        description="返回当前可用的能力分层：官方文档层、社区运行时层、元工具。先调这个再决定用什么。",
        inputSchema={"type": "object", "properties": {}},
    ),
    types.Tool(
        name="mc_health",
        description="体检：探测官方/超级/mcdk 是否已装，检查上游连通性、项目路径、mcdev 开关与台账。排障第一步。",
        inputSchema={"type": "object", "properties": {"project": {"type": "string"}}},
    ),
    types.Tool(
        name="mc_preflight",
        description="跑工程静态体检（结构/uuid/Python 基线/端侧混用）。返回 CRITICAL/WARN 清单。",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "python_baseline": {"type": "integer", "enum": [2, 3], "default": 3},
                "strict": {"type": "boolean", "default": False},
            },
        },
    ),
    types.Tool(
        name="mc_ledger_add",
        description="把已查证的 API 追加进 docs/fact-ledger.md。查完 API 必须立刻调用。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "side": {"type": "string", "enum": ["server", "client", "common"], "default": "common"},
                "purpose": {"type": "string"},
                "usage": {"type": "string"},
                "source": {"type": "string", "default": "get_api_detail"},
                "project": {"type": "string"},
            },
            "required": ["name", "purpose", "usage"],
        },
    ),
    types.Tool(
        name="mc_ledger_check",
        description="校验代码里的 ModSDK 风格调用是否都已在台账中；交付前必跑。",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "strict": {"type": "boolean", "default": False},
            },
        },
    ),
    types.Tool(
        name="mc_api_lookup_local",
        description="官方 MCP 不可用时的离线兜底检索（1886 条索引）。结果非权威，需复核。",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "side": {"type": "string", "enum": ["server", "client"]},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    ),
]


def _text(payload):
    return [types.TextContent(type="text", text=payload)]


def _run_script(script, args, timeout=180):
    cmd = [sys.executable, str(SCRIPTS / script)] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        body = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if err:
            body = (body + "\n[stderr]\n" + err).strip()
        return proc.returncode, body or "(无输出)"
    except subprocess.TimeoutExpired:
        return 124, "执行超时（%ss）" % timeout
    except Exception as exc:  # noqa: BLE001
        return 2, "%s: %s" % (type(exc).__name__, exc)


INSTALL_LINKS = {
    "official_mcp": "https://github.com/MCNeteaseDevs/modsdk_mcp_server",
    "super_mcp": "https://github.com/151shi23/netease-mc-super-mcp",
    "mcdk": "https://github.com/GitHub-Zero123/MCDevTool/releases",
    "mc_studio": "https://mc.163.com/",
}


def _env_check_script():
    candidates = []
    if os.environ.get("SUPER_SCRIPTS"):
        candidates.append(Path(os.environ["SUPER_SCRIPTS"]))
    candidates += [
        WORKSPACE / "skills" / "netease-mc-modsdk-super" / "scripts",
        REPO / "scripts",
        REPO.parent / "scripts",
    ]
    for candidate in candidates:
        script = candidate / "env_check.py"
        if script.exists():
            return script
    return None


def _env_snapshot():
    """优先调用 Skill 的 env_check.py；没有则退化为最小探测。"""
    script = _env_check_script()
    if script:
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--json"],
                capture_output=True, text=True, timeout=20,
            )
            if proc.stdout.strip():
                return json.loads(proc.stdout)
        except Exception:
            pass

    official = os.environ.get("NEMC_MCP_ROOT") or os.environ.get("MODSDK_MCP_ROOT")
    if not official:
        vendor = REPO.parent / "vendor" / "modsdk_mcp_server"
        official = str(vendor) if (vendor / "start_mcp.py").exists() else None
    mcdk = shutil.which("mcdk") or shutil.which("mcdk.exe")
    missing = [k for k, v in (("official_mcp", official), ("mcdk", mcdk)) if not v]
    return {
        "official_mcp": {"found": bool(official), "path": official},
        "super_mcp": {"found": True, "path": str(REPO)},
        "mcdk": {"found": bool(mcdk), "path": mcdk},
        "missing": missing,
        "links": INSTALL_LINKS,
        "source": "builtin",
    }


def _mcdev_status(project):
    path = project / ".mcdev.json"
    if not path.exists():
        return "缺少 .mcdev.json（还不是 MCDevTool 管理的工程）"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ".mcdev.json 存在但解析失败（可能含注释）"
    cfg = data.get("mcp_server_config") or {}
    return "mcp_server_config.enabled=%s, port=%s" % (cfg.get("enabled", False), cfg.get("server_port", 19133))


def handle_meta(name, args, upstreams):
    project = _project_dir(args.get("project"))

    if name == "mc_capabilities":
        lines = ["平台: %s %s" % (platform.system(), platform.release()),
                 "项目: %s" % project, ""]
        for up in upstreams:
            state = "可用 (%d tools)" % len(up.tools) if up.ok else ("不可用: " + (up.error or "unknown"))
            lines.append("- %-22s %s" % (up.label, state))
        lines += ["", "自有工具: " + ", ".join(t.name for t in META_TOOLS),
                  "",
                  "用法约定:",
                  "  文档/生成/审查 -> 官方 39 工具（无状态，跨平台）",
                  "  运行/热重载/日志 -> mcdk 运行时工具（Windows + 已启动游戏）",
                  "  查完 API -> mc_ledger_add；交付前 -> mc_ledger_check"]
        return _text("\n".join(lines))

    if name == "mc_health":
        official = next(u for u in upstreams if u.key == "official")
        runtime = next(u for u in upstreams if u.key == "mcdk")
        ledger = project / "docs" / "fact-ledger.md"
        lines = [
            "平台: %s" % platform.system(),
            "官方 MCP: %s" % ("OK (%d tools)" % len(official.tools) if official.ok else "FAIL - " + official.error),
            "运行时 MCP: %s" % ("OK (%d tools)" % len(runtime.tools) if runtime.ok else "UNAVAILABLE - " + runtime.error),
            "项目目录: %s %s" % (project, "存在" if project.is_dir() else "不存在"),
            "mcdev 配置: %s" % _mcdev_status(project),
            "事实台账: %s" % ("存在" if ledger.exists() else "缺失（首次开发可接受，交付前必须有）"),
            "预检脚本: %s" % ("存在" if (SCRIPTS / "preflight.py").exists() else "缺失"),
        ]
        env = _env_snapshot()
        found = lambda key: "已装" if env.get(key, {}).get("found") else "未装"
        lines.append("环境探测: 官方 MCP %s | 超级 MCP %s | mcdk %s" % (
            found("official_mcp"), found("super_mcp"), found("mcdk")))
        if env.get("missing"):
            lines.append("缺失组件: %s" % ", ".join(env["missing"]))
            for key in env["missing"]:
                lines.append("  - %s: %s" % (key, (env.get("links") or {}).get(key, "")))
            lines.append("下一步: 先询问用户是否安装；同意后按 16-env-check.md 下载，勿自动安装")
        if platform.system() != "Windows":
            lines.append("提示: 运行时层仅 Windows 可用，本机只能做文档/生成/静态检查。")
        return _text("\n".join(lines))

    if name == "mc_preflight":
        argv = ["--project", str(project)]
        if args.get("python_baseline") in (2, 3):
            argv += ["--python-baseline", str(args["python_baseline"])]
        if args.get("strict"):
            argv.append("--strict")
        code, body = _run_script("preflight.py", argv)
        return _text("exit=%d\n%s" % (code, body))

    if name == "mc_ledger_add":
        argv = ["add", "--project", str(project), "--name", args["name"],
                "--side", args.get("side", "common"), "--purpose", args.get("purpose", ""),
                "--usage", args.get("usage", ""), "--source", args.get("source", "get_api_detail")]
        code, body = _run_script("fact_ledger.py", argv)
        return _text("exit=%d\n%s" % (code, body))

    if name == "mc_ledger_check":
        argv = ["check", "--project", str(project)]
        if args.get("strict"):
            argv.append("--strict")
        code, body = _run_script("fact_ledger.py", argv)
        return _text("exit=%d\n%s" % (code, body))

    if name == "mc_api_lookup_local":
        argv = [args["query"]]
        if args.get("side"):
            argv += ["--side", args["side"]]
        if args.get("limit"):
            argv += ["--limit", str(args["limit"])]
        code, body = _run_script("api_index_search.py", argv)
        return _text("exit=%d\n%s" % (code, body))

    return None


def build_server(upstreams):
    server = Server("netease-mc-super")

    @server.list_tools()
    async def handle_list_tools():
        merged = list(META_TOOLS)
        seen = {t.name for t in merged}
        for up in upstreams:
            for tool in up.tools:
                name = tool.name
                if name in seen:
                    name = "%s__%s" % (up.key, name)
                if name in seen:
                    continue
                seen.add(name)
                merged.append(types.Tool(
                    name=name,
                    description="[%s] %s" % (up.key, tool.description or ""),
                    inputSchema=tool.inputSchema,
                ))
        return merged

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict):
        arguments = arguments or {}
        try:
            meta = handle_meta(name, arguments, upstreams)
            if meta is not None:
                return meta

            target, real = None, name
            for up in upstreams:
                if not up.ok:
                    continue
                for tool in up.tools:
                    if tool.name == name or "%s__%s" % (up.key, tool.name) == name:
                        target, real = up, tool.name
                        break
                if target:
                    break

            if target is None:
                return _text("未知工具: %s。先调用 mc_capabilities 查看当前可用工具。" % name)

            result = await target.session.call_tool(real, arguments)
            if result.isError:
                body = "\n".join(getattr(c, "text", "") for c in result.content)
                return _text("[%s] 工具返回错误: %s" % (target.key, body or "无详情"))
            return result.content
        except Exception as exc:  # noqa: BLE001
            return _text("调用失败 [%s]: %s: %s" % (name, type(exc).__name__, exc))

    return server


async def run_serve(upstreams):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        for up in upstreams:
            await up.start(stack)
        server = build_server(upstreams)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


async def run_list(upstreams):
    from contextlib import AsyncExitStack
    async with AsyncExitStack() as stack:
        for up in upstreams:
            await up.start(stack)
        payload = {"meta": [t.name for t in META_TOOLS],
                   "upstreams": {u.key: {"ok": u.ok, "count": len(u.tools),
                                        "tools": [t.name for t in u.tools],
                                        "error": u.error} for u in upstreams}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="NetEase ModSDK super MCP")
    parser.add_argument("--list", action="store_true", help="仅列出聚合后的工具，不启动服务")
    args = parser.parse_args()

    upstreams = build_upstreams()
    try:
        if args.list:
            asyncio.run(run_list(upstreams))
        else:
            asyncio.run(run_serve(upstreams))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
