"""Controllable proxy runtime used by the desktop application and local API."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import monotonic
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
    proxy_running: bool
    proxy_uptime_seconds: int
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
        # Start and stop both cross a thread boundary. Keep the complete transition
        # serialized so stop cannot observe a half-started proxy without its master.
        self._lifecycle_lock = threading.Lock()
        self._started = threading.Event()
        self._last_error: str | None = None
        self._proxy_started_at: datetime | None = None
        self._initialized = False
        self._system_proxy_active = False
        self._stop_requested = False
        self._cleanup_on_exit = False
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
            proxy_running = (
                self._thread is not None and self._thread.is_alive() and self._master is not None
            )
            proxy_started_at = self._proxy_started_at
        proxy_uptime_seconds = (
            max(0, int((now - proxy_started_at).total_seconds()))
            if proxy_running and proxy_started_at is not None
            else 0
        )
        return RuntimeObservabilitySnapshot(
            heartbeat_at=now.isoformat(),
            heartbeat_sequence=heartbeat_sequence,
            uptime_seconds=max(0, int((now - self._started_at).total_seconds())),
            proxy_running=proxy_running,
            proxy_uptime_seconds=proxy_uptime_seconds,
            latest_log_sequence=latest_log_sequence,
            logs=logs,
        )

    def start_proxy(self) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            certificate = self.certificates.status()
            if certificate["supported"] and not certificate["installed"]:
                raise RuntimeError("Install the HTTPS certificate before starting the proxy")

            with self._lock:
                existing_thread = self._thread
                if existing_thread is not None and existing_thread.is_alive():
                    if self._cleanup_on_exit:
                        raise RuntimeError("The previous proxy is still stopping")
                    return self.snapshot()

            # A worker that has already exited cannot own any live proxy resources.
            # Finish its deferred cleanup before reusing the WatchManager.
            if existing_thread is not None:
                self._cleanup_stopped_proxy(existing_thread)

            with self._lock:
                self._last_error = None
                self._started.clear()
                self._master = None
                self._stop_requested = False
                self._cleanup_on_exit = False

                if not self._initialized:
                    self.watch_manager.initialize()
                    self._initialized = True

                self.config.proxy.host = "127.0.0.1"
                self.config.filter.site_profiles = list(self._enabled_site_profiles)
                self._logger.info(
                    "Desktop proxy start requested on port %d", self.config.proxy.port
                )
                thread = threading.Thread(target=self._run_proxy, daemon=True, name="lli-proxy")
                self._thread = thread
                thread.start()

            if not self._started.wait(timeout=8):
                self._stop_proxy_locked()
                raise RuntimeError("The proxy did not start within 8 seconds")

            with self._lock:
                running = self._thread is thread and thread.is_alive() and self._master is not None
                error = self._last_error
            if error:
                raise RuntimeError(error)
            if not running:
                raise RuntimeError("The proxy stopped during startup")

            try:
                self.system_proxy.activate(self.config.proxy.host, self.config.proxy.port)
            except Exception:
                self._stop_proxy_locked()
                raise

            with self._lock:
                running = self._thread is thread and thread.is_alive() and self._master is not None
                if running:
                    self._system_proxy_active = True
                    self._proxy_started_at = datetime.now(timezone.utc)
            if not running:
                # The worker may have exited while Windows was being reconfigured.
                # Its finally block could not observe the active flag, so restore now.
                self.system_proxy.deactivate()
                raise RuntimeError("The proxy stopped during startup")
            return self.snapshot()

    def _run_proxy(self) -> None:
        from lli.proxy import run_watch_proxy

        def on_ready(master: Any) -> None:
            with self._lock:
                self._master = master
                stop_requested = self._stop_requested
            if stop_requested:
                master.shutdown()

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
            worker = threading.current_thread()
            with self._lock:
                self._master = None
                needs_recovery = self._thread is worker and (
                    not self._stop_requested or self._cleanup_on_exit
                )
                if needs_recovery and self._last_error is None:
                    self._last_error = "The proxy stopped unexpectedly"
            self._started.set()
            loop.close()
            if needs_recovery:
                self._cleanup_after_worker_exit(worker)

    def _cleanup_after_worker_exit(self, worker: threading.Thread) -> None:
        """Release integrations after an unrequested or timed-out worker exit."""
        with self._lock:
            if self._thread is not worker:
                return
            restore_system_proxy = self._system_proxy_active
            initialized = self._initialized

        try:
            if restore_system_proxy:
                self.system_proxy.deactivate()
        except Exception:
            self._logger.exception("Failed to restore the Windows system proxy after proxy exit")
        finally:
            try:
                if initialized:
                    self.watch_manager.shutdown()
            except Exception:
                self._logger.exception("Failed to shut down watch mode after proxy exit")
            finally:
                with self._lock:
                    if self._thread is worker:
                        self._thread = None
                        self._master = None
                        self._proxy_started_at = None
                        self._system_proxy_active = False
                        self._initialized = False
                        self._stop_requested = False
                        self._cleanup_on_exit = False

    def _cleanup_stopped_proxy(
        self,
        thread: threading.Thread | None,
        *,
        restore_system_proxy: bool = True,
    ) -> None:
        """Release resources only after the corresponding worker has exited."""
        if thread is not None and thread.is_alive():
            raise RuntimeError("The proxy is still running")

        with self._lock:
            if thread is not None and self._thread is not thread:
                return
            initialized = self._initialized
            self._master = None
            self._thread = None
            self._proxy_started_at = None
            self._system_proxy_active = False
            self._stop_requested = False
            self._cleanup_on_exit = False

        try:
            if restore_system_proxy:
                self.system_proxy.deactivate()
        finally:
            try:
                if initialized:
                    self.watch_manager.shutdown()
            finally:
                # Even a failed integration cleanup must not leave the runtime
                # believing that a closed WatchManager is still initialized.
                with self._lock:
                    if self._thread is None:
                        self._initialized = False

    def _restore_system_proxy_before_shutdown(self) -> bool:
        """Restore direct networking before asking mitmproxy to close old flows.

        Existing browser/player connections may keep the local proxy busy while
        they drain. Updating WinINet first lets all new connections bypass it
        immediately, while the worker can still finish its normal shutdown.
        """
        with self._lock:
            should_restore = self._system_proxy_active
        if not should_restore:
            return False

        started_at = monotonic()
        self._logger.info("Restoring system proxy before proxy shutdown")
        try:
            status = self.system_proxy.deactivate()
        except Exception:
            self._logger.exception(
                "Failed to restore the Windows system proxy before proxy shutdown"
            )
            return False

        with self._lock:
            self._system_proxy_active = False
        elapsed_ms = (monotonic() - started_at) * 1000
        self._logger.info(
            "System proxy restored before proxy shutdown in %.0f ms "
            "(enabled=%s, managed=%s, server=%s)",
            elapsed_ms,
            getattr(status, "enabled", None),
            getattr(status, "managed_by_lli", None),
            getattr(status, "server", None),
        )
        return True

    def _stop_proxy_locked(self) -> RuntimeSnapshot:
        """Stop a proxy while the lifecycle lock is held."""
        if self.watch_manager.state == WatchState.RECORDING:
            raise RuntimeError("Stop recording before stopping the proxy")

        with self._lock:
            master = self._master
            thread = self._thread
            self._stop_requested = True

        stop_started_at = monotonic()
        self._logger.info("Desktop proxy stop requested")
        restored_before_shutdown = self._restore_system_proxy_before_shutdown()

        shutdown_error: Exception | None = None
        if master is not None:
            try:
                master.shutdown()
            except Exception as exc:
                shutdown_error = exc

        if shutdown_error is None and thread is not None and thread.is_alive():
            thread.join(timeout=8)

        if thread is not None and thread.is_alive():
            with self._lock:
                self._cleanup_on_exit = True
                self._last_error = "The proxy did not stop within 8 seconds"
            if shutdown_error is not None:
                raise shutdown_error
            raise RuntimeError("The proxy did not stop within 8 seconds")

        # If the early restore succeeded, the post-exit cleanup must not issue a
        # second registry write. When it failed, the existing cleanup path retries
        # the restoration before releasing the WatchManager.
        self._cleanup_stopped_proxy(
            thread,
            restore_system_proxy=not restored_before_shutdown,
        )
        if shutdown_error is not None:
            raise shutdown_error
        self._logger.info(
            "Desktop proxy stopped in %.0f ms",
            (monotonic() - stop_started_at) * 1000,
        )
        return self.snapshot()

    def stop_proxy(self) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            return self._stop_proxy_locked()

    def start_recording(self) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            if not self.snapshot().proxy_running:
                raise RuntimeError("Start the proxy before recording a session")
            self.watch_manager.start_recording()
            self._logger.info("Recording started")
            return self.snapshot()

    def stop_recording(self) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            if self.watch_manager.state != WatchState.RECORDING:
                raise RuntimeError("No recording session is active")
            session = self.watch_manager.stop_recording()
            self.watch_manager.process_session(session)
            self._logger.info("Recording processed: %s", session.session_id)
            return self.snapshot()

    def set_site_profiles(self, profile_ids: list[str]) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            valid_ids = {profile.id for profile in get_model_site_profiles()}
            unknown = set(profile_ids) - valid_ids
            if unknown:
                raise RuntimeError(f"Unknown site profiles: {', '.join(sorted(unknown))}")
            if self.snapshot().proxy_running:
                raise RuntimeError("Stop the proxy before changing model website profiles")
            self._enabled_site_profiles = list(profile_ids)
            self.config.filter.site_profiles = list(profile_ids)
            self._logger.info(
                "Updated model website profiles: %s", ", ".join(profile_ids) or "none"
            )
            return self.snapshot()

    def update_settings(
        self,
        proxy_port: int,
        log_level: str,
        address_recognition: bool,
    ) -> RuntimeSnapshot:
        with self._lifecycle_lock:
            if self.snapshot().proxy_running and proxy_port != self.config.proxy.port:
                raise RuntimeError("Stop the proxy before changing its port")
            self.config.proxy.port = proxy_port
            self.config.logging.level = log_level
            self.config.filter.auto_detect_api_paths = address_recognition
            setup_logger(log_level, self.config.logging.log_file)
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
            if self.watch_manager.state == WatchState.RECORDING:
                try:
                    self.stop_recording()
                except Exception:
                    self._logger.exception("Failed to finalize recording during shutdown")
            self.stop_proxy()
        finally:
            try:
                self.system_proxy.recover_stale_proxy()
            except Exception:
                self._logger.exception("Failed to recover the Windows system proxy during shutdown")
            finally:
                self._logger.removeHandler(self._log_handler)
