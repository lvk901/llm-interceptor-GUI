from __future__ import annotations

from types import SimpleNamespace

from lli.config import LLIConfig
from lli.filters import URLFilter
from lli.proxy import NON_LLM_TUNNEL_HOSTS, WatchAddon, _get_ignore_hosts


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
    method: str = "GET",
    request_content: bytes = b"",
    request_content_type: str = "",
    response_headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(
            pretty_url=url,
            method=method,
            content=request_content,
            headers={"content-type": request_content_type},
        ),
        response=SimpleNamespace(
            headers=response_headers or {},
            status_code=200,
            content=b"",
            stream=False,
        ),
    )


def make_addon() -> tuple[WatchAddon, MemoryWatchManager]:
    config = LLIConfig()
    manager = MemoryWatchManager()
    return WatchAddon(config, manager, URLFilter(config.filter)), manager


def test_bilibili_grpc_is_marked_as_non_llm_tunnel_host() -> None:
    assert "grpc.biliapi.net" in NON_LLM_TUNNEL_HOSTS


def test_non_llm_tunnel_hosts_preserve_custom_bypasses() -> None:
    config = LLIConfig()
    config.proxy.no_proxy = ["localhost", "grpc.biliapi.net"]

    assert _get_ignore_hosts(config) == ["localhost", "grpc.biliapi.net"]


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


def test_flow_diagnostics_redact_query_and_track_media_route() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://cdn.example/video.mp4?token=secret-value",
        response_headers={"content-type": "video/mp4", "content-length": "1024"},
    )

    addon.request(flow)
    diagnostic = addon._flow_diagnostics[id(flow)]
    assert diagnostic.endpoint == "https://cdn.example/video.mp4"
    assert diagnostic.route == "PASSTHROUGH"
    assert diagnostic.request_bytes == 0

    addon.responseheaders(flow)
    assert diagnostic.headers_at is not None
    assert diagnostic.streamed is True
    addon.response(flow)

    assert manager.records == []
    assert id(flow) not in addon._flow_diagnostics


