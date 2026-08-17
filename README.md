# LLM Interceptor (LLI)

[简体中文](README.zh-CN.md)

LLM Interceptor is a local proxy and trace analysis tool for inspecting LLM API traffic from AI coding tools, model websites, and OpenAI-compatible relay services. It reconstructs streamed responses into readable conversations and stores captures as sessions for inspecting prompts, tool calls, outputs, latency, and token usage.

> Only intercept traffic you are authorized to inspect. Captures can contain prompts, responses, and other sensitive data. Protect the output directory accordingly.

## Highlights

- Capture common LLM APIs including Anthropic, OpenAI, Google, DeepSeek, Groq, Mistral, and Together.
- Rebuild streamed SSE responses for OpenAI Chat Completions and Responses API, including Codex `response.output_text.delta` events.
- Recognize OpenAI-compatible relays by `/v1/*` paths and request shape (`model` plus `messages`, `input`, or `prompt`), without requiring an OpenAI hostname.
- Configure browser-model API profiles for ChatGPT, Claude, Gemini, DeepSeek, Kimi, Qwen, Doubao, and Zhipu Qingyan.
- Use the Windows desktop app for proxy and recording controls, certificate installation, settings, live logs, and backend heartbeat.
- Review conversations, system prompts, tool calls, metrics, and raw JSON in the Web UI.
- Mask common secret fields automatically. Unmatched traffic and large media downloads are streamed through instead of being buffered into model traces.

## Windows Desktop App

The desktop executable is available at:

```text
dist/LLM Interceptor.exe
```

Recommended first-run sequence:

1. Click **Install certificate** and allow installation of the mitmproxy HTTPS root certificate for the current Windows user.
2. Click **Start proxy**. LLI listens on `127.0.0.1:9090` and temporarily configures the current user's Windows system proxy.
3. Click **Start recording**, then begin the model conversation or API call.
4. Click **Stop recording** when finished. The session is processed and becomes available in the sidebar.
5. Click **Stop proxy** to restore the Windows proxy settings that were present before LLI started.

### Proxy and Recording Are Separate

- The proxy can run without recording; traffic is forwarded but no session is created.
- Recording requires a running proxy.
- A proxy cannot be stopped during recording. Stop and process the recording first to avoid incomplete sessions and unrecovered network settings.
- On app shutdown, LLI attempts to finalize an active recording and restore a system proxy managed by LLI.

### When Another System Proxy Is Running

