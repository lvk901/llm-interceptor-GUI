from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from lli.config import FilterConfig
from lli.filters import URLFilter
from lli.sites import DEFAULT_MODEL_SITE_IDS, get_model_site_patterns
from lli.windows import LLI_PROXY_OVERRIDE, RegistryValue, WindowsSystemProxy


def test_default_model_site_profiles_capture_only_chat_api_paths() -> None:
    url_filter = URLFilter(FilterConfig())
    assert set(DEFAULT_MODEL_SITE_IDS) == {
        "chatgpt",
        "claude",
        "gemini",
        "deepseek-chat",
        "kimi",
        "qwen",
        "doubao",
        "zhipu",
    }
    assert url_filter.should_capture("https://chatgpt.com/backend-api/conversation")
    assert url_filter.should_capture("https://claude.ai/api/organizations/test/chat_conversations")
    assert url_filter.should_capture("https://chat.deepseek.com/api/v0/chat/completion")
    assert not url_filter.should_capture("https://chatgpt.com/assets/application.js")
    assert not url_filter.should_capture("https://claude.ai/favicon.ico")


def test_default_provider_profiles_do_not_capture_same_host_media() -> None:
    """Known provider hosts can serve media and health assets beside LLM APIs."""
    url_filter = URLFilter(FilterConfig())

    assert not url_filter.should_capture("https://api.openai.com/assets/video.mp4")
    assert not url_filter.should_capture("https://api.anthropic.com/health")
    assert not url_filter.should_capture("https://api.deepseek.com/v1/video/segment.m4s")


def test_disabled_model_site_profile_does_not_match() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=["chatgpt"]))
    assert url_filter.should_capture("https://chatgpt.com/backend-api/conversation")
    assert not url_filter.should_capture(
        "https://claude.ai/api/organizations/test/chat_conversations"
    )
    assert get_model_site_patterns(["chatgpt"])


def test_relay_address_recognition_captures_unknown_v1_domain() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=[]))
    assert url_filter.should_capture("https://relay.internal.example/v1/chat/completions")
    assert url_filter.should_capture("https://relay.internal.example/openai/v1/responses")
    assert url_filter.should_capture("https://relay.internal.example/v1/audio/transcriptions")
    assert url_filter.should_capture(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert not url_filter.should_capture("https://relay.internal.example/v1/private-route")


def test_relay_address_recognition_ignores_ordinary_versioned_api_paths() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=[]))

    assert not url_filter.should_capture("https://news.example/v1/feed/index")
    assert not url_filter.should_capture("https://cdn.example/v1/video/playlist.m3u8")
    assert not url_filter.should_capture("https://api.example/v1/downloads/latest")
    assert not url_filter.should_capture(
        "https://api.example/v1/models/catalog:metadata"
    )
    assert not url_filter.should_capture("https://news.example/api/generate-thumbnail")


def test_unversioned_ai_path_is_captured_without_a_request_body() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=[]))

    assert url_filter.should_capture("https://summary.example/api/video-summary")
    assert url_filter.should_capture("https://relay.example/api/inference")


def test_nonstandard_relay_path_still_requires_llm_shaped_body() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=[]))
    relay_url = "https://relay.internal.example/v1/private-route"

    assert url_filter.should_capture(
        relay_url,
        {"model": "gpt-test", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert not url_filter.should_capture(relay_url, {"page": 1, "items": []})


def test_relay_address_recognition_updates_without_rebuilding_filter() -> None:
    config = FilterConfig(include_patterns=[], site_profiles=[], auto_detect_api_paths=True)
    url_filter = URLFilter(config)
    relay_url = "https://relay.internal.example/v1/chat/completions"

    assert url_filter.should_capture(relay_url)
    config.auto_detect_api_paths = False
    assert not url_filter.should_capture(relay_url)


def test_relay_payload_recognition_captures_nonstandard_path() -> None:
    url_filter = URLFilter(FilterConfig(include_patterns=[], site_profiles=[]))
    body = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "hello"}]}
    assert url_filter.should_capture("https://gateway.example/api/inference", body)
    assert not url_filter.should_capture("https://gateway.example/api/inference", {"model": ""})


def test_excluded_relay_url_wins_over_address_recognition() -> None:
    url_filter = URLFilter(
        FilterConfig(
            include_patterns=[],
            site_profiles=[],
            exclude_patterns=[r".*relay\.internal\.example/v1/health.*"],
        )
    )
    assert not url_filter.should_capture("https://relay.internal.example/v1/health")


