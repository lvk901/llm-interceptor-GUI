"""Controllable proxy runtime used by the desktop application and local API."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from lli.config import LLIConfig
from lli.logger import LOGGER_NAME, get_logger, setup_logger
from lli.sites import DEFAULT_MODEL_SITE_IDS, get_model_site_profiles
from lli.watch import WatchManager, WatchState
from lli.windows import WindowsCertificateService, WindowsSystemProxy


@dataclass
class RuntimeSnapshot:
    proxy_running: bool
    proxy_host: str
    proxy_port: int
    recording: bool
    session_id: str | None
    system_proxy: dict[str, Any]
    certificate: dict[str, Any]
    enabled_site_profiles: list[str]
    site_profiles: list[dict[str, str]]
    output_dir: str
    log_level: str
    address_recognition: bool
    error: str | None


@dataclass
class RuntimeLogEntry:
    """A log entry emitted by the existing LLI logger for the desktop UI."""

    sequence: int
    timestamp: str
    level: str
    source: str
    message: str


@dataclass
class RuntimeObservabilitySnapshot:
    """A bounded log delta and a fresh backend heartbeat."""

    heartbeat_at: str
    heartbeat_sequence: int
    uptime_seconds: int
    latest_log_sequence: int
    logs: list[RuntimeLogEntry]


class RuntimeLogHandler(logging.Handler):
    """Copy LLI records into the in-memory buffer without changing terminal logging."""

    def __init__(self, sink: ProxyRuntime) -> None:
        super().__init__(level=logging.DEBUG)
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink._append_log(record)
        except Exception:
            self.handleError(record)


class ProxyRuntime:
    """Own the background mitmproxy thread and reversible desktop integrations."""

    def __init__(self, config: LLIConfig, watch_manager: WatchManager) -> None:
        self.config = config
        self.watch_manager = watch_manager
        self.system_proxy = WindowsSystemProxy()
        self.certificates = WindowsCertificateService()
        self._thread: threading.Thread | None = None
        self._master: Any | None = None
        self._lock = threading.RLock()
        self._started = threading.Event()
        self._last_error: str | None = None
        self._initialized = False
        self._enabled_site_profiles = list(config.filter.site_profiles or DEFAULT_MODEL_SITE_IDS)
        self._logger = get_logger()
        self._started_at = datetime.now(timezone.utc)
        self._heartbeat_sequence = 0
        self._log_sequence = 0
        self._logs: deque[RuntimeLogEntry] = deque(maxlen=500)
        self._log_handler = RuntimeLogHandler(self)
        self._logger.addHandler(self._log_handler)

    def _append_log(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        # The CLI renders Rich markup; the desktop terminal should receive plain text.
        message = re.sub(r"\[/?[^\]]+\]", "", message)
        with self._lock:
            self._log_sequence += 1
            self._logs.append(
                RuntimeLogEntry(
                    sequence=self._log_sequence,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    level=record.levelname,
                    source=record.name.removeprefix(f"{LOGGER_NAME}."),
                    message=message,
                )
            )

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            proxy_running = (
                self._thread is not None and self._thread.is_alive() and self._master is not None
            )
            error = self._last_error
        profiles = [
            {"id": profile.id, "name": profile.name} for profile in get_model_site_profiles()
        ]
        return RuntimeSnapshot(
            proxy_running=proxy_running,
            proxy_host=self.config.proxy.host,
            proxy_port=self.config.proxy.port,
            recording=self.watch_manager.state == WatchState.RECORDING,
            session_id=self.watch_manager.current_session_id,
            system_proxy=asdict(self.system_proxy.status()),
            certificate=self.certificates.status(),
            enabled_site_profiles=list(self._enabled_site_profiles),
            site_profiles=profiles,
            output_dir=str(self.watch_manager.output_dir),
            log_level=self.config.logging.level,
            address_recognition=self.config.filter.auto_detect_api_paths,
            error=error,
        )

    def observability(self, after_sequence: int = 0) -> RuntimeObservabilitySnapshot:
        """Return recent LLI logs plus a monotonic heartbeat for desktop clients."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._heartbeat_sequence += 1
            logs = [entry for entry in self._logs if entry.sequence > after_sequence]
            latest_log_sequence = self._log_sequence
            heartbeat_sequence = self._heartbeat_sequence
        return RuntimeObservabilitySnapshot(
            heartbeat_at=now.isoformat(),
            heartbeat_sequence=heartbeat_sequence,
            uptime_seconds=max(0, int((now - self._started_at).total_seconds())),
            latest_log_sequence=latest_log_sequence,
            logs=logs,
        )

    def start_proxy(self) -> RuntimeSnapshot:
        certificate = self.certificates.status()
        if certificate["supported"] and not certificate["installed"]:
            raise RuntimeError("Install the HTTPS certificate before starting the proxy")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.snapshot()
            self._last_error = None
            self._started.clear()
            self._master = None

            if not self._initialized:
                self.watch_manager.initialize()
                self._initialized = True

            self.config.proxy.host = "127.0.0.1"
            self.config.filter.site_profiles = list(self._enabled_site_profiles)
            self._logger.info("Desktop proxy start requested on port %d", self.config.proxy.port)
            self._thread = threading.Thread(target=self._run_proxy, daemon=True, name="lli-proxy")
            self._thread.start()

        if not self._started.wait(timeout=8):
            self.stop_proxy()
            raise RuntimeError("The proxy did not start within 8 seconds")

        with self._lock:
            if self._last_error:
                raise RuntimeError(self._last_error)
        try:
            self.system_proxy.activate(self.config.proxy.host, self.config.proxy.port)
        except Exception:
            self.stop_proxy()
            raise
        return self.snapshot()

    def _run_proxy(self) -> None:
        from lli.proxy import run_watch_proxy

        def on_ready(master: Any) -> None:
            with self._lock:
                self._master = master

        def on_running() -> None:
            self._started.set()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                run_watch_proxy(
                    self.config,
                    self.watch_manager,
                    on_ready=on_ready,
                    on_running=on_running,
                )
            )
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self._started.set()
        finally:
            with self._lock:
                self._master = None
            self._started.set()
            loop.close()

    def stop_proxy(self) -> RuntimeSnapshot:
        if self.watch_manager.state == WatchState.RECORDING:
            self.stop_recording()

        with self._lock:
            master = self._master
            thread = self._thread
        if master is not None:
            master.shutdown()
        if thread is not None and thread.is_alive():
            thread.join(timeout=8)
        with self._lock:
            self._master = None
            self._thread = None
        self.system_proxy.deactivate()
        if self._initialized:
            self.watch_manager.shutdown()
            self._initialized = False
        self._logger.info("Desktop proxy stopped")
        return self.snapshot()

    def start_recording(self) -> RuntimeSnapshot:
        if not self.snapshot().proxy_running:
            raise RuntimeError("Start the proxy before recording a session")
        self.watch_manager.start_recording()
        self._logger.info("Recording started")
        return self.snapshot()

    def stop_recording(self) -> RuntimeSnapshot:
        if self.watch_manager.state != WatchState.RECORDING:
            raise RuntimeError("No recording session is active")
        session = self.watch_manager.stop_recording()
        self.watch_manager.process_session(session)
        self._logger.info("Recording processed: %s", session.session_id)
        return self.snapshot()

    def set_site_profiles(self, profile_ids: list[str]) -> RuntimeSnapshot:
        valid_ids = {profile.id for profile in get_model_site_profiles()}
        unknown = set(profile_ids) - valid_ids
        if unknown:
            raise RuntimeError(f"Unknown site profiles: {', '.join(sorted(unknown))}")
        if self.snapshot().proxy_running:
            raise RuntimeError("Stop the proxy before changing model website profiles")
        self._enabled_site_profiles = list(profile_ids)
        self.config.filter.site_profiles = list(profile_ids)
        self._logger.info("Updated model website profiles: %s", ", ".join(profile_ids) or "none")
        return self.snapshot()

    def update_settings(
        self,
        proxy_port: int,
        log_level: str,
        address_recognition: bool,
    ) -> RuntimeSnapshot:
        if self.snapshot().proxy_running and proxy_port != self.config.proxy.port:
            raise RuntimeError("Stop the proxy before changing its port")
        self.config.proxy.port = proxy_port
        self.config.logging.level = log_level
        self.config.filter.auto_detect_api_paths = address_recognition
        setup_logger(log_level)
        # setup_logger replaces handlers, so attach the desktop stream again.
        self._logger = get_logger()
        self._logger.addHandler(self._log_handler)
        self._logger.info("Desktop settings updated")
        return self.snapshot()

    def install_certificate(self) -> RuntimeSnapshot:
        self.certificates.install()
        return self.snapshot()

    def shutdown(self) -> None:
        try:
            self.stop_proxy()
        finally:
            self.system_proxy.recover_stale_proxy()
            self._logger.removeHandler(self._log_handler)
