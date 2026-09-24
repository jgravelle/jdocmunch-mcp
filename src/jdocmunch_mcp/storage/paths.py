"""Where jdocmunch keeps its files (jdoc#146).

Every module that stores something under the index root resolves that root
here. Until #146 eleven of them fell back to ``~/.doc-index`` whenever they
were handed ``base_path=None``, while ``DocStore`` honoured ``DOC_INDEX_PATH``
(#37). The MCP server passes the variable explicitly, so it never noticed; the
CLI passes nothing, so with ``DOC_INDEX_PATH`` set the index went to one root
and its sidecars to another, overwriting the live sidecars of any index with
the same name under the home directory.

⚠ The variable is read at CALL time, never at import, so a caller that sets it
after import still gets it.
"""

from __future__ import annotations

import os
from pathlib import Path


def home_root() -> Path:
    """The root used when ``DOC_INDEX_PATH`` is unset."""
    return Path.home() / ".doc-index"


def default_root() -> Path:
    """``DOC_INDEX_PATH`` if set, else :func:`home_root`."""
    env_path = os.environ.get("DOC_INDEX_PATH")
    return Path(env_path) if env_path else home_root()


def resolve_root(base_path) -> Path:
    """An explicit ``base_path`` wins; otherwise :func:`default_root`."""
    return Path(base_path) if base_path else default_root()
