from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from lli.config import LLIConfig
from lli.runtime import ProxyRuntime
from lli.watch import WatchState
from lli.windows import SystemProxyStatus


class IdleWatchManager:
    state = WatchState.IDLE

    def __init__(self) -> None:
        self.shutdown_calls = 0
        self.initialize_calls = 0
        self.output_dir = Path("traces")
        self.current_session_id: str | None = None

    def initialize(self) -> None:
        self.initialize_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FailingShutdownWatchManager(IdleWatchManager):
    def shutdown(self) -> None:
        self.shutdown_calls += 1
        raise RuntimeError("watch shutdown failure")


class RecordingWatchManager(IdleWatchManager):
    state = WatchState.RECORDING


class MemorySystemProxy:
    def __init__(self) -> None:
        self.deactivate_calls = 0
        self.recover_calls = 0

    def deactivate(self) -> None:
        self.deactivate_calls += 1

    def recover_stale_proxy(self) -> None:
        self.recover_calls += 1

    def status(self) -> SystemProxyStatus:
        return SystemProxyStatus(True, False, False, None)


class OrderedSystemProxy(MemorySystemProxy):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def deactivate(self) -> None:
        self.events.append("deactivate")
        super().deactivate()


class BlockingSystemProxy(MemorySystemProxy):
    def __init__(self) -> None:
        super().__init__()
        self.activation_started = threading.Event()
        self.allow_activation = threading.Event()

    def activate(self, host: str, port: int) -> None:
        self.activation_started.set()
        assert self.allow_activation.wait(timeout=2)


class FakeCertificates:
    def status(self) -> dict[str, bool]:
        return {"supported": False, "installed": False}


class CooperativeMaster:
    def __init__(self) -> None:
        self.shutdown_calls = 0
        self.stopped = threading.Event()

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.stopped.set()


class FailingMaster:
    def shutdown(self) -> None:
        raise RuntimeError("mitmproxy shutdown failure")


class WaitingThread:
    def __init__(self) -> None:
        self.join_calls = 0

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        self.join_calls += 1


class TrackingMaster:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class OrderedMaster:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def shutdown(self) -> None:
        self.events.append("shutdown")


def test_stop_proxy_restores_system_proxy_after_shutdown_failure() -> None:
    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    system_proxy = MemorySystemProxy()
    runtime.system_proxy = system_proxy  # type: ignore[assignment]
    runtime._initialized = True
    runtime._master = FailingMaster()

    with pytest.raises(RuntimeError, match="mitmproxy shutdown failure"):
        runtime.stop_proxy()

    assert system_proxy.deactivate_calls == 1
    assert watch_manager.shutdown_calls == 1


def test_stop_proxy_restores_system_proxy_before_shutdown() -> None:
    events: list[str] = []
    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    runtime.system_proxy = OrderedSystemProxy(events)  # type: ignore[assignment]
    runtime._initialized = True
    runtime._system_proxy_active = True
    runtime._master = OrderedMaster(events)

    runtime.stop_proxy()

    assert events[:2] == ["deactivate", "shutdown"]
    assert runtime._system_proxy_active is False


def test_shutdown_recovers_and_removes_log_handler_after_stop_failure() -> None:
    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    system_proxy = MemorySystemProxy()
    runtime.system_proxy = system_proxy  # type: ignore[assignment]
    runtime._initialized = True
    runtime._master = FailingMaster()

    with pytest.raises(RuntimeError, match="mitmproxy shutdown failure"):
        runtime.shutdown()

    assert system_proxy.recover_calls == 1
    assert runtime._log_handler not in runtime._logger.handlers


def test_stop_proxy_requires_recording_to_end_first() -> None:
    runtime = ProxyRuntime(LLIConfig(), RecordingWatchManager())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="Stop recording before stopping the proxy"):
        runtime.stop_proxy()


