from __future__ import annotations

import pytest

from lli.config import LLIConfig
from lli.runtime import ProxyRuntime
from lli.watch import WatchState


class IdleWatchManager:
    state = WatchState.IDLE

    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


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


class FailingMaster:
    def shutdown(self) -> None:
        raise RuntimeError("mitmproxy shutdown failure")


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


def test_stop_proxy_requires_recording_to_end_first() -> None:
    runtime = ProxyRuntime(LLIConfig(), RecordingWatchManager())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="Stop recording before stopping the proxy"):
        runtime.stop_proxy()
