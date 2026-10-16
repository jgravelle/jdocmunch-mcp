"""jdoc#178: ``__version__`` must not raise on a damaged dist-info.

``__getattr__`` caught only ``PackageNotFoundError``. A ``METADATA`` that is not
valid UTF-8 makes ``importlib.metadata.version`` raise ``UnicodeDecodeError``,
and that came out of ``__version__`` itself, which ``server.py`` reads at
import. Reported by @outoftheblue9 while working on #175.
"""

import importlib.metadata

import pytest

import jdocmunch_mcp
from jdocmunch_mcp import runtime_staleness as rs


@pytest.fixture
def damaged_site(tmp_path, monkeypatch):
    dist = tmp_path / "jdocmunch_mcp-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_bytes(b"Name: jdocmunch-mcp\nVersion: 1.0\xff\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(jdocmunch_mcp, "_VERSION", None)
    # A control: the input really raises in the resolver on this Python.
    with pytest.raises(UnicodeDecodeError):
        importlib.metadata.version("jdocmunch-mcp")
    return tmp_path


def test_version_reads_unknown_instead_of_raising(damaged_site):
    assert jdocmunch_mcp.__version__ == "unknown"


def test_the_fallback_is_cached(damaged_site, monkeypatch):
    assert jdocmunch_mcp.__version__ == "unknown"
    def boom(_name):
        raise AssertionError("resolved twice")
    monkeypatch.setattr(importlib.metadata, "version", boom)
    assert jdocmunch_mcp.__version__ == "unknown"


def test_staleness_reads_the_same_failure_as_no_version(damaged_site):
    # Both readers treat the damaged dist-info as "no version", so the
    # staleness probe can't report drift between them.
    assert jdocmunch_mcp.__version__ == "unknown"
    assert rs._installed_version() is None


def test_an_unrelated_attribute_still_raises():
    with pytest.raises(AttributeError):
        jdocmunch_mcp.no_such_name