def test_stop_proxy_retains_runtime_ownership_when_worker_does_not_exit() -> None:
    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    system_proxy = MemorySystemProxy()
    runtime.system_proxy = system_proxy  # type: ignore[assignment]
    thread = WaitingThread()
    master = TrackingMaster()
    runtime._thread = thread  # type: ignore[assignment]
    runtime._master = master
    runtime._initialized = True

    with pytest.raises(RuntimeError, match="did not stop within 8 seconds"):
        runtime.stop_proxy()

    assert master.shutdown_calls == 1
    assert thread.join_calls == 1
    assert runtime._thread is thread
    assert runtime._master is master
    assert runtime._initialized is True
    assert system_proxy.deactivate_calls == 0
    assert watch_manager.shutdown_calls == 0


def test_unexpected_worker_exit_restores_proxy_and_closes_watch_manager() -> None:
    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    system_proxy = MemorySystemProxy()
    runtime.system_proxy = system_proxy  # type: ignore[assignment]
    worker = WaitingThread()
    runtime._thread = worker  # type: ignore[assignment]
    runtime._initialized = True
    runtime._system_proxy_active = True

    runtime._cleanup_after_worker_exit(worker)  # type: ignore[arg-type]

    assert system_proxy.deactivate_calls == 1
    assert watch_manager.shutdown_calls == 1
    assert runtime._thread is None
    assert runtime._initialized is False
    assert runtime._system_proxy_active is False


def test_stop_proxy_resets_initialization_when_watch_shutdown_fails() -> None:
    watch_manager = FailingShutdownWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    runtime.system_proxy = MemorySystemProxy()  # type: ignore[assignment]
    runtime._initialized = True

    with pytest.raises(RuntimeError, match="watch shutdown failure"):
        runtime.stop_proxy()

    assert runtime._thread is None
    assert runtime._initialized is False


def test_runtime_updates_relay_recognition_while_proxy_is_running() -> None:
    watch_manager = IdleWatchManager()
    config = LLIConfig()
    runtime = ProxyRuntime(config, watch_manager)  # type: ignore[arg-type]
    runtime.system_proxy = MemorySystemProxy()  # type: ignore[assignment]
    runtime.certificates = FakeCertificates()  # type: ignore[assignment]
    runtime._thread = WaitingThread()  # type: ignore[assignment]
    runtime._master = object()

    runtime.update_settings(config.proxy.port, "INFO", False)

    assert config.filter.auto_detect_api_paths is False


def test_stop_waits_for_start_to_publish_its_master(monkeypatch) -> None:
    import lli.proxy

    watch_manager = IdleWatchManager()
    runtime = ProxyRuntime(LLIConfig(), watch_manager)  # type: ignore[arg-type]
    system_proxy = BlockingSystemProxy()
    runtime.system_proxy = system_proxy  # type: ignore[assignment]
    runtime.certificates = FakeCertificates()  # type: ignore[assignment]
    master = CooperativeMaster()

    async def controlled_proxy(*args, on_ready, on_running, **kwargs):
        on_ready(master)
        on_running()
        await asyncio.to_thread(master.stopped.wait)

    monkeypatch.setattr(lli.proxy, "run_watch_proxy", controlled_proxy)
    start_error: list[Exception] = []
    stop_error: list[Exception] = []
    stop_done = threading.Event()

    def start() -> None:
        try:
            runtime.start_proxy()
        except Exception as exc:  # pragma: no cover - asserted below
            start_error.append(exc)

    def stop() -> None:
        try:
            runtime.stop_proxy()
        except Exception as exc:  # pragma: no cover - asserted below
            stop_error.append(exc)
        finally:
            stop_done.set()

    start_thread = threading.Thread(target=start)
    start_thread.start()
    assert system_proxy.activation_started.wait(timeout=2)

    stop_thread = threading.Thread(target=stop)
    stop_thread.start()
    assert not stop_done.wait(timeout=0.1)
    assert master.shutdown_calls == 0

    system_proxy.allow_activation.set()
    start_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not start_error
    assert not stop_error
    assert master.shutdown_calls == 1
    assert runtime._thread is None
    assert watch_manager.shutdown_calls == 1
