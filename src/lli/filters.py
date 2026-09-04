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

# Keep address-only relay recognition narrow. A version prefix is common on
# ordinary REST, feed, and media APIs, so it is not sufficient evidence of LLM
# traffic by itself. Non-standard LLM routes can still be recognized from their
# request body (model + a known prompt/input field) below.
LLM_VERSIONED_API_PATH_RE = re.compile(
    r"/(?:openai/)?v\d+(?:beta)?/"
    r"(?:chat/completions?|responses?|completions?|embeddings?|messages?|complete|"
    r"generateContent|streamGenerateContent|"
    r"images/generations?|audio/(?:transcriptions?|speech)|moderations?|"
    r"models/[^/?#]+:(?:generateContent|streamGenerateContent|embedContent|countTokens))"
    r"(?:[/?:]|$)",
    re.IGNORECASE,
)
# Some AI services use unversioned routes and send parameters in the query
# string (or no request body at all). Match complete path segments only so
# similarly named media endpoints such as ``generate-thumbnail`` stay out.
LLM_PATH_HINT_RE = re.compile(
    r"(?:^|/)(?:chat|completion|completions|responses?|embeddings?|generate(?:content)?|"
    r"inference|infer|assistant|copilot|transcript(?:ion)?|(?:audio/)?speech)(?:[/?:]|$)|"
    r"(?:^|/|[-_])summar(?:y|ize)(?:[/?:_-]|$)",
    re.IGNORECASE,
)
# Backward-compatible name for callers that imported the old module constant.
VERSIONED_API_PATH_RE = LLM_VERSIONED_API_PATH_RE
LLM_PAYLOAD_KEYS = frozenset(
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
        # Keep the config reference so the desktop runtime can toggle relay
        # recognition without rebuilding the active mitmproxy addon.
        self._config = config
        self._logger = get_logger()

    @property
    def auto_detect_api_paths(self) -> bool:
        """Return the current relay-recognition setting."""
        return self._config.auto_detect_api_paths

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

        # Most OpenAI-compatible relays expose one of the standard LLM routes,
        # such as /v1/chat/completions or /v1/responses.
        path = url.partition("?")[0].partition("#")[0]
        if LLM_VERSIONED_API_PATH_RE.search(url):
            return True

        # Unversioned path hints are useful for body-less GET-style AI APIs.
        # When a JSON body is available, require the stronger model/payload
        # evidence below so an ordinary POST route named ``/inference`` is not
        # captured merely because of its path.
        if body is None and LLM_PATH_HINT_RE.search(path):
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
        r".*api\.anthropic\.com/v1/(?:messages?|complete)(?:[/?:].*)?$",
    ],
    "openai": [
        r".*api\.openai\.com/v1/(?:chat/completions?|responses?|completions?|embeddings?|"
        r"images/generations?|audio/(?:transcriptions?|speech)|moderations?)(?:[/?:].*)?$",
    ],
    "google": [
        r".*generativelanguage\.googleapis\.com/v1(?:beta)?/(?:generateContent|"
        r"streamGenerateContent|models/[^/?#:]+:(?:generateContent|streamGenerateContent|"
        r"embedContent|countTokens))(?:[/?:].*)?$",
    ],
    "together": [
        r".*api\.together\.xyz/v1/(?:chat/completions?|completions?|embeddings?)(?:[/?:].*)?$",
    ],
    "groq": [
        r".*api\.groq\.com/openai/v1/(?:chat/completions?|completions?|embeddings?)(?:[/?:].*)?$",
    ],
    "mistral": [
        r".*api\.mistral\.ai/v1/(?:chat/completions?|fim/completions?|embeddings?)(?:[/?:].*)?$",
    ],
    "cohere": [
        r".*api\.cohere\.ai/v1/(?:chat|generate|embed|rerank)(?:[/?:].*)?$",
    ],
    "deepseek": [
        r".*api\.deepseek\.com/v1/(?:chat/completions?|completions?)(?:[/?:].*)?$",
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
