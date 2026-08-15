"""Windows desktop launcher for LLM Interceptor."""

from __future__ import annotations

import socket
import threading
import time
from contextlib import closing

from lli.config import get_default_trace_dir, load_config
from lli.logger import setup_logger
from lli.runtime import ProxyRuntime
from lli.server import create_app
from lli.watch import WatchManager


def _get_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> None:
    """Start the local API server and display it in a native webview window."""
    try:
        import uvicorn
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Desktop dependencies are missing. Install with: pip install 'llm-interceptor[desktop]'"
        ) from exc

    config = load_config()
    config.proxy.host = "127.0.0.1"
    setup_logger(config.logging.level)
    watch_manager = WatchManager(output_dir=get_default_trace_dir(), port=config.proxy.port)
    runtime = ProxyRuntime(config, watch_manager)
    runtime.system_proxy.recover_stale_proxy()

    api_port = _get_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(watch_manager, runtime),
            host="127.0.0.1",
            port=api_port,
            log_level="error",
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True, name="lli-desktop-api")
    server_thread.start()

    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        runtime.shutdown()
        raise SystemExit("The local LLI desktop server could not start")

    try:
        webview.create_window(
            "LLM Interceptor",
            f"http://127.0.0.1:{api_port}",
            min_size=(1024, 700),
        )
        webview.start()
    finally:
        runtime.shutdown()
        server.should_exit = True
        server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
