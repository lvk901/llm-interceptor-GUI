# LLM Interceptor（LLI）

[English](README.md)

LLM Interceptor 是一个本地代理抓包与分析工具，用于查看 AI 编程工具、模型网站和 OpenAI 兼容中转站发出的 LLM API 请求与响应。它可以把流式响应还原为可阅读的对话，并按会话保存，方便排查提示词、工具调用、模型输出、耗时和 token 用量。

> 仅应抓取你拥有权限查看的流量。日志可能包含提示词、响应内容和其他敏感数据，请妥善保管输出目录。

## 功能概览

- 代理层捕获 Anthropic、OpenAI、Google、DeepSeek、Groq、Mistral、Together 等常见模型 API。
- 支持 OpenAI Chat Completions 和 Responses API 的流式 SSE；可还原 Codex 使用的 `response.output_text.delta` 等事件。
- 支持 OpenAI 兼容中转站：根据 `/v1/*` 地址、请求体中的 `model` 与 `messages`、`input`、`prompt` 等特征识别，不依赖 OpenAI 官方域名。
- 支持 ChatGPT、Claude、Gemini、DeepSeek、Kimi、通义千问、豆包、智谱清言等网页模型的 API 路径配置。
- Windows 桌面版提供代理开关、录制开关、HTTPS 证书安装、设置页、实时日志和后端心跳。
- 会话数据自动整理为请求/响应对，Web UI 可查看对话、系统提示词、工具调用、统计信息和原始 JSON。
- 自动脱敏常见密钥字段；非模型流量和大媒体下载会直接透传，不写入模型会话。

## Windows 桌面版

桌面版适合希望图形化操作的 Windows 用户。可执行文件路径为：

```text
dist/LLM Interceptor.exe
```

首次启动的建议操作顺序：

1. 点击顶部的“安装证书”，允许为当前 Windows 用户安装 mitmproxy HTTPS 根证书。
2. 点击“启动代理”。LLI 会监听 `127.0.0.1:9090`，并临时将当前用户的 Windows 系统代理指向该地址。
3. 点击“开始录制”，然后再进行模型对话或调用。
4. 完成后点击“停止录制”。此时会话会自动处理并显示在左侧列表中。
5. 最后点击“停止代理”。LLI 会恢复启动前保存的 Windows 系统代理设置。

### 代理与录制是两个独立操作

- 可以只启动代理而不录制，此时流量会透传，但不会生成会话。
- 必须先启动代理才能开始录制。
- 录制中不能停止代理。请先停止录制，再停止代理，避免会话不完整和网络设置未恢复。
- 退出桌面程序时，程序会尝试结束正在录制的会话并恢复 LLI 管理的系统代理。

### 已有系统代理时的建议

