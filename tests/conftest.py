"""Test session setup: deterministic command output.

Typer switches its Rich console to forced terminal mode when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is set,
which wraps usage errors in ANSI escapes and breaks substring assertions on command output. The tests want the
plain text a non-interactive user sees, so those variables are cleared before any test module imports typer.
"""

import os

for _variable in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_variable, None)
os.environ.setdefault("NO_COLOR", "1")
os.environ.setdefault("TERM", "dumb")
