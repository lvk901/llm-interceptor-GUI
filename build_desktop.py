"""Build the portable Windows LLM Interceptor desktop executable."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    if os.name != "nt":
        raise SystemExit("The portable desktop executable is currently built on Windows only.")

    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit("Install desktop build dependencies with: uv sync --extra desktop") from exc

    root = Path(__file__).parent
    static_dir = root / "src" / "lli" / "static"
    if not (static_dir / "index.html").exists():
        raise SystemExit("Build the React UI first with: python build_ui.py")

    PyInstaller.__main__.run(
        [
            "--noconfirm",
            "--clean",
            "--onefile",
            "--noconsole",
            "--name",
            "LLM Interceptor",
            "--paths",
            str(root / "src"),
            "--add-data",
            f"{static_dir}{os.pathsep}lli/static",
            "--collect-all",
            "mitmproxy",
            "--collect-all",
            "webview",
            "--collect-all",
            "cryptography",
            str(root / "src" / "lli" / "desktop.py"),
        ]
    )


if __name__ == "__main__":
    main()
