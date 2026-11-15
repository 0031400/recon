# recon —— 带自研加密的远程主机控制 MCP

用 **Python 3.10 + uv** 实现。结构参考同目录的 `frit` 项目：**远端被控主机跑 TCP 加密服务，AI 本地跑 stdio MCP 客户端**，中间走 **AES-128-ECB 加密帧**。

提供三类能力：`exec`（执行命令）、`upload`（上传文件，分块）、`download`（下载文件，分块），支持 **Windows / Linux**。

## 架构

```
AI 客户端
  │ stdio (MCP JSON-RPC)
  ▼
recon (本地, stdio MCP ⇄ TCP 客户端)      ← 配置: 远程 host/port/psk
  │ TCP + AES-128-ECB 加密帧 (frit 式)
  ▼
recon server (远端被控主机, 加密 TCP 服务)  ← 真正执行 exec / 文件读写
```

- AI 客户端只认识本地一个 **stdio MCP server**（兼容性最好）。
- 本地进程把每条 MCP 工具调用序列化成 RPC，用加密帧发到远端；远端执行后加密回传结果。
- 加密链路与 frit 同源：`[2B密文长度][AES-128-ECB+PKCS7]`，明文内部 `[2B帧类型][payload]`，帧类型 `0x66F0` 兼作鉴权。

## 安装

```bash
uv sync
```

## 配置

远端 `data/server.json`（被控主机）：

```json
{
    "listen": "tcp://0.0.0.0:40000",
    "psk": "共享密钥",
    "debug": false,
    "settings": {
        "default_timeout": 60,
        "max_timeout": 3600,
        "max_output_bytes": 2097152,
        "chunk_size": 10485760,
        "chunk_threshold": 10485760,
        "max_transfer_bytes": 1073741824,
        "temp_subdir": "tmp"
    }
}
```

本地 `data/client.json`（AI 所在机器）：

```json
{
    "server": "tcp://1.2.3.4:40000",
    "psk": "共享密钥",
    "debug": false
}
```

- `psk` 两端必须一致。
- `settings` 可选，全部有默认值；`chunk_size` / `chunk_threshold` 默认各 10 MiB。
- 支持 UTF-8 BOM 的 JSON。

## 运行

`server` 与 `client` 是**平级子命令**，结构一致，各自只接受 `-c <配置文件路径>`。

远端（被控主机）：

```bash
uv run python -m recon server -c data/server.json
```

本地（AI 端，作为 MCP stdio server 由客户端拉起）：

```bash
uv run python -m recon client -c data/client.json
```

> `server` 和 `client` 都**只接受 `-c <配置文件路径>`** 这一个命令行参数；`host`/`port`/`psk` 等全部写在配置文件里，且**不读取任何环境变量**。

### 接入 AI 客户端（MCP 配置）

用 stdio 方式配置，由客户端拉起本地 `recon client`（只传 `-c` 指向客户端配置文件）：

```json
{
  "mcpServers": {
    "win-prod-01": {
      "command": "uv",
      "args": ["run", "python", "-m", "recon", "client", "-c", "D:/path/to/data/client.json"]
    }
  }
}
```

## 工具一览（11 个）
| 工具 | 说明 |
|---|---|
| `host_info()` | 远端 OS/版本/架构/主机名/用户/python/cwd/可用 shell |
| `exec(command, shell="auto", cwd, timeout=60, env, stdin)` | 执行命令，返回 `{exit_code, stdout, stderr, duration_ms, truncated}` |
| `upload_begin(path, total_size, sha256=None, resume=False)` | 开始分块上传 → `{upload_id, next_offset}` |
| `upload_chunk(upload_id, offset, chunk_b64)` | 追加一块 → `{received, next_offset}` |
| `upload_finish(upload_id)` | 校验并原子落盘 → `{bytes, sha256, path}` |
| `upload_abort(upload_id)` | 取消上传、删临时文件 |
| `upload(path, content_b64)` | 小文件单次上传 |
| `download_begin(path)` | 开始分块下载 → `{download_id, total_size, sha256}` |
| `download_chunk(download_id, offset, length)` | 读一块 → `{chunk_b64, eof, next_offset}` |
| `download_finish(download_id)` | 释放下载会话 |
| `download(path, max_bytes=None)` | 小文件单次下载 |

## 加密方案（与 frit 一致）

- 密钥派生：`aes_key = SHA256(psk)[:16]`（AES-128）。
- 加密：**AES-128-ECB + PKCS7**。
- 线上帧：`[2B 大端密文长度][密文]`。
- 明文帧：`[2B 帧类型][payload]`；`0x66F0` = JSON-RPC 控制消息，`0x04EB` = 原始数据。
- **鉴权中间件**：首个解密帧的帧类型必须是 `0x66F0`，否则（解密失败 / padding 非法 / 类型不对）直接断开。错误 psk 会在第一次解密时报 `invalid padding` 并被拒绝。

> 警告：固定 key 的 AES-ECB 存在"相同明文块 → 相同密文块"的弱点，与 frit 保持一致，仅按需求提供；生产环境建议改用 AES-GCM 等 AEAD。

## 分块传输约定

- 块大小 / 阈值：各 **10 MiB**（可配）。
- 临时文件：`<项目>/data/<temp_subdir>/` 下，`upload_finish` 用 `os.replace` 原子改名。
- 断点续传：`upload_begin(..., resume=true)` 复用该目标遗留的 `.part`。
- **不支持并发**：同一时间只允许一个上传或一个下载。

## 目录结构

```
recon/
├── pyproject.toml           # uv / 依赖 / 入口 recon
├── data/                    # 配置示例 + 传输临时文件
│   ├── server.example.json
│   └── client.example.json
├── src/recon/
│   ├── __main__.py          # CLI: `recon server -c ...` / `recon client -c ...`
│   ├── config.py            # Server/Client 配置（JSON）
│   ├── config_tuning.py     # exec/传输参数（Settings）
│   ├── crypto.py            # AES-128-ECB 帧加密（frit 同源）
│   ├── protocol.py          # 明文帧编解码
│   ├── aio.py               # asyncio 加密帧读写
│   ├── backend.py           # exec/upload/download 实际执行
│   ├── server.py            # 加密 TCP 服务（远端）
│   ├── client.py            # stdio MCP ⇄ TCP 桥（本地）
│   └── executors/           # 跨平台命令执行器
└── tests/
    ├── test_smoke.py        # crypto / protocol / transfer 单测
    ├── smoke.py             # 端到端冒烟（加密 TCP）
    └── stdio_e2e.py         # 真实 stdio MCP 端到端
```

## 自检

```bash
uv run python tests/smoke.py       # 加密往返 + exec + 分块上传下载 + 错误 psk 拒绝
uv run pytest                      # 单元测试
# 真实 stdio 端到端（需先按上面启动一个 server）：
uv run python tests/stdio_e2e.py
```