如果电脑同时运行 Steamcommunity 302、VPN、加速器或其他系统代理工具，不建议让多个程序同时接管 Windows 系统代理。优先使用“只给目标程序设置代理”的方式，例如只让 Codex 经过 LLI：

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:9090'
$env:HTTPS_PROXY = 'http://127.0.0.1:9090'
codex
```

这样浏览器、视频、游戏平台和现有系统代理保持原有网络路径，只有当前 PowerShell 启动的 Codex 会通过 LLI。

### 设置页

顶部右侧齿轮进入设置页，可配置：

- 代理监听端口。代理运行时不能修改端口。
- 日志级别。
- 模型网站捕获配置。代理运行时不能修改网站配置。
- 地址识别：启用后会识别 `/v1/*` 和符合 OpenAI 兼容特征的请求，适合自定义域名或 API 中转站。

首页底部的 **LIVE RUNTIME** 显示后端心跳、代理运行时长和实时日志。该面板默认折叠，展开状态会在下次启动时保留。

## 快速开始：命令行

### 安装

推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv tool install llm-interceptor
```

或使用 pip：

```bash
pip install llm-interceptor
```

从源码安装：

```bash
git clone https://github.com/lvk901/llm-interceptor.git
cd llm-interceptor
uv sync --dev
uv run lli-dev-setup
```

### 启动监听

```bash
lli watch
```

默认代理端口是 `9090`，Web UI 默认打开在 `http://127.0.0.1:8000`。在 watch 模式下：

- `Enter`：开始录制；再次按下则停止录制并处理会话。
- `Esc`：取消当前录制，不保留该会话。
- `Ctrl+C`：退出监听。

### 只让某个命令行工具经过代理

PowerShell：

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:9090'
$env:HTTPS_PROXY = 'http://127.0.0.1:9090'
codex
```

macOS / Linux：

```bash
export HTTP_PROXY=http://127.0.0.1:9090
export HTTPS_PROXY=http://127.0.0.1:9090
codex
```

Node.js 客户端若不信任本地证书，可额外设置：

```bash
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

## HTTPS 证书

LLI 需要 mitmproxy 根证书才能解密 HTTPS 流量。首次运行代理会生成证书，查看本机安装说明：

```bash
lli config --cert-help
```

Windows 用户也可在桌面版点击“安装证书”。只安装当前用户需要的证书；如果公司设备受策略管理，请先遵循公司的安全要求。

公司网络使用自签名或企业根证书时，可以为 LLI 配置上游 CA：

```bash
lli watch --upstream-ca-cert /path/to/company-ca.pem
```

这里的上游 CA 用于 LLI 验证公司代理或目标站点；客户端仍需要信任 mitmproxy 证书。

## 自定义 API 与中转站

地址识别默认会处理常见 OpenAI 兼容路径，例如：

```text
/v1/chat/completions
/v1/responses
```

对于非标准路径，LLI 还会检查 JSON 请求是否同时具有 `model` 与 `messages`、`input` 或 `prompt` 等模型调用特征。若中转站的路径和请求格式都非常规，可手动追加匹配规则：

```bash
lli watch --include "*api.example.com*"
```

多个规则可重复传入；无须捕获的 URL 可以排除：

```bash
lli watch --include "*relay.example.com*" --exclude "*relay.example.com/health*"
```

## Web UI

Web UI 会显示已保存的会话：

- 左侧会话列表按时间和目录名自然排序，例如 `_2` 会排在 `_10` 前。
- 中间列表展示每个请求的接口、模型、状态、耗时、token 和系统提示词分组。
- 右侧详情可查看对话、系统提示词、工具调用、统计图表和原始 JSON。
- 左侧和中间面板可拖动调整宽度；拖动时不会播放滞后动画，并会保留详情区域的最小可用宽度。
- 切换会话会清除上一个会话遗留的系统提示词筛选条件。

内置的网站捕获配置包括：ChatGPT、Claude、Gemini、DeepSeek、Kimi、通义千问、豆包和智谱清言。可在桌面版设置页按需启用。

## 输出数据

`lli watch` 会在输出目录创建会话文件夹。默认目录由系统决定，也可以用 `--output-dir` 指定：

```bash
lli watch --output-dir ./traces
```

典型结构如下：

```text
traces/
├── all_captured_20260817_120000.jsonl
└── session_20260817_120500/
    ├── raw.jsonl
    ├── merged.jsonl
    └── split_output/
        ├── 001_request_2026-08-17_12-05-00.json
        └── 001_response_2026-08-17_12-05-02.json
```

常用离线处理命令：

```bash
lli merge --input raw_trace.jsonl --output merged.jsonl
lli split --input merged.jsonl --output ./split_output
lli stats traces/session_xxx/raw.jsonl
```

## CLI 参考

| 命令 | 用途 |
| --- | --- |
| `lli watch` | 启动代理、录制会话并默认启动 Web UI。 |
| `lli watch --port 8888` | 使用指定代理端口。 |
| `lli watch --lan` | 监听局域网地址；仅在确认网络环境安全时使用。 |
| `lli watch --include "*host*"` | 增加需要捕获的 URL glob。 |
| `lli watch --exclude "*path*"` | 排除 URL，并尽量让其跳过 MITM。 |
| `lli config --show` | 查看当前配置。 |
| `lli config --cert-help` | 查看证书安装说明。 |
| `lli config --proxy-help` | 查看客户端代理配置说明。 |
| `lli merge` / `lli split` / `lli stats` | 离线合并、拆分和统计抓包文件。 |

使用 `lli --help` 或 `lli watch --help` 查看完整参数。

## 常见问题

### 启动后没有捕获到流量

1. 确认目标程序实际使用了 `127.0.0.1:9090`，并在开始对话前已经启动录制。
2. 对于 Codex、中转站或自定义域名，启用地址识别；仍未识别时使用 `--include` 添加域名或路径。
3. HTTPS 请求需要客户端信任 mitmproxy 根证书。
4. 桌面版请查看底部 LIVE RUNTIME 的日志和心跳，确认代理处于运行状态。

### 开启系统代理后其他软件变慢或无法联网

系统代理会让 Windows 应用都经过 LLI。停止录制后先停止代理，LLI 会恢复启动前的系统代理。若系统还运行其他代理工具，改用“只让目标命令行程序设置 `HTTP_PROXY` / `HTTPS_PROXY`”的方式，避免多个程序互相覆盖设置。

### 停止代理按钮不可用

这是录制保护机制。请先点击“停止录制”，等待会话处理完成，再停止代理。

### 响应显示为解析失败

LLI 会保留原始响应，同时尝试识别常见流式和非流式协议。中转站若返回非 JSON、加密数据或自定义事件格式，原始 JSON 视图仍可用于定位响应实际内容；可附上脱敏后的请求/响应样本提交 issue。

## 开发与构建

安装开发依赖并执行测试：

```bash
uv sync --extra desktop --dev
uv run pytest tests -q
uv run ruff check src tests
```

构建桌面程序：

```bash
python build_ui.py
python build_desktop.py
```

产物位于 `dist/LLM Interceptor.exe`。开发界面单独构建时，也可以在 `ui` 目录运行：

```bash
npm run build
```

## 许可证

[MIT License](LICENSE)