Avoid running multiple applications that take ownership of the Windows system proxy, such as Steamcommunity 302, VPN clients, or accelerators. Prefer an app-specific proxy for Codex and similar CLI tools:

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:9090'
$env:HTTPS_PROXY = 'http://127.0.0.1:9090'
codex
```

Only Codex launched from that PowerShell session will use LLI. Browsers, video applications, game platforms, and your existing system proxy retain their normal route.

### Settings and Runtime Status

Open the settings page with the button in the upper-right corner. It provides:

- Proxy port and log-level controls. The port cannot change while the proxy is running.
- Model website profile selection. Profiles cannot change while the proxy is running.
- Address recognition for `/v1/*` and compatible request bodies, useful for custom domains and API relays.

The **LIVE RUNTIME** panel at the bottom of the home page shows backend heartbeat, proxy uptime, and live logs. It is collapsed by default and remembers its open/closed state across launches.

## CLI Quick Start

### Install

Using [uv](https://docs.astral.sh/uv/) is recommended:

```bash
uv tool install llm-interceptor
```

Or use pip:

```bash
pip install llm-interceptor
```

Install from source:

```bash
git clone https://github.com/lvk901/llm-interceptor.git
cd llm-interceptor
uv sync --dev
uv run lli-dev-setup
```

### Start Watching

```bash
lli watch
```

The default proxy listens on port `9090`; the Web UI starts at `http://127.0.0.1:8000` by default.

- Press `Enter` to start recording; press it again to stop and process the session.
- Press `Esc` to cancel the active recording without saving it.
- Press `Ctrl+C` to exit watch mode.

### Send Only One CLI Tool Through LLI

PowerShell:

```powershell
$env:HTTP_PROXY = 'http://127.0.0.1:9090'
$env:HTTPS_PROXY = 'http://127.0.0.1:9090'
codex
```

macOS / Linux:

```bash
export HTTP_PROXY=http://127.0.0.1:9090
export HTTPS_PROXY=http://127.0.0.1:9090
codex
```

For Node.js clients that do not trust the local certificate, set:

```bash
export NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem
```

## HTTPS Certificates

LLI needs the mitmproxy root certificate to decrypt HTTPS traffic. The first proxy start creates it. Show local installation instructions with:

```bash
lli config --cert-help
```

Windows users can also click **Install certificate** in the desktop app. In a company network that uses a private CA for upstream HTTPS, configure that CA separately:

```bash
lli watch --upstream-ca-cert /path/to/company-ca.pem
```

The upstream CA lets LLI verify a company proxy or target server. Your client still needs to trust the mitmproxy certificate.

## Custom APIs and Relays

Address recognition covers common OpenAI-compatible paths such as:

```text
/v1/chat/completions
/v1/responses
```

LLI also checks JSON request structure for `model` plus LLM input fields. If a relay uses a non-standard path and request format, add an explicit glob:

```bash
lli watch --include "*api.example.com*"
```

Add multiple patterns by repeating `--include`. Exclude endpoints that should not be captured:

```bash
lli watch --include "*relay.example.com*" --exclude "*relay.example.com/health*"
```

## Web UI

- Sessions are ordered deterministically by timestamp and natural directory name, so `_2` sorts before `_10`.
- The middle request list shows endpoint, model, status, latency, token usage, and system-prompt grouping.
- Details show conversation content, system prompts, tool calls, charts, and raw JSON.
- The session and request panels are resizable. Dragging does not use a delayed width transition and preserves usable space for the details pane.
- Switching sessions clears the previous system-prompt filter.

The built-in browser-model profiles are ChatGPT, Claude, Gemini, DeepSeek, Kimi, Qwen, Doubao, and Zhipu Qingyan. Enable only the profiles you need in desktop settings.

## Output Files

`lli watch` creates session folders in the selected output directory. Set it explicitly when needed:

```bash
lli watch --output-dir ./traces
```

Example structure:

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

Offline commands:

```bash
lli merge --input raw_trace.jsonl --output merged.jsonl
lli split --input merged.jsonl --output ./split_output
lli stats traces/session_xxx/raw.jsonl
```

## CLI Reference

| Command | Purpose |
| --- | --- |
| `lli watch` | Start the proxy, record sessions, and start the Web UI by default. |
| `lli watch --port 8888` | Use a specific proxy port. |
| `lli watch --lan` | Listen on the LAN. Use only on a trusted network. |
| `lli watch --include "*host*"` | Add a capture URL glob. |
| `lli watch --exclude "*path*"` | Exclude a URL and attempt to bypass MITM for it. |
| `lli config --show` | Show the current configuration. |
| `lli config --cert-help` | Show certificate installation help. |
| `lli config --proxy-help` | Show client proxy configuration help. |
| `lli merge` / `lli split` / `lli stats` | Process capture files offline. |

Use `lli --help` or `lli watch --help` for all options.

## Troubleshooting

### No traffic is captured

1. Verify the target application uses `127.0.0.1:9090` and that recording began before the conversation.
2. For Codex, a relay, or a custom domain, enable address recognition. Add `--include` when it is still not recognized.
3. HTTPS traffic requires the client to trust the mitmproxy root certificate.
4. In the desktop app, check LIVE RUNTIME logs and heartbeat to confirm the proxy is running.

### Other applications become slow or lose connectivity

The Windows system proxy routes all applicable applications through LLI. Stop recording, then stop the proxy; LLI restores the prior system proxy configuration. When another proxy tool is active, use app-specific `HTTP_PROXY` and `HTTPS_PROXY` variables instead of the system proxy.

### The Stop Proxy button is disabled

An active recording protects the proxy from being stopped. Click **Stop recording**, wait for processing to finish, then stop the proxy.

### A response cannot be parsed

LLI keeps the raw response and attempts to recognize common streaming and non-streaming protocols. A relay that returns non-JSON, encrypted data, or custom events may not render as a conversation; inspect the raw JSON view and attach a redacted request/response sample when reporting the issue.

## Development and Build

Install development dependencies and run checks:

```bash
uv sync --extra desktop --dev
uv run pytest tests -q
uv run ruff check src tests
```

Build the desktop executable:

```bash
python build_ui.py
python build_desktop.py
```

The output is `dist/LLM Interceptor.exe`. To build only the frontend, run this in `ui`:

```bash
npm run build
```

## License

[MIT License](LICENSE)
