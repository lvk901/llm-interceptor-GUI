from __future__ import annotations

from types import SimpleNamespace

from lli.config import LLIConfig
from lli.filters import URLFilter
from lli.proxy import WatchAddon


class MemoryWatchManager:
    """Minimal watch manager used to verify proxy routing decisions."""

    current_session_id = "session_test"

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write_record(self, record: dict[str, object], session_id: str | None = None) -> None:
        self.records.append(record)


def make_flow(
    url: str,
    *,
    request_content: bytes = b"",
    request_content_type: str = "",
    response_headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(
            pretty_url=url,
            method="GET",
            content=request_content,
            headers={"content-type": request_content_type},
        ),
        response=SimpleNamespace(headers=response_headers or {}, stream=False),
    )


def make_addon() -> tuple[WatchAddon, MemoryWatchManager]:
    config = LLIConfig()
    manager = MemoryWatchManager()
    return WatchAddon(config, manager, URLFilter(config.filter)), manager


def test_non_model_media_response_streams_without_capture() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://media.example/video.mp4",
        response_headers={"content-type": "video/mp4"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []
    assert addon._request_ids == {}


def test_non_model_large_response_streams_without_capture() -> None:
    addon, _ = make_addon()
    flow = make_flow(
        "https://cdn.example/download.bin",
        response_headers={"content-type": "application/octet-stream", "content-length": "524288"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True


def test_model_response_is_buffered_and_captured() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://beeapi.ai/v1/responses",
        request_content=b'{"model":"gpt-test","input":"hello"}',
        request_content_type="application/json",
        response_headers={"content-type": "text/event-stream"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"


def test_non_json_unmatched_upload_is_not_parsed() -> None:
    addon, _ = make_addon()
    flow = make_flow(
        "https://upload.example/file",
        request_content=b"not-json",
        request_content_type="application/octet-stream",
    )

    assert addon._should_parse_unmatched_request(flow) is False


def test_passthrough_activity_is_limited_per_host(monkeypatch) -> None:
    addon, _ = make_addon()
    now = 100.0
    monkeypatch.setattr("lli.proxy.time.monotonic", lambda: now)

    addon._log_passthrough_activity("GET", "https://video.example/one?secret=value")
    addon._log_passthrough_activity("GET", "https://video.example/two?secret=value")

    assert addon._passthrough_log_times == {"video.example": 100.0}

    now += 3.0
    addon._log_passthrough_activity("GET", "https://video.example/three")

    assert addon._passthrough_log_times == {"video.example": 103.0}
