from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lli.config import FilterConfig
from lli.filters import URLFilter
from lli.sites import DEFAULT_MODEL_SITE_IDS, get_model_site_patterns
from lli.windows import RegistryValue, WindowsSystemProxy


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
    assert url_filter.should_capture("https://relay.internal.example/v1/private-route")


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
        self.values = deepcopy(values)

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
