"""Built-in capture profiles for browser-based AI model websites."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSiteProfile:
    """A named set of API-only URL patterns for one model website."""

    id: str
    name: str
    patterns: tuple[str, ...]


MODEL_SITE_PROFILES: tuple[ModelSiteProfile, ...] = (
    ModelSiteProfile(
        id="chatgpt",
        name="ChatGPT",
        patterns=(r".*(?:chatgpt\.com|chat\.openai\.com)/backend-api/.*",),
    ),
    ModelSiteProfile(
        id="claude",
        name="Claude",
        patterns=(r".*claude\.ai/api/.*",),
    ),
    ModelSiteProfile(
        id="gemini",
        name="Gemini",
        patterns=(r".*gemini\.google\.com/_/BardChatUi/.*",),
    ),
    ModelSiteProfile(
        id="deepseek-chat",
        name="DeepSeek",
        patterns=(r".*chat\.deepseek\.com/api/.*",),
    ),
    ModelSiteProfile(
        id="kimi",
        name="Kimi",
        patterns=(r".*(?:kimi\.moonshot\.cn|kimi\.com)/api/.*",),
    ),
    ModelSiteProfile(
        id="qwen",
        name="Tongyi Qwen",
        patterns=(r".*(?:chat\.qwen\.ai|qianwen\.com)/api/.*",),
    ),
    ModelSiteProfile(
        id="doubao",
        name="Doubao",
        patterns=(r".*www\.doubao\.com/(?:chat/)?api/.*",),
    ),
    ModelSiteProfile(
        id="zhipu",
        name="Zhipu Qingyan",
        patterns=(r".*(?:chatglm\.cn|chat\.z\.ai)/api/.*",),
    ),
)

DEFAULT_MODEL_SITE_IDS = tuple(profile.id for profile in MODEL_SITE_PROFILES)


def get_model_site_profiles() -> list[ModelSiteProfile]:
    """Return the supported website profiles in stable display order."""
    return list(MODEL_SITE_PROFILES)


def get_model_site_patterns(profile_ids: list[str] | tuple[str, ...]) -> list[str]:
    """Return API capture patterns for the selected website profiles."""
    selected = set(profile_ids)
    return [
        pattern
        for profile in MODEL_SITE_PROFILES
        if profile.id in selected
        for pattern in profile.patterns
    ]
