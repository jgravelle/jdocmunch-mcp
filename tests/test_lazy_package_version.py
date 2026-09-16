"""``jdocmunch_mcp.__version__`` resolves on first access, not on import.

The package ``__init__`` used to compute it in the module body, so importing ANY
part of the package -- including ``cli.main``, which every hook fire imports --
paid for ``importlib.metadata`` walking installed distributions to answer one
string, for a value most entry points never read.

⚠ PEP 562's module ``__getattr__`` is the right tool HERE and the wrong tool for
a module's own internals: it fires for attribute access on the module OBJECT,
not for bare global lookups inside the module's own functions. Anything added to
``__init__.py`` that needs the version must resolve it directly rather than
expecting the hook to fire.

⚠⚠ ``tests/test_cli_import_cost.py`` ratchets that importing the package does NOT
pull in ``importlib.metadata``. That guard alone is satisfiable by a build that
has simply stopped reporting a version at all, so the complement below --
reading the version DOES import it -- is what makes the pair meaningful.

Coverage ported from a sibling session's independent implementation of the same
change (`perf/lazy-package-init`). Its implementation was redundant by the time
it was reviewed; these behaviours were not covered here and are.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(code: str) -> subprocess.CompletedProcess:
    """Run in a FRESH interpreter: every claim here is about import-time state,
    which this process has already passed."""
    import os

    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
        env={**os.environ, "PYTHONPATH": SRC},
    )


# --------------------------------------------------------------------------
# Every spelling keeps working
# --------------------------------------------------------------------------


def test_attribute_access_returns_a_string():
    import jdocmunch_mcp

    assert isinstance(jdocmunch_mcp.__version__, str)
    assert jdocmunch_mcp.__version__


def test_from_import_spelling_still_works():
    from jdocmunch_mcp import __version__

    assert isinstance(__version__, str)


def test_all_spellings_agree():
    import jdocmunch_mcp
    from jdocmunch_mcp import __version__ as imported

    assert imported == jdocmunch_mcp.__version__ == getattr(jdocmunch_mcp, "__version__")


def test_hasattr_is_true():
    import jdocmunch_mcp

    assert hasattr(jdocmunch_mcp, "__version__")


def test_dir_lists_version_before_first_access():
    """``__dir__`` must advertise it even though it is not yet in ``globals()``,
    or tab-completion and introspection stop seeing a public attribute."""
    r = _run("""
        import jdocmunch_mcp
        print("LISTED=" + ("YES" if "__version__" in dir(jdocmunch_mcp) else "NO"))
    """)
    assert r.returncode == 0, r.stderr
    assert "LISTED=YES" in r.stdout


def test_unknown_attribute_still_raises_attribute_error():
    """A module ``__getattr__`` that swallows everything turns typos into silent
    ``None``s across the whole package."""
    import jdocmunch_mcp

    with pytest.raises(AttributeError):
        jdocmunch_mcp.definitely_not_a_real_attribute


# --------------------------------------------------------------------------
# Laziness, and its complement
# --------------------------------------------------------------------------


def test_importing_the_package_does_not_import_importlib_metadata():
    r = _run("""
        import sys
        import jdocmunch_mcp  # noqa: F401
        print("META=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
    """)
    assert r.returncode == 0, r.stderr
    assert "META=NO" in r.stdout, (
        "importing the package pulled in importlib.metadata again -- "
        "__version__ is being resolved at import time"
    )


def test_reading_the_version_does_import_it():
    """⚠ The complement, and the reason the pair is not vacuous: a build that
    silently stopped reporting a version would satisfy the test above."""
    r = _run("""
        import sys
        import jdocmunch_mcp
        v = jdocmunch_mcp.__version__
        print("META=" + ("YES" if "importlib.metadata" in sys.modules else "NO"))
        print("VALUE=" + v)
    """)
    assert r.returncode == 0, r.stderr
    assert "META=YES" in r.stdout
    value = [ln for ln in r.stdout.splitlines() if ln.startswith("VALUE=")][0]
    assert value != "VALUE=", "the version resolved to an empty string"


def test_repeated_access_does_not_re_resolve():
    """Behaviour, not mechanism: the value is resolved once. Asserting WHERE it
    is cached would pin an implementation detail two equivalent designs differ
    on (a module-dict self-replace versus a private global)."""
    r = _run("""
        import jdocmunch_mcp
        from importlib import metadata

        calls = {"n": 0}
        real = metadata.version

        def counting(name):
            calls["n"] += 1
            return real(name)

        metadata.version = counting
        first = jdocmunch_mcp.__version__
        after_first = calls["n"]
        for _ in range(20):
            assert jdocmunch_mcp.__version__ == first
        print(f"EXTRA={calls['n'] - after_first}")
    """)
    assert r.returncode == 0, r.stderr
    assert "EXTRA=0" in r.stdout, (
        f"repeated reads re-resolved the version: {r.stdout.strip()}"
    )


def test_missing_distribution_falls_back_to_unknown():
    """The fallback is load-bearing: a source checkout with no installed
    distribution must still import, and tests/test_mcp_instructions.py depends
    on that behaviour."""
    r = _run("""
        import sys
        from importlib import metadata

        def boom(name):
            raise metadata.PackageNotFoundError(name)

        metadata.version = boom
        import jdocmunch_mcp
        print("VALUE=" + jdocmunch_mcp.__version__)
    """)
    assert r.returncode == 0, r.stderr
    assert "VALUE=unknown" in r.stdout