class MemorySystemProxy(WindowsSystemProxy):
    """Registry-free proxy implementation for reversible proxy state tests."""

    def __init__(self, state_path: Path) -> None:
        super().__init__(state_path)
        self.values = {
            "ProxyEnable": RegistryValue(True, 0, 4),
            "ProxyServer": RegistryValue(False),
            "ProxyOverride": RegistryValue(True, "intranet", 1),
            "AutoConfigURL": RegistryValue(True, "https://pac.example/proxy.pac", 1),
        }

    @property
    def supported(self) -> bool:
        return True

    def _read_values(self) -> dict[str, RegistryValue]:
        return deepcopy(self.values)

    def _write_values(self, values: dict[str, RegistryValue]) -> None:
        self.values.update(deepcopy(values))

    def _notify_settings_changed(self) -> None:
        pass


def test_system_proxy_restores_prior_values_when_unchanged(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    original = deepcopy(proxy.values)

    proxy.activate("127.0.0.1", 9090)
    assert proxy.status().managed_by_lli is True
    proxy.deactivate()

    assert proxy.values == original
    assert not proxy.state_path.exists()


def test_system_proxy_preserves_user_changes_made_while_active(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    proxy.activate("127.0.0.1", 9090)
    proxy.values["ProxyServer"] = RegistryValue(True, "http=custom:8080", 1)

    proxy.deactivate()

    assert proxy.values["ProxyServer"].value == "http=custom:8080"
    assert not proxy.state_path.exists()


def test_system_proxy_preserves_user_bypass_changes_made_while_active(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    proxy.activate("127.0.0.1", 9090)
    proxy.values["ProxyOverride"] = RegistryValue(True, "custom-bypass", 1)

    proxy.deactivate()

    assert proxy.values["ProxyOverride"].value == "custom-bypass"
    assert not proxy.state_path.exists()


def test_system_proxy_disables_lli_proxy_if_snapshot_is_missing(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    proxy.activate("127.0.0.1", 9090)
    proxy._clear_snapshot()

    proxy.deactivate()

    assert proxy.values["ProxyEnable"].value == 0


def test_system_proxy_snapshot_is_atomically_replaced(tmp_path: Path, monkeypatch) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    original_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.exists()
        replacements.append((source_path, destination_path))
        original_replace(source_path, destination_path)

    monkeypatch.setattr("lli.windows.os.replace", recording_replace)
    proxy._save_snapshot("http=127.0.0.1:9090;https=127.0.0.1:9090", proxy.values)

    assert replacements and replacements[0][1] == proxy.state_path
    assert proxy._load_snapshot() is not None
    assert not list(tmp_path.glob("*.tmp"))


def test_stale_recovery_disables_only_the_lli_proxy_fingerprint(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    proxy.values.update(
        {
            "ProxyEnable": RegistryValue(True, 1, 4),
            "ProxyServer": RegistryValue(
                True, "http=127.0.0.1:9090;https=127.0.0.1:9090", 1
            ),
            "ProxyOverride": RegistryValue(True, LLI_PROXY_OVERRIDE, 1),
            "AutoConfigURL": RegistryValue(False),
        }
    )

    proxy.recover_stale_proxy()

    assert proxy.values["ProxyEnable"].value == 0


def test_stale_recovery_uses_safe_fallback_for_a_damaged_snapshot(tmp_path: Path) -> None:
    state_path = tmp_path / "proxy.json"
    state_path.write_text("{not-json", encoding="utf-8")
    proxy = MemorySystemProxy(state_path)
    proxy.values.update(
        {
            "ProxyEnable": RegistryValue(True, 1, 4),
            "ProxyServer": RegistryValue(
                True, "http=127.0.0.1:9090;https=127.0.0.1:9090", 1
            ),
            "ProxyOverride": RegistryValue(True, LLI_PROXY_OVERRIDE, 1),
            "AutoConfigURL": RegistryValue(False),
        }
    )

    proxy.recover_stale_proxy()

    assert proxy.values["ProxyEnable"].value == 0


def test_stale_recovery_preserves_changed_proxy_configuration(tmp_path: Path) -> None:
    proxy = MemorySystemProxy(tmp_path / "proxy.json")
    proxy.values.update(
        {
            "ProxyEnable": RegistryValue(True, 1, 4),
            "ProxyServer": RegistryValue(
                True, "http=127.0.0.1:9090;https=127.0.0.1:9090", 1
            ),
            "ProxyOverride": RegistryValue(True, "custom-bypass", 1),
            "AutoConfigURL": RegistryValue(False),
        }
    )

    proxy.recover_stale_proxy()

    assert proxy.values["ProxyEnable"].value == 1
