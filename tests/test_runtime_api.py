from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lli.runtime import RuntimeObservabilitySnapshot, RuntimeSnapshot
from lli.server import create_app
from lli.watch import WatchManager


class FakeRuntime:
    """In-memory runtime double used to verify the local control API contract."""

    def __init__(self) -> None:
        self.proxy_running = False
        self.recording = False
        self.profiles = ["chatgpt", "claude"]

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            proxy_running=self.proxy_running,
            proxy_host="127.0.0.1",
            proxy_port=9090,
            recording=self.recording,
            session_id="session_20260815_120000" if self.recording else None,
            system_proxy={
                "supported": True,
                "enabled": self.proxy_running,
                "managed_by_lli": self.proxy_running,
                "server": None,
            },
            certificate={
                "supported": True,
                "exists": True,
                "installed": False,
                "path": "C:/test.pem",
            },
            enabled_site_profiles=self.profiles,
            site_profiles=[
                {"id": "chatgpt", "name": "ChatGPT"},
                {"id": "claude", "name": "Claude"},
            ],
            output_dir="C:/traces",
            log_level="INFO",
            address_recognition=True,
            error=None,
        )

    def observability(self, after_sequence: int = 0) -> RuntimeObservabilitySnapshot:
        return RuntimeObservabilitySnapshot(
            heartbeat_at="2026-08-15T12:00:00+00:00",
            heartbeat_sequence=1,
            uptime_seconds=42,
            proxy_running=self.proxy_running,
            proxy_uptime_seconds=42 if self.proxy_running else 0,
            latest_log_sequence=1,
            logs=[],
        )

    def start_proxy(self) -> RuntimeSnapshot:
        self.proxy_running = True
        return self.snapshot()

    def stop_proxy(self) -> RuntimeSnapshot:
        if self.recording:
            raise RuntimeError("Stop recording before stopping the proxy")
        self.proxy_running = False
        return self.snapshot()

    def start_recording(self) -> RuntimeSnapshot:
        if not self.proxy_running:
            raise RuntimeError("Start the proxy before recording a session")
        self.recording = True
        return self.snapshot()

    def stop_recording(self) -> RuntimeSnapshot:
        self.recording = False
        return self.snapshot()

    def set_site_profiles(self, profile_ids: list[str]) -> RuntimeSnapshot:
        if self.proxy_running:
            raise RuntimeError("Stop the proxy before changing model website profiles")
        self.profiles = profile_ids
        return self.snapshot()

    def install_certificate(self) -> RuntimeSnapshot:
        return self.snapshot()

    def update_settings(
        self,
        proxy_port: int,
        log_level: str,
        address_recognition: bool,
    ) -> RuntimeSnapshot:
        assert proxy_port == 9191
        assert log_level == "DEBUG"
        assert address_recognition is True
        return self.snapshot()


def test_runtime_api_controls_proxy_and_recording(tmp_path: Path) -> None:
    watch_manager = WatchManager(output_dir=tmp_path)
    runtime = FakeRuntime()
    app = create_app(watch_manager, runtime=runtime)  # type: ignore[arg-type]
    client = TestClient(app)

    assert client.get("/api/runtime").json()["proxy_running"] is False
    assert client.post("/api/runtime/proxy/start").json()["proxy_running"] is True
    recording = client.post("/api/runtime/recording/start")
    assert recording.status_code == 200
    assert recording.json()["recording"] is True
    proxy_stop = client.post("/api/runtime/proxy/stop")
    assert proxy_stop.status_code == 409
    assert proxy_stop.json()["detail"] == "Stop recording before stopping the proxy"
    assert client.post("/api/runtime/recording/stop").json()["recording"] is False
    assert client.post("/api/runtime/proxy/stop").json()["proxy_running"] is False


def test_runtime_api_rejects_profile_changes_while_proxy_runs(tmp_path: Path) -> None:
    watch_manager = WatchManager(output_dir=tmp_path)
    runtime = FakeRuntime()
    app = create_app(watch_manager, runtime=runtime)  # type: ignore[arg-type]
    client = TestClient(app)

    client.post("/api/runtime/proxy/start")
    response = client.put("/api/runtime/site-profiles", json={"profile_ids": ["chatgpt"]})
    assert response.status_code == 409
    assert response.json()["detail"] == "Stop the proxy before changing model website profiles"


def test_runtime_api_exposes_observability_and_settings(tmp_path: Path) -> None:
    watch_manager = WatchManager(output_dir=tmp_path)
    runtime = FakeRuntime()
    client = TestClient(create_app(watch_manager, runtime=runtime))  # type: ignore[arg-type]

    observability = client.get("/api/runtime/observability")
    assert observability.status_code == 200
    assert observability.json()["heartbeat_sequence"] == 1
    assert observability.json()["uptime_seconds"] == 42
    assert observability.json()["proxy_running"] is False
    assert observability.json()["proxy_uptime_seconds"] == 0

    settings = client.put(
        "/api/runtime/settings",
        json={
            "proxy_port": 9191,
            "log_level": "DEBUG",
            "address_recognition": True,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["output_dir"] == "C:/traces"


def test_runtime_api_is_unavailable_without_desktop_runtime(tmp_path: Path) -> None:
    watch_manager = WatchManager(output_dir=tmp_path)
    app = create_app(watch_manager)
    response = TestClient(app).get("/api/runtime")
    assert response.status_code == 409
