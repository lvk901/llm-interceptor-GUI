"""
URL filtering for LLM Interceptor.

Provides pattern-based filtering to capture only relevant LLM API traffic.
"""

import fnmatch
import re
from collections.abc import Mapping
from re import Pattern

from lli.config import FilterConfig
from lli.logger import get_logger
from lli.sites import get_model_site_patterns

VERSIONED_API_PATH_RE = re.compile(r"/(?:openai/)?v\d+(?:beta)?(?:/|$)", re.IGNORECASE)
LLM_PAYLOAD_KEYS = frozenset({"messages", "input", "prompt", "tools", "max_tokens", "stream"})


class URLFilter:
    """
    URL filter based on include/exclude patterns.

    Supports two types of patterns:
    - Regex patterns (default, used internally for built-in LLM providers)
    - Glob patterns (user-friendly, for --include CLI option)

    Include patterns are checked first, then exclude patterns.
    If no include patterns match, the URL is rejected.
    If any exclude pattern matches, the URL is rejected.
    """

    def __init__(self, config: FilterConfig):
        """
        Initialize the URL filter with the given configuration.

        Args:
            config: FilterConfig with include and exclude patterns
        """
        # Regex patterns (default built-in patterns)
        resolved_patterns = [
            *config.include_patterns,
            *get_model_site_patterns(config.site_profiles),
        ]
        self.include_patterns: list[Pattern[str]] = [
            re.compile(pattern, re.IGNORECASE) for pattern in resolved_patterns
        ]
        self.exclude_patterns: list[Pattern[str]] = [
            re.compile(p, re.IGNORECASE) for p in config.exclude_patterns
        ]
        # Glob patterns (user-provided via --include)
        self.include_globs: list[str] = list(config.include_globs)
        self.exclude_globs: list[str] = list(config.exclude_globs)
        self.auto_detect_api_paths = config.auto_detect_api_paths
        self._logger = get_logger()

    def should_capture(self, url: str, body: object | None = None) -> bool:
        """
        Determine if a URL should be captured.

        Args:
            url: The full URL to check
            body: Parsed JSON request body when available. Used for relay detection.

        Returns:
            True if the URL should be captured, False otherwise
        """
        url_lower = url.lower()

        # Check exclude patterns first (highest priority)
        # Check regex exclude patterns
        for pattern in self.exclude_patterns:
            if pattern.search(url):
                self._logger.debug("URL excluded by regex pattern: %s", url)
                return False

        # Check glob exclude patterns
        for glob_pattern in self.exclude_globs:
            if fnmatch.fnmatch(url_lower, glob_pattern.lower()):
                self._logger.debug("URL excluded by glob pattern: %s", url)
                return False

        # Check include patterns
        # Check regex include patterns
        for pattern in self.include_patterns:
            if pattern.search(url):
                self._logger.debug("URL matched regex include pattern: %s", url)
                return True

        # Check glob include patterns
        for glob_pattern in self.include_globs:
            if fnmatch.fnmatch(url_lower, glob_pattern.lower()):
                self._logger.debug("URL matched glob include pattern: %s", url)
                return True

        if self._looks_like_relay_api(url, body):
            self._logger.debug("URL matched relay API recognition: %s", url)
            return True

        # No match - don't capture
        return False

    def _looks_like_relay_api(self, url: str, body: object | None) -> bool:
        """Recognize OpenAI-compatible relays without depending on their hostname."""
        if not self.auto_detect_api_paths:
            return False

        # Most OpenAI-compatible relays expose paths such as /v1/chat/completions.
        if VERSIONED_API_PATH_RE.search(url):
            return True

        if not isinstance(body, Mapping):
            return False

        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            return False

        return any(key in body for key in LLM_PAYLOAD_KEYS)

    def add_include_pattern(self, pattern: str) -> None:
        """Add an include regex pattern at runtime."""
        self.include_patterns.append(re.compile(pattern, re.IGNORECASE))

    def add_exclude_pattern(self, pattern: str) -> None:
        """Add an exclude regex pattern at runtime."""
        self.exclude_patterns.append(re.compile(pattern, re.IGNORECASE))

    def add_include_glob(self, pattern: str) -> None:
        """
        Add an include glob pattern at runtime.

        Glob patterns support:
        - * : matches any characters
        - ? : matches a single character
        - [seq] : matches any character in seq
        - [!seq] : matches any character not in seq

        Example: "*api.example.com*" matches any URL containing "api.example.com"
        """
        self.include_globs.append(pattern)

    def add_exclude_glob(self, pattern: str) -> None:
        """
        Add an exclude glob pattern at runtime.

        Glob patterns support:
        - * : matches any characters
        - ? : matches a single character
        - [seq] : matches any character in seq
        - [!seq] : matches any character not in seq
        """
        self.exclude_globs.append(pattern)


# Common LLM API endpoints for reference
KNOWN_LLM_ENDPOINTS = {
    "anthropic": [
        r".*api\.anthropic\.com/v1/messages.*",
        r".*api\.anthropic\.com/v1/complete.*",
    ],
    "openai": [
        r".*api\.openai\.com/v1/chat/completions.*",
        r".*api\.openai\.com/v1/completions.*",
        r".*api\.openai\.com/v1/embeddings.*",
    ],
    "google": [
        r".*generativelanguage\.googleapis\.com/v1.*",
        r".*generativelanguage\.googleapis\.com/v1beta.*",
    ],
    "together": [
        r".*api\.together\.xyz/v1/.*",
    ],
    "groq": [
        r".*api\.groq\.com/openai/v1/.*",
    ],
    "mistral": [
        r".*api\.mistral\.ai/v1/.*",
    ],
    "cohere": [
        r".*api\.cohere\.ai/v1/.*",
    ],
    "deepseek": [
        r".*api\.deepseek\.com/v1/.*",
    ],
}


def get_provider_patterns(providers: list[str]) -> list[str]:
    """
    Get URL patterns for specific providers.

    Args:
        providers: List of provider names (e.g., ["anthropic", "openai"])

    Returns:
        List of regex patterns for the specified providers
    """
    patterns: list[str] = []
    for provider in providers:
        provider_lower = provider.lower()
        if provider_lower in KNOWN_LLM_ENDPOINTS:
            patterns.extend(KNOWN_LLM_ENDPOINTS[provider_lower])
    return patterns
