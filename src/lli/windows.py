"""Windows-specific system proxy and certificate helpers for the desktop app."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from lli.config import get_cert_info

INTERNET_SETTINGS_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
PROXY_VALUE_NAMES = ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL")


def _hidden_subprocess_options() -> dict[str, Any]:
    """Run Windows helper commands without creating a visible console window."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


@dataclass
class RegistryValue:
    exists: bool
    value: Any = None
    value_type: int | None = None


@dataclass
class SystemProxyStatus:
    supported: bool
    enabled: bool
    managed_by_lli: bool
    server: str | None


class WindowsSystemProxy:
    """Manage one reversible, per-user WinINet proxy configuration."""

    def __init__(self, state_path: Path | None = None) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.state_path = state_path or app_data / "llm-interceptor" / "system_proxy_snapshot.json"
        self._managed_server: str | None = None

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def _proxy_server(self, host: str, port: int) -> str:
        return f"http={host}:{port};https={host}:{port}"

    def _read_values(self) -> dict[str, RegistryValue]:
        if not self.supported:
            return {}
        import winreg

        values: dict[str, RegistryValue] = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS_PATH) as key:
            for name in PROXY_VALUE_NAMES:
                try:
                    value, value_type = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    values[name] = RegistryValue(exists=False)
                else:
                    values[name] = RegistryValue(True, value, value_type)
        return values

    def _write_values(self, values: dict[str, RegistryValue]) -> None:
        if not self.supported:
            return
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            INTERNET_SETTINGS_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            for name, entry in values.items():
                if entry.exists:
                    assert entry.value_type is not None
                    winreg.SetValueEx(key, name, 0, entry.value_type, entry.value)
                else:
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass

    def _notify_settings_changed(self) -> None:
        if not self.supported:
            return
        import ctypes

        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
        wininet.InternetSetOptionW(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH

    def _load_snapshot(self) -> tuple[str, dict[str, RegistryValue]] | None:
        if not self.state_path.exists():
            return None
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            server = payload["managed_server"]
            values = {
                name: RegistryValue(**entry)
                for name, entry in payload["values"].items()
                if name in PROXY_VALUE_NAMES
            }
            if not isinstance(server, str) or set(values) != set(PROXY_VALUE_NAMES):
                return None
            return server, values
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None

    def _save_snapshot(self, server: str, values: dict[str, RegistryValue]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "managed_server": server,
            "values": {name: asdict(value) for name, value in values.items()},
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

    def _clear_snapshot(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> SystemProxyStatus:
        if not self.supported:
            return SystemProxyStatus(False, False, False, None)
        values = self._read_values()
        enabled = bool(values["ProxyEnable"].value) if values["ProxyEnable"].exists else False
        server = values["ProxyServer"].value if values["ProxyServer"].exists else None
        snapshot = self._load_snapshot()
        managed_server = self._managed_server or (snapshot[0] if snapshot else None)
        return SystemProxyStatus(True, enabled, enabled and server == managed_server, server)

    def activate(self, host: str, port: int) -> SystemProxyStatus:
        if not self.supported:
            raise RuntimeError("Windows system proxy control is only available on Windows")

        server = self._proxy_server(host, port)
        current = self._read_values()
        status = self.status()
        if not status.managed_by_lli:
            self._save_snapshot(server, current)

        import winreg

        self._write_values(
            {
                "ProxyEnable": RegistryValue(True, 1, winreg.REG_DWORD),
                "ProxyServer": RegistryValue(True, server, winreg.REG_SZ),
                "ProxyOverride": RegistryValue(
                    True,
                    "localhost;127.0.0.1;<local>",
                    winreg.REG_SZ,
                ),
                "AutoConfigURL": RegistryValue(False),
            }
        )
        self._managed_server = server
        self._notify_settings_changed()
        return self.status()

    def deactivate(self) -> SystemProxyStatus:
        if not self.supported:
            return self.status()
        snapshot = self._load_snapshot()
        if not snapshot:
            # A damaged snapshot must not leave Windows pointing to a stopped LLI proxy.
            current = self._read_values()
            current_server = current["ProxyServer"].value if current["ProxyServer"].exists else None
            enabled = bool(current["ProxyEnable"].value) if current["ProxyEnable"].exists else False
            if enabled and self._managed_server and current_server == self._managed_server:
                import winreg

                self._write_values({"ProxyEnable": RegistryValue(True, 0, winreg.REG_DWORD)})
                self._notify_settings_changed()
            self._managed_server = None
            return self.status()

        server, saved_values = snapshot
        current = self._read_values()
        current_server = current["ProxyServer"].value if current["ProxyServer"].exists else None
        enabled = bool(current["ProxyEnable"].value) if current["ProxyEnable"].exists else False
        if enabled and current_server == server:
            self._write_values(saved_values)
            self._notify_settings_changed()
        self._managed_server = None
        self._clear_snapshot()
        return self.status()

    def recover_stale_proxy(self) -> SystemProxyStatus:
        """Restore an LLI proxy setting left behind by an unexpected previous exit."""
        return self.deactivate()


class WindowsCertificateService:
    """Create and install the mitmproxy root CA in the current user's root store."""

    def __init__(self) -> None:
        self._installed_cache: tuple[Path, float, bool] | None = None

    def ensure_certificate(self) -> Path:
        from mitmproxy import certs

        info = get_cert_info()
        cert_path = Path(info["cert_path"])
        certs.CertStore.from_store(cert_path.parent, "mitmproxy", 2048)
        return cert_path

    def status(self) -> dict[str, Any]:
        info = get_cert_info()
        certificate_path = Path(info["cert_path"])
        installed = False
        if info["exists"]:
            now = monotonic()
            cached = self._installed_cache
            if cached is not None and cached[0] == certificate_path and now - cached[1] < 10:
                installed = cached[2]
            else:
                installed = self._is_installed(certificate_path)
                self._installed_cache = (certificate_path, now, installed)
        return {
            "supported": sys.platform == "win32",
            "exists": info["exists"],
            "installed": installed,
            "path": info["cert_path"],
        }

    def _is_installed(self, certificate_path: Path) -> bool:
        if sys.platform != "win32" or not certificate_path.exists():
            return False
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes

            cert = x509.load_pem_x509_certificate(certificate_path.read_bytes())
            thumbprint = cert.fingerprint(hashes.SHA1()).hex().upper()
            result = subprocess.run(
                ["certutil", "-user", "-store", "Root"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                **_hidden_subprocess_options(),
            )
            output = (result.stdout + result.stderr).replace(" ", "").upper()
            return result.returncode == 0 and thumbprint in output
        except (OSError, subprocess.SubprocessError, ValueError):
            return False

    def install(self) -> dict[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("Certificate installation is only available on Windows")
        cert_path = self.ensure_certificate()
        escaped_path = str(cert_path).replace("'", "''")
        command = (
            "$process = Start-Process -FilePath 'certutil.exe' "
            f"-ArgumentList @('-user', '-addstore', 'Root', '{escaped_path}') "
            "-WindowStyle Hidden -Verb RunAs -Wait -PassThru; exit $process.ExitCode"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            **_hidden_subprocess_options(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            detail = detail or "Certificate installation was cancelled"
            raise RuntimeError(detail)
        self._installed_cache = None
        return self.status()
