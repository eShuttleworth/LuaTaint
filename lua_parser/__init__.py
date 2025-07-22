"""Lua parser package initialization."""

import sys
import types
import typing

# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------
# Some environments install the ``typing`` backport from PyPI, which shadows
# the standard library version.  The backport does not provide ``typing.io``
# that newer versions of ``antlr4`` expect.  When missing, importing the
# runtime raises ``ModuleNotFoundError: No module named 'typing.io'``.  To keep
# the parser functional we ensure ``typing.io`` is defined before importing
# any antlr modules.
if not hasattr(typing, "io"):
    _typing_io = types.SimpleNamespace(TextIO=typing.TextIO)
    typing.io = _typing_io
    sys.modules["typing.io"] = _typing_io

__version__ = "3.1.1"
