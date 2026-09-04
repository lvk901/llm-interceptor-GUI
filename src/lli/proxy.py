"""
mitmproxy addon for LLM Interceptor.

Handles traffic interception, data capture, and sensitive data masking.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

from mitmproxy import http
from mitmproxy.options import Options
from mitmproxy.tls import TlsData
from mitmproxy.tools.dump import DumpMaster

from lli.config import LLIConfig
from lli.filters import URLFilter
from lli.logger import (
    get_logger,
    log_request_summary,
    log_streaming_progress,
    log_tls_handshake_failure,
)

if TYPE_CHECKING:
    from lli.watch import WatchManager


MAX_UNMATCHED_REQUEST_BODY_BYTES = 1024 * 1024
STREAM_PASSTHROUGH_MIN_BYTES = 256 * 1024
PASSTHROUGH_LOG_INTERVAL_SECONDS = 3.0
STREAM_PASSTHROUGH_CONTENT_TYPES = (
    "audio/",
    "font/",
    "image/",
    "video/",
    "application/octet-stream",
    "application/grpc",
    "application/vnd.apple.mpegurl",
    "application/dash+xml",
)
BILIBILI_AI_PATH_RE = re.compile(
    r"(?:^|/)(?:ai|aigc|assistant|copilot|chat|completion|generate|llm|model|"
    r"summary|summarize|transcript|ask)(?:[/_.?=-]|$)",
    re.IGNORECASE,
)
BILIBILI_FEED_PATH_RE = re.compile(
    r"(?:^|/)(?:feed|dynamic|recommend|recommendation|rcmd|popular|region)(?:[/_.?=-]|$)",
    re.IGNORECASE,
)
BILIBILI_MEDIA_PATH_RE = re.compile(
    r"(?:^|/)(?:playurl|playurlconf|upgcxcode|download|segment|dash)(?:[/_.?=-]|$)|"
    r"\.(?:m3u8|mpd|mp4|ts|m4s|flv)(?:$|\?)",
    re.IGNORECASE,
)
AI_BODY_KEYS = frozenset(
    {
        "messages",
        "input",
        "instructions",
        "prompt",
        "tools",
        "contents",
        "content",
        "query",
    }
)

# Bilibili's protobuf RPC endpoints rely on HTTP/2 trailers that cannot be
# safely MITM'd by the local proxy. They are media/control traffic, not AI
# endpoints; tunnel this host so playback keeps the native gRPC connection.
NON_LLM_TUNNEL_HOSTS = ("grpc.biliapi.net",)


def _get_ignore_hosts(config: LLIConfig) -> list[str]:
    """Combine user bypass rules with safe non-LLM protocol tunnels."""
    ignore_hosts = list(config.proxy.no_proxy or [])
    for host in NON_LLM_TUNNEL_HOSTS:
        if host not in ignore_hosts:
            ignore_hosts.append(host)
    return ignore_hosts


@dataclass
class _FlowDiagnostics:
    """Timing and routing metadata for one flow, excluding request contents."""

    token: str
    started_at: float
    method: str
    endpoint: str
    route: str
    request_bytes: int
    captured: bool
    headers_at: float | None = None
    completed_at: float | None = None
    streamed: bool = False


class WatchAddon:
    """
    mitmproxy addon for watch mode.

    Writes records through WatchManager for session-aware logging
    with automatic session ID injection.
    """

    def __init__(
        self,
        config: LLIConfig,
        watch_manager: WatchManager,
        url_filter: URLFilter,
    ):
        """
        Initialize the watch addon.

        Args:
            config: LLI configuration
            watch_manager: WatchManager instance for session management
            url_filter: URL filter for traffic selection
        """
        self.config = config
        self.watch_manager = watch_manager
        self.url_filter = url_filter
        self.masking_config = config.masking
        self._logger = get_logger()

        # Track in-flight requests
        self._request_times: dict[int, float] = {}
        self._request_ids: dict[int, str] = {}
        self._request_sessions: dict[int, str | None] = {}
        self._capture_decisions: dict[int, bool] = {}
        self._bilibili_ai_decisions: dict[int, bool] = {}
        self._bilibili_passthrough_decisions: dict[int, bool] = {}
        self._streamed_passthrough: set[int] = set()
        self._flow_diagnostics: dict[int, _FlowDiagnostics] = {}
        self._passthrough_log_times: dict[str, float] = {}

    def request(self, flow: http.HTTPFlow) -> None:
        """Handle an outgoing request."""
        url = flow.request.pretty_url
        method = flow.request.method

        self._logger.debug("Intercepted request: %s %s", method, self._safe_endpoint(url))

        body = None
        # An unversioned AI path is only sufficient evidence when the request is
        # genuinely body-less. Passing an empty mapping for body-bearing flows
        # prevents a large, unparsed upload from being captured (or bypassed)
        # solely because its path contains ``inference`` or ``summary``.
        should_capture = self.url_filter.should_capture(url, {} if flow.request.content else None)
        if self._is_bilibili_url(url) and self._should_parse_unmatched_request(flow):
            # Bilibili uses generic /v1 paths for both media/feed APIs and AI APIs.
            # Inspect only small JSON requests so the distinction does not require
            # buffering media or large uploads.
            body = self._parse_body(flow.request.content, flow.request.headers.get("content-type"))
        if not should_capture and body is None and self._should_parse_unmatched_request(flow):
            # Inspect JSON relay payloads without decoding uploads or binary application traffic.
            body = self._parse_body(flow.request.content, flow.request.headers.get("content-type"))
            should_capture = self.url_filter.should_capture(url, body)

        flow_id = id(flow)
        bilibili_ai = self._is_bilibili_ai_request(url, body)
        self._bilibili_ai_decisions[flow_id] = bilibili_ai
        safe_bilibili_passthrough = (
            self._is_bilibili_url(url)
            and not bilibili_ai
            and self._is_bilibili_passthrough_request(url)
            and flow.request.method.upper() in {"GET", "HEAD", "OPTIONS"}
            and (not flow.request.content or isinstance(body, dict))
        )
        self._bilibili_passthrough_decisions[flow_id] = safe_bilibili_passthrough
        if bilibili_ai:
            # AI endpoints must remain observable even when their payload does not
            # use the usual model/messages shape and URLFilter would not match it.
            should_capture = True
        elif (
            self._is_bilibili_passthrough_request(url)
            and flow.request.content
            and body is None
            and "json" in flow.request.headers.get("content-type", "").lower()
        ):
            # An oversized JSON body cannot be inspected safely at request time;
            # retain the flow so a possible AI call is not silently bypassed.
            should_capture = True
        if (
            should_capture
            and safe_bilibili_passthrough
        ):
            should_capture = False

        route = self._diagnostic_route(url, should_capture, bilibili_ai, bool(flow.request.content))
        self._flow_diagnostics[flow_id] = _FlowDiagnostics(
            token=f"{flow_id:x}"[-10:],
            started_at=time.monotonic(),
            method=method,
            endpoint=self._safe_endpoint(url),
            route=route,
            request_bytes=len(flow.request.content or b""),
            captured=should_capture,
        )
        self._logger.debug(
            "[FLOW] request id=%s route=%s %s %s body=%dB",
            self._flow_diagnostics[flow_id].token,
            route,
            method,
            self._safe_endpoint(url),
            len(flow.request.content or b""),
        )
        hostname = (urlsplit(url).hostname or "").casefold()
        if hostname.startswith("grpc.") or hostname.endswith(".grpc.biliapi.net"):
            self._logger.info(
                "[FLOW-GRPC] request id=%s route=%s %s body=%dB %s",
                self._flow_diagnostics[flow_id].token,
                route,
                method,
                len(flow.request.content or b""),
                self._safe_endpoint(url),
            )

        if not should_capture:
            # Keep the decision explicit: URLFilter may recognize the same generic
            # /v1 path again in responseheaders/response, but this flow is known
            # to be ordinary Bilibili traffic.
            self._capture_decisions[flow_id] = False
            self._log_passthrough_activity(method, url)
            self._logger.debug("URL not matched, skipping: %s", self._safe_endpoint(url))
            return

        # Generate IDs, capture timing, and emit live logs only for model traffic.
        request_id = str(uuid4())
        self._request_ids[flow_id] = request_id
        self._request_times[flow_id] = time.time()
        self._capture_decisions[flow_id] = True
        log_request_summary(method, url, captured=True)

        # Capture current session ID for this request
        session_id = self.watch_manager.current_session_id
        self._request_sessions[flow_id] = session_id

        # Parse headers (with masking)
        headers = self._mask_headers(dict(flow.request.headers))

        # Relay detection may already have parsed this body. All captured requests
        # still need their payload persisted, including those matched by URL.
        if body is None:
            body = self._parse_body(flow.request.content, flow.request.headers.get("content-type"))

        # Mask sensitive body fields if configured
        if isinstance(body, dict):
            body = self._mask_body_fields(body)

        # Create request record
        record = {
            "type": "request",
            "id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "url": url,
            "headers": headers,
            "body": body,
        }

        self.watch_manager.write_record(record, session_id=session_id)
        self._logger.debug("Captured request %s to %s", request_id[:8], url)

    def response(self, flow: http.HTTPFlow) -> None:
        """Handle a response."""
        url = flow.request.pretty_url
        method = flow.request.method
        status_code = flow.response.status_code

        flow_id = id(flow)
        if flow_id in self._streamed_passthrough:
            self._logger.debug("Completed streamed passthrough: %s", url)
            self._streamed_passthrough.discard(flow_id)
            self._log_flow_completion(flow)
            self._cleanup_flow(flow_id)
            return

        # Reuse the request's decision when JSON payload recognition triggered capture.
        should_capture = self._capture_decisions.get(flow_id)
        if should_capture is None:
            should_capture = self.url_filter.should_capture(url)

        if not should_capture:
            # Cleanup and exit early if not capturing
            self._cleanup_flow(flow_id)
            return

        request_id = self._request_ids.get(flow_id, str(uuid4()))
        start_time = self._request_times.get(flow_id, time.time())
        latency_ms = (time.time() - start_time) * 1000
        log_request_summary(method, url, status_code, latency_ms, captured=True)

        # Use the session ID from the request start
        session_id = self._request_sessions.get(flow_id)

        # Check if this is a streaming response
        content_type = flow.response.headers.get("content-type", "")
        is_streaming = "text/event-stream" in content_type

        self._logger.debug(
            "Response received: %s %s (streaming=%s, content-type=%s)",
            status_code,
            url,
            is_streaming,
            content_type,
        )

        if is_streaming:
            # For streaming SSE responses, parse the complete body into chunks
            sse_events = self._parse_sse_body(flow.response.content)

            # Write individual chunk records for each SSE event
            for chunk_index, event_content in enumerate(sse_events):
                chunk_record = {
                    "type": "response_chunk",
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status_code": status_code,
                    "chunk_index": chunk_index,
                    "content": event_content,
                }
                self.watch_manager.write_record(chunk_record, session_id=session_id)
                log_streaming_progress(request_id, chunk_index)

            # Write meta record with chunk count
            meta_record = {
                "type": "response_meta",
                "request_id": request_id,
                "total_latency_ms": latency_ms,
                "status_code": status_code,
                "total_chunks": len(sse_events),
            }
            self.watch_manager.write_record(meta_record, session_id=session_id)
        else:
            # Non-streaming response - capture complete body
            headers = self._mask_headers(dict(flow.response.headers))
            body = self._parse_body(
                flow.response.content, flow.response.headers.get("content-type")
            )

            record = {
                "type": "response",
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status_code": status_code,
                "headers": headers,
                "body": body,
                "latency_ms": latency_ms,
            }
            self.watch_manager.write_record(record, session_id=session_id)

        # Cleanup
        self._log_flow_completion(flow)
        self._cleanup_flow(flow_id)

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """Handle response headers (called before body is received)."""
        flow_id = id(flow)
        should_capture = self._capture_decisions.get(flow_id)
        if should_capture is None:
            should_capture = self.url_filter.should_capture(flow.request.pretty_url)
        will_stream = (
            not should_capture
            and (
                self._should_stream_passthrough(flow)
                or self._should_stream_bilibili_non_ai(flow)
                or self._should_stream_unmatched_json(flow)
            )
        )
        diagnostic = self._flow_diagnostics.get(flow_id)
        if diagnostic is not None:
            diagnostic.headers_at = time.monotonic()
            diagnostic.streamed = will_stream
            content_type = flow.response.headers.get("content-type", "")
            content_length = flow.response.headers.get("content-length", "?")
            content_range = flow.response.headers.get("content-range", "-")
            ttfb_ms = (diagnostic.headers_at - diagnostic.started_at) * 1000
            self._logger.debug(
                "[FLOW] headers id=%s route=%s status=%s ttfb=%.0fms stream=%s "
                "type=%s length=%s range=%s",
                diagnostic.token,
                diagnostic.route,
                getattr(flow.response, "status_code", "?"),
                ttfb_ms,
                will_stream,
                content_type or "-",
                content_length,
                content_range,
            )
            if not should_capture and ttfb_ms >= 500:
                self._logger.info(
                    "[FLOW-SLOW] headers id=%s %s ttfb=%.0fms stream=%s type=%s",
                    diagnostic.token,
                    diagnostic.endpoint,
                    ttfb_ms,
                    will_stream,
                    content_type or "-",
                )
            if not should_capture and content_type.lower().startswith("application/grpc"):
                self._logger.info(
                    "[FLOW-GRPC] headers id=%s status=%s ttfb=%.0fms stream=%s type=%s",
                    diagnostic.token,
                    getattr(flow.response, "status_code", "?"),
                    ttfb_ms,
                    will_stream,
                    content_type or "-",
                )
        if will_stream:
            flow.response.stream = True
            self._streamed_passthrough.add(flow_id)
            return

        content_type = flow.response.headers.get("content-type", "")
        if should_capture and "text/event-stream" in content_type:
            self._logger.debug("Detected streaming response for %s", flow.request.pretty_url)

    def error(self, flow: http.HTTPFlow) -> None:
        """Discard request tracking when mitmproxy ends a flow with an error."""
        diagnostic = self._flow_diagnostics.get(id(flow))
        if diagnostic is not None:
            elapsed_ms = (time.monotonic() - diagnostic.started_at) * 1000
            header_ms = (
                (diagnostic.headers_at - diagnostic.started_at) * 1000
                if diagnostic.headers_at is not None
                else None
            )
            self._logger.warning(
                "[FLOW-ERROR] id=%s %s elapsed=%.0fms headers=%s stream=%s error=%s",
                diagnostic.token,
                diagnostic.endpoint,
                elapsed_ms,
                f"{header_ms:.0f}ms" if header_ms is not None else "?",
                diagnostic.streamed,
                getattr(getattr(flow, "error", None), "msg", None) or "unknown",
            )
        self._streamed_passthrough.discard(id(flow))
        self._cleanup_flow(id(flow))

    def done(self, flow: http.HTTPFlow) -> None:
        """Release any residual flow state after a streamed response completes."""
        if id(flow) in self._flow_diagnostics:
            self._log_flow_completion(flow)
        self._streamed_passthrough.discard(id(flow))
        self._cleanup_flow(id(flow))

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        """Return a query-free endpoint suitable for diagnostic logs."""
        parsed = urlsplit(url)
        host = parsed.hostname or "unknown-host"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        path = parsed.path or "/"
        if len(path) > 240:
            path = f"{path[:237]}..."
        return f"{parsed.scheme or 'http'}://{host}{path}"

    @classmethod
    def _diagnostic_route(
        cls,
        url: str,
        should_capture: bool,
        bilibili_ai: bool,
        has_request_body: bool,
    ) -> str:
        if should_capture or bilibili_ai:
            return "LLM"
        if cls._is_bilibili_url(url) and cls._is_bilibili_passthrough_request(url):
            return "MEDIA"
        if not has_request_body:
            return "PASSTHROUGH"
        return "UNMATCHED"

    def _log_flow_completion(self, flow: http.HTTPFlow) -> None:
        """Log end-to-end timing without touching or serializing response bodies."""
        diagnostic = self._flow_diagnostics.get(id(flow))
        if diagnostic is None or diagnostic.completed_at is not None:
            return
        diagnostic.completed_at = time.monotonic()
        header_ms = (
            (diagnostic.headers_at - diagnostic.started_at) * 1000
            if diagnostic.headers_at is not None
            else None
        )
        total_ms = (diagnostic.completed_at - diagnostic.started_at) * 1000
        body = getattr(getattr(flow, "response", None), "content", None)
        body_bytes = len(body) if isinstance(body, bytes) else 0
        response = getattr(flow, "response", None)
        content_type = response.headers.get("content-type", "-") if response else "-"
        content_length = response.headers.get("content-length", "?") if response else "?"
        self._logger.debug(
            "[FLOW] complete id=%s route=%s status=%s total=%.0fms headers=%s "
            "stream=%s body=%dB declared=%s type=%s",
            diagnostic.token,
            diagnostic.route,
            getattr(response, "status_code", "?"),
            total_ms,
            f"{header_ms:.0f}ms" if header_ms is not None else "?",
            diagnostic.streamed,
            body_bytes,
            content_length,
            content_type,
        )
        if not diagnostic.captured and total_ms >= 500:
            self._logger.info(
                "[FLOW-SLOW] complete id=%s %s total=%.0fms headers=%s stream=%s body=%dB",
                diagnostic.token,
                diagnostic.endpoint,
                total_ms,
                f"{header_ms:.0f}ms" if header_ms is not None else "?",
                diagnostic.streamed,
                body_bytes,
            )

    @staticmethod
    def _is_bilibili_url(url: str) -> bool:
        """Return whether a URL belongs to Bilibili or its video CDN."""
        hostname = urlsplit(url).hostname
        if not hostname:
            return False
        hostname = hostname.casefold().rstrip(".")
        return any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in (
                "bilibili.com",
                "bilibili.tv",
                "bilibili.co",
                "bilivideo.com",
                "bilivideo.cn",
                "acgvideo.com",
            )
        )

    @classmethod
    def _is_bilibili_ai_request(cls, url: str, body: Any) -> bool:
        """Identify Bilibili AI calls that must remain captured and buffered."""
        if not cls._is_bilibili_url(url):
            return False
        path = urlsplit(url).path
        if BILIBILI_AI_PATH_RE.search(path):
            return True
        return (
            isinstance(body, dict)
            and isinstance(body.get("model"), str)
            and bool(AI_BODY_KEYS.intersection(body))
        )

    @classmethod
    def _is_bilibili_passthrough_request(cls, url: str) -> bool:
        """Identify ordinary Bilibili media/feed endpoints for zero-copy streaming."""
        path = urlsplit(url).path
        return bool(BILIBILI_FEED_PATH_RE.search(path) or BILIBILI_MEDIA_PATH_RE.search(path))

    def _should_stream_bilibili_non_ai(self, flow: http.HTTPFlow) -> bool:
        """Stream ordinary Bilibili media/feed responses while retaining AI analysis."""
        url = flow.request.pretty_url
        if not self._is_bilibili_url(url):
            return False
        flow_id = id(flow)
        if flow_id in self._bilibili_passthrough_decisions:
            return self._bilibili_passthrough_decisions[flow_id]
        if flow.request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            return False
        if self._bilibili_ai_decisions.get(flow_id, self._is_bilibili_ai_request(url, None)):
            return False
        path = urlsplit(url).path
        if self._is_bilibili_passthrough_request(url):
            return not flow.request.content
        content_type = flow.response.headers.get("content-type", "").lower()
        return (
            not flow.request.content
            and "json" in content_type
            and bool(BILIBILI_FEED_PATH_RE.search(path))
        )

    @staticmethod
    def _should_parse_unmatched_request(flow: http.HTTPFlow) -> bool:
        """Limit relay detection to reasonably sized JSON request bodies."""
        content = flow.request.content
        if not content or len(content) > MAX_UNMATCHED_REQUEST_BODY_BYTES:
            return False
        content_type = flow.request.headers.get("content-type", "").lower()
        return "json" in content_type

    @staticmethod
    def _should_stream_passthrough(flow: http.HTTPFlow) -> bool:
        """Stream non-model media and large downloads without buffering their bodies."""
        headers = flow.response.headers
        content_type = headers.get("content-type", "").lower()
        if content_type.startswith(STREAM_PASSTHROUGH_CONTENT_TYPES):
            return True
        if "content-range" in headers:
            return True
        try:
            return int(headers.get("content-length", "0")) >= STREAM_PASSTHROUGH_MIN_BYTES
        except ValueError:
            return False

    @staticmethod
    def _should_stream_unmatched_json(flow: http.HTTPFlow) -> bool:
        """Stream ordinary read-only JSON responses that were not captured.

        Feed and news APIs commonly return JSON from generic paths (including
        versioned paths that are not LLM endpoints).  The request phase has
        already classified the flow as non-LLM; limiting this fallback to
        body-less read methods keeps POST-based AI requests conservative while
        avoiding full buffering for ordinary feeds.
        """
        if flow.request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            return False
        if flow.request.content:
            return False
        content_type = flow.response.headers.get("content-type", "").lower()
        return "json" in content_type

    def _log_passthrough_activity(self, method: str, url: str) -> None:
        """Show bounded non-model connection activity without logging every media segment."""
        host = urlsplit(url).netloc or "unknown-host"
        now = time.monotonic()
        last_logged = self._passthrough_log_times.get(host)
        if last_logged is not None and now - last_logged < PASSTHROUGH_LOG_INTERVAL_SECONDS:
            return

        if len(self._passthrough_log_times) >= 512:
            cutoff = now - PASSTHROUGH_LOG_INTERVAL_SECONDS
            self._passthrough_log_times = {
                name: timestamp
                for name, timestamp in self._passthrough_log_times.items()
                if timestamp >= cutoff
            }
        self._passthrough_log_times[host] = now
        self._logger.info("[PASS] %s %s", method, host)

    def tls_failed_server(self, data: TlsData) -> None:
        """Log TLS handshake failures with server context."""
        server = data.conn
        address = getattr(server, "address", None)
        host = getattr(server, "sni", None) or (address[0] if address else None)
        port = address[1] if address else None
        if host and port:
            target = f"https://{host}:{port}"
        elif host:
            target = host
        else:
            target = "unknown"
        log_tls_handshake_failure(target, getattr(server, "error", None))

    def _parse_sse_body(self, content: bytes | None) -> list[Any]:
        """Parse a complete SSE response body into individual events."""
        if not content:
            return []

        events = []
        try:
            text = content.decode("utf-8")
            raw_events = text.split("\n\n")

            for raw_event in raw_events:
                raw_event = raw_event.strip()
                if not raw_event:
                    continue

                for line in raw_event.split("\n"):
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            events.append({"done": True})
                        else:
                            try:
                                events.append(json.loads(data))
                            except json.JSONDecodeError:
                                events.append({"raw": data})
                    elif line.startswith("event:"):
                        event_type = line[6:].strip()
                        if events and isinstance(events[-1], dict):
                            events[-1]["_event_type"] = event_type

            return events

        except Exception as e:
            self._logger.debug("Failed to parse SSE body: %s", e)
            return [{"error": str(e), "raw": content[:500].hex() if content else ""}]

    def _parse_body(self, content: bytes | None, content_type: str | None) -> Any:
        """Parse request/response body based on content type."""
        if not content:
            return None

        try:
            if content_type and "json" in content_type:
                return json.loads(content.decode("utf-8"))

            try:
                text = content.decode("utf-8")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
            except UnicodeDecodeError:
                return f"<binary content: {len(content)} bytes>"

        except Exception as e:
            self._logger.debug("Failed to parse body: %s", e)
            return f"<parse error: {e}>"

    def _mask_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """Mask sensitive headers."""
        if not self.masking_config.mask_auth_headers:
            return headers

        masked = {}
        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower in self.masking_config.sensitive_headers:
                masked[key] = self._mask_api_key(value)
            else:
                masked[key] = value

        return masked

    # Pre-compiled regex patterns for API key masking
    _MASK_PATTERNS = [
        (re.compile(r"(sk-[a-zA-Z0-9]{4})[a-zA-Z0-9]+"), r"\1***"),
        (re.compile(r"(Bearer\s+)[a-zA-Z0-9_-]+"), r"\1***MASKED***"),
        (re.compile(r"([a-zA-Z0-9]{8})[a-zA-Z0-9]{24,}"), r"\1***"),
    ]

    def _mask_api_key(self, value: str) -> str:
        """Mask an API key value."""
        masked = value
        for pattern, replacement in self._MASK_PATTERNS:
            masked = pattern.sub(replacement, masked)

        if masked == value and len(value) > 16:
            return value[:8] + self.masking_config.mask_pattern

        return masked

    def _mask_body_fields(self, body: dict[str, Any]) -> dict[str, Any]:
        """Mask sensitive fields in the request/response body."""
        if not self.masking_config.sensitive_body_fields:
            return body

        masked = body.copy()
        for field_path in self.masking_config.sensitive_body_fields:
            parts = field_path.split(".")
            self._mask_nested_field(masked, parts)

        return masked

    def _mask_nested_field(self, obj: dict[str, Any], path: list[str]) -> None:
        """Recursively mask a nested field."""
        if not path:
            return

        key = path[0]
        if key not in obj:
            return

        if len(path) == 1:
            obj[key] = self.masking_config.mask_pattern
        elif isinstance(obj[key], dict):
            self._mask_nested_field(obj[key], path[1:])

    def _cleanup_flow(self, flow_id: int) -> None:
        """Clean up tracking data for a completed flow."""
        self._request_times.pop(flow_id, None)
        self._request_ids.pop(flow_id, None)
        self._request_sessions.pop(flow_id, None)
        self._capture_decisions.pop(flow_id, None)
        self._bilibili_ai_decisions.pop(flow_id, None)
        self._bilibili_passthrough_decisions.pop(flow_id, None)
        self._flow_diagnostics.pop(flow_id, None)


def create_watch_proxy_master(
    config: LLIConfig,
    watch_manager: WatchManager,
) -> DumpMaster:
    """Create a configured dump master without starting its event loop."""
    logger = get_logger()
    logger.info("Starting watch proxy on %s:%d", config.proxy.host, config.proxy.port)

    mitmproxy_logger = logging.getLogger("mitmproxy")
    mitmproxy_logger.setLevel(logging.WARNING)
    mitmproxy_logger.propagate = False

    mitmproxy_console_logger = logging.getLogger("mitmproxy.console")
    mitmproxy_console_logger.setLevel(logging.WARNING)
    mitmproxy_console_logger.propagate = False

    url_filter = URLFilter(config.filter)

    # Create watch addon
    addon = WatchAddon(config, watch_manager, url_filter)

    # Upstream CA: validate path when set and warn if ssl_insecure is also on
    if config.proxy.upstream_ca_cert:
        ca_path = Path(config.proxy.upstream_ca_cert)
        if not ca_path.exists():
            raise FileNotFoundError(
                f"Upstream CA cert path does not exist: {config.proxy.upstream_ca_cert}"
            )
        if config.proxy.ssl_insecure:
            logger.warning(
                "Upstream CA cert is set but ssl_insecure=true; "
                "upstream verification will be skipped, reducing benefit of the CA."
            )

    # Configure mitmproxy options
    opts = Options(
        listen_host=config.proxy.host,
        listen_port=config.proxy.port,
        ssl_insecure=config.proxy.ssl_insecure,
    )
    ignore_hosts = _get_ignore_hosts(config)
    if ignore_hosts:
        opts.update(ignore_hosts=ignore_hosts)
        logger.info("Non-LLM tunnel hosts: %s", ", ".join(NON_LLM_TUNNEL_HOSTS))
    if config.proxy.upstream_ca_cert:
        opts.update(
            ssl_verify_upstream_trusted_ca=str(Path(config.proxy.upstream_ca_cert).resolve())
        )

    # Create and run DumpMaster
    # Suppress mitmproxy's default console output by redirecting stdout temporarily
    null_stream = StringIO()

    # Redirect stdout during DumpMaster creation to suppress console output
    with redirect_stdout(null_stream):
        master = DumpMaster(opts)
        master.addons.add(addon)

    # Try to remove eventlog addon if it exists
    try:
        from mitmproxy.addons import eventstore

        for addon_name in list(master.addons.keys()):
            addon_instance = master.addons[addon_name]
            if isinstance(addon_instance, eventstore.EventStore):
                master.addons.remove(addon_name)
    except Exception:
        pass

    return master


async def run_watch_proxy(
    config: LLIConfig,
    watch_manager: WatchManager,
    on_ready: Callable[[DumpMaster], None] | None = None,
    on_running: Callable[[], None] | None = None,
) -> None:
    """Start the mitmproxy server in watch mode."""
    logger = get_logger()
    master = create_watch_proxy_master(config, watch_manager)
    if on_ready:
        on_ready(master)
    if on_running:

        class RuntimeSignalAddon:
            def running(self) -> None:
                on_running()

        master.addons.add(RuntimeSignalAddon())
    logger.info("Watch proxy initialized, monitoring traffic...")

    try:
        await master.run()
    except Exception as e:
        logger.error("Watch proxy error: %s", e)
        raise
