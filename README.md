# NetEase Minecraft Super MCP

一个 stdio MCP 服务，把**网易《我的世界》中国版 ModSDK 官方 MCP** 与**社区运行时工具**聚合成单一入口。

官方 MCP 能保证“写对”，但不碰运行时；社区工具能“跑通”，但模型往往不知道何时该用。本项目把两者放进同一个工具命名空间，并补上体检、台账、离线兜底六个元工具。

```
MCP 客户端
    │  stdio
    ▼
netease-mc-super
    ├── 官方 modsdk_mcp_server   39 tools   全平台   查 API / 生成 / 审查
    ├── mcdk_stdio_bridge        运行时     Windows  启停 / 热重载 / 日志 / 截图
    └── 元工具                    6 tools    全平台   体检 / 台账 / 离线检索
```

## 为什么需要它

1. **官方 MCP 没有运行时能力**：不能启动游戏、读日志、执行代码。模型写完代码无从验证。
2. **社区工具需要自己拼 CLI**：会话、游标、退出码都要手管，模型很容易漏。
3. **多 MCP 并存时模型会选错**：工具分散在不同 server，路由依赖模型自觉。
4. **上下文会爆**：文档、日志、图片一多，工具清单就被挤出去了。

聚合层把“选哪个 server”变成“选哪个工具”，并让不可用的一层显式报错，而不是静默失败。

## 快速开始

### 前置

- Python ≥ 3.10，且已安装 `mcp` 包
- 官方仓库：[MCNeteaseDevs/modsdk_mcp_server](https://github.com/MCNeteaseDevs/modsdk_mcp_server)（GPL-3.0）
- 可选运行时：[GitHub-Zero123/MCDevTool](https://github.com/GitHub-Zero123/MCDevTool)（BSD-3，仅 Windows）

```bash
# 1. 准备官方 MCP
git clone https://github.com/MCNeteaseDevs/modsdk_mcp_server.git
cd modsdk_mcp_server && python -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. 本仓库
git clone https://github.com/151shi23/netease-mc-super-mcp.git netease-mc-super
cd netease-mc-super

# 3. 自检（不启动服务）
../modsdk_mcp_server/.venv/bin/python super_mcp.py --list
```

预期输出中 `official.ok` 为 `true`、`count` 为 `39`。

### 客户端配置

```jsonc
{
  "mcpServers": {
    "netease-mc-super": {
      "command": "/abs/path/modsdk_mcp_server/.venv/bin/python",
      "args": ["/abs/path/netease-mc-super/super_mcp.py"],
      "env": {
        "SUPER_PROJECT": "D:/MyAddon",
        "SUPER_OFFICIAL_ROOT": "/abs/path/modsdk_mcp_server",
        "SUPER_MCDK_BRIDGE": "D:/MCDevTool/build/mcdk_stdio_bridge.exe"
      }
    }
  }
}
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUPER_PROJECT` | 当前目录 | 工程根（含 `.mcdev.json`） |
| `SUPER_OFFICIAL_ROOT` | `../vendor/modsdk_mcp_server` | 官方 MCP 仓库根 |
| `SUPER_MCDK_BRIDGE` | 空 | `mcdk_stdio_bridge` 可执行文件；不配则运行时层不可用 |
| `SUPER_MCDK_HOST` / `SUPER_MCDK_PORT` | `localhost` / `19133` | 游戏内 MCP 地址 |
| `SUPER_SCRIPTS` | `./scripts` | 元工具脚本目录 |

## 元工具

| 工具 | 作用 | 何时用 |
|---|---|---|
| `mc_capabilities` | 列出当前各层可用性与工具数 | 不确定有什么 |
| `mc_health` | 上游 / 工程 / mcdev / 台账体检 | 连不上、工具缺失 |
| `mc_preflight` | 静态检查：结构、uuid、Python 基线、端侧混用 | 写完代码 |
| `mc_ledger_add` | 把已查证的 API 写入 `docs/fact-ledger.md` | 每次查完 API |
| `mc_ledger_check` | 校验代码调用是否都在台账中 | 交付前 |
| `mc_api_lookup_local` | 离线索引兜底检索 | 官方 MCP 不可用 |

## 运行时工具（来自 mcdk）

`execute_code`、`get_latest_logs`、`get_latest_error_logs`、`reload_scripts`、`reload_game`、`capture_game_window`、`mc_input`、`mc_profiler`。

前置：项目 `mcdev.json` 开启

```jsonc
{ "mcp_server_config": { "enabled": true, "server_ip": "localhost", "server_port": 19133 } }
```

并用 `mcdk run` 启动游戏。桥接可早于游戏加载，真正调用时才连接。

## 验证

```bash
# 用带 mcp 包的 Python
<python> tests/smoke.py
```

覆盖：工具聚合数量、`mc_health`、官方 `search_api` 转发、台账写入与校验。任一失败退出码为 1。

## 设计取舍

- **启动不依赖游戏**：桥接只在 `tools/call` 时连接，避免客户端启动时因游戏未开而整体失败。
- **失败要可诊断**：运行时不可用返回“原因 + 下一步”，不返回空内容。
- **冲突显式化**：同名工具官方优先，社区工具改名为 `mcdk__<name>`。
- **代理层不是证书颁发机构**：schema 以上游实时返回为准；本项目只做进程内转发，不复制上游代码。

## 已知限制

- 运行时层仅 Windows，且必须有游戏开发端；本仓库的 CI 环境无法验证。
- 官方 MCP 为 GPL-3.0，mcdk 为 BSD-3，各自义务不因聚合而改变；本项目未内嵌其代码。
- 游戏内脚本基线请在项目中实测确认；本项目的静态检查默认按 Python 3。
- 发布链路（`mcs-test-helper`）不在本服务内，避免误触发账号操作。

## 许可

MIT，见 [LICENSE](LICENSE)。

第三方依赖各自遵循其许可：modsdk_mcp_server（GPL-3.0）、MCDevTool / mcdk（BSD-3）。
