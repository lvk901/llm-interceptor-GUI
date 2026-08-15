from __future__ import annotations

import sys

from lli.windows import _hidden_subprocess_options


def test_hidden_subprocess_options_are_platform_appropriate() -> None:
    options = _hidden_subprocess_options()
    if sys.platform == "win32":
        assert options["creationflags"] > 0
        assert options["startupinfo"] is not None
    else:
        assert options == {}
