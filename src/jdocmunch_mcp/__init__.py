"""jDocMunch MCP - structured documentation retrieval server.

⚠⚠ **``__version__`` IS RESOLVED ON FIRST ACCESS, NOT ON IMPORT.** It used to be
computed in the module body, so importing ANY part of this package -- including
``cli.main``, which every hook fire imports -- paid for ``importlib.metadata``
plus the distribution lookup itself, for a string most entry points never read.
Deferring it is nearly all of what the package ``__init__`` used to cost.

⚠ ``cli/main.py``'s ``_LazyVersionAction`` was written to preserve "the lazy
``importlib.metadata`` lookup in the package ``__init__``". That laziness did
not exist -- the Action deferred a string interpolation in front of an eager
lookup. It does now, and that docstring is finally true.

PEP 562 module ``__getattr__``: ``from jdocmunch_mcp import __version__``,
``jdocmunch_mcp.__version__`` and ``getattr`` all keep working unchanged, and
the value is cached after the first read.
"""

__all__ = ["__version__"]

_VERSION: "str | None" = None


def __getattr__(name: str) -> str:
    if name == "__version__":
        global _VERSION
        if _VERSION is None:
            from importlib.metadata import PackageNotFoundError, version
            try:
                _VERSION = version("jdocmunch-mcp")
            except PackageNotFoundError:
                _VERSION = "unknown"
        return _VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(list(globals()) + ["__version__"])