def test_non_model_large_response_streams_without_capture() -> None:
    addon, _ = make_addon()
    flow = make_flow(
        "https://cdn.example/download.bin",
        response_headers={"content-type": "application/octet-stream", "content-length": "524288"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True


def test_non_model_grpc_response_streams_without_capture() -> None:
    """gRPC metadata and auxiliary calls must not wait for full response buffering."""
    addon, manager = make_addon()
    flow = make_flow(
        "https://grpc.biliapi.net/bilibili.app.view.v1.View/View",
        method="POST",
        request_content=b"protobuf-payload",
        request_content_type="application/grpc+proto",
        response_headers={"content-type": "application/grpc+proto"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []


def test_unmatched_json_feed_streams_without_capture() -> None:
    """Generic read-only feed JSON must not be buffered by the interceptor."""
    addon, manager = make_addon()
    flow = make_flow(
        "https://news.example/api/v1/feed/latest",
        response_headers={"content-type": "application/json; charset=utf-8"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []
    assert addon._capture_decisions[id(flow)] is False


def test_unversioned_ai_get_is_captured_before_json_passthrough() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://summary.example/api/video-summary",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"
    assert addon._capture_decisions[id(flow)] is True


def test_unmatched_json_post_is_not_streamed_without_llm_classification() -> None:
    """Opaque POST payloads stay buffered until their body can be classified."""
    addon, manager = make_addon()
    flow = make_flow(
        "https://news.example/api/v1/feed/latest",
        method="POST",
        request_content=b"opaque",
        request_content_type="application/json",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records == []


def test_llm_endpoint_is_not_streamed_when_response_is_media() -> None:
    """LLM classification wins even if an upstream returns a large media body."""
    addon, manager = make_addon()
    flow = make_flow(
        "https://relay.example/v1/responses",
        request_content=b'{"model":"gpt-test","input":"summarize"}',
        request_content_type="application/json",
        response_headers={
            "content-type": "video/mp4",
            "content-length": "10485760",
        },
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"
    assert addon._capture_decisions[id(flow)] is True


def test_responseheaders_reuses_request_capture_decision(monkeypatch) -> None:
    """A runtime filter change must not flip a flow after request handling."""
    addon, manager = make_addon()
    flow = make_flow(
        "https://news.example/api/v1/feed/latest",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    assert addon._capture_decisions[id(flow)] is False

    # Simulate a settings update between request and responseheaders.
    monkeypatch.setattr(addon.url_filter, "should_capture", lambda _url: True)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []


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


def test_bilibili_feed_response_streams_without_capture() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://api.bilibili.com/v1/feed/index",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []
    assert addon._capture_decisions[id(flow)] is False


def test_bilibili_video_cdn_response_streams_without_capture() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://cn-hb.bilivideo.com/upgcxcode/segment.m4s",
        response_headers={"content-type": "video/iso.segment"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []


def test_bilibili_ai_summary_remains_captured() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://api.bilibili.com/v1/ai/video-summary",
        request_content=b'{"model":"bili-ai","input":"video-123"}',
        request_content_type="application/json",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"
    assert addon._capture_decisions[id(flow)] is True


def test_bilibili_ai_path_is_captured_without_llm_shaped_payload() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://api.bilibili.com/x/ai/video-summary",
        request_content=b'{"bvid":"BV123"}',
        request_content_type="application/json",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"
    assert addon._capture_decisions[id(flow)] is True


def test_bilibili_post_feed_path_is_not_streamed_without_ai_classification() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://api.bilibili.com/x/feed/recommend",
        method="POST",
        request_content=b"opaque-payload",
        request_content_type="application/octet-stream",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records == []


def test_bilibili_feed_with_ai_payload_remains_captured() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://api.bilibili.com/v1/feed/recommend",
        request_content=b'{"model":"bili-ai","messages":[]}',
        request_content_type="application/json",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"


def test_bilibili_feed_with_unparsed_large_body_is_not_bypassed() -> None:
    addon, manager = make_addon()
    large_body = (b'{"model":"' + b"x" * (1024 * 1024 + 1) + b'"}')
    flow = make_flow(
        "https://api.bilibili.com/v1/feed/recommend",
        request_content=large_body,
        request_content_type="application/json",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"


def test_ordinary_versioned_feed_is_not_captured_or_buffered_as_model_traffic() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://news.example/v1/feed/latest",
        response_headers={"content-type": "application/json"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is True
    assert manager.records == []


def test_explicit_llm_endpoint_remains_captured_after_version_path_tightening() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://relay.example/v1/chat/completions",
        method="POST",
        request_content=b'{"model":"gpt-test","messages":[]}',
        request_content_type="application/json",
        response_headers={"content-type": "text/event-stream"},
    )

    addon.request(flow)
    addon.responseheaders(flow)

    assert flow.response.stream is False
    assert manager.records[0]["type"] == "request"


def test_response_uses_request_capture_decision_without_rechecking_filter(monkeypatch) -> None:
    addon, manager = make_addon()
    flow = make_flow("https://relay.example/v1/chat/completions")
    addon.request(flow)

    def fail_if_rechecked(*args, **kwargs):
        raise AssertionError("URL filter should not be called again for a tracked flow")

    monkeypatch.setattr(addon.url_filter, "should_capture", fail_if_rechecked)
    flow.response.headers = {"content-type": "application/json"}
    flow.response.content = b'{}'
    addon.response(flow)

    assert [record["type"] for record in manager.records] == ["request", "response"]


def test_url_matched_request_persists_masked_json_body() -> None:
    addon, manager = make_addon()
    addon.masking_config.sensitive_body_fields = ["api_key"]
    flow = make_flow(
        "https://api.openai.com/v1/responses",
        request_content=(
            b'{"model":"gpt-test","input":"hello","api_key":"secret-value"}'
        ),
        request_content_type="application/json",
    )

    addon.request(flow)

    assert manager.records[0]["body"] == {
        "model": "gpt-test",
        "input": "hello",
        "api_key": "***MASKED***",
    }


def test_url_matched_request_body_is_not_limited_by_relay_detection_size() -> None:
    addon, manager = make_addon()
    large_input = "x" * (1024 * 1024 + 1)
    flow = make_flow(
        "https://api.openai.com/v1/responses",
        request_content=(f'{{"model":"gpt-test","input":"{large_input}"}}').encode(),
        request_content_type="application/json",
    )

    addon.request(flow)

    assert manager.records[0]["body"] == {"model": "gpt-test", "input": large_input}


def test_large_unmatched_json_is_not_probed_as_a_relay() -> None:
    addon, manager = make_addon()
    flow = make_flow(
        "https://gateway.example/api/catalog",
        request_content=b"{" + b"x" * (1024 * 1024 + 1) + b"}",
        request_content_type="application/json",
    )

    addon.request(flow)

    assert manager.records == []
    assert addon._request_ids == {}


def test_error_flow_cleans_captured_request_tracking() -> None:
    addon, _ = make_addon()
    flow = make_flow("https://api.openai.com/v1/responses")

    addon.request(flow)
    addon.error(flow)

    assert addon._request_ids == {}
    assert addon._request_times == {}
    assert addon._request_sessions == {}
    assert addon._capture_decisions == {}


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
