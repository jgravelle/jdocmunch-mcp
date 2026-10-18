"""The long-lived server discloses when the disk has moved past it (v1.147.0).

An MCP stdio server cannot restart itself and cannot ask to be restarted, so the
achievable design is detect-and-disclose. These tests pin the detection and, as
importantly, pin that a FRESH process discloses nothing -- a signal that fires
when nothing is wrong is one nobody reads.
"""

import importlib
import json
import logging
import os
import time
from datetime import datetime, timezone

import pytest

from jdocmunch_mcp import runtime_staleness as rs


@pytest.fixture(autouse=True)
def _clear_cache():
    """The report is cached for a TTL; tests must not inherit each other's."""
    rs._cache["checked_at"] = 0.0
    rs._cache["report"] = None
    rs._failure_logged = False
    rs._version_failure_logged = False
    yield
    rs._cache["checked_at"] = 0.0
    rs._cache["report"] = None
    rs._failure_logged = False
    rs._version_failure_logged = False


def test_a_freshly_loaded_process_discloses_nothing():
    assert rs.staleness_report() is None


def test_file_newer_than_load_time_is_reported(tmp_path, monkeypatch):
    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("x", encoding="utf-8")
    (pkg / "server.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)

    future = time.time() + 120
    os.utime(pkg / "server.py", (future, future))

    report = rs.staleness_report()
    assert report is not None
    assert report["stale"] is True
    assert "files_changed" in report["reasons"]
    assert "server.py" in report["changed_paths"]
    # It must say what to DO, not merely that something is wrong.
    assert "Restart" in report["action"]


def _write_dist_info(site, version, name="jdocmunch_mcp"):
    """A minimal ``.dist-info`` that ``importlib.metadata`` will list."""
    dist = site / f"{name}-{version}.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: jdocmunch-mcp\nVersion: {version}\n\nbody\n",
        encoding="utf-8",
    )
    return dist


def _real_running_version(monkeypatch):
    """What the package's OWN resolver returns in the current environment.

    Resets the cached value so the real ``__getattr__`` runs, not a copy of it.
    ``monkeypatch`` restores the cache afterwards.
    """
    import jdocmunch_mcp

    monkeypatch.setattr(jdocmunch_mcp, "_VERSION", None)
    return jdocmunch_mcp.__version__


def _isolate_package_dir(site, monkeypatch):
    """Point the mtime signal at an empty package directory inside ``site``.

    The version tests assert ``staleness_report() is None``; without this they
    would also depend on the real ``src/jdocmunch_mcp/{__init__,server}.py``
    being older than the test process.
    """
    pkg = site / "jdocmunch_mcp"
    pkg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)
    return pkg


def test_version_drift_is_reported(tmp_path, monkeypatch):
    _write_dist_info(tmp_path, "9.9.9")
    _isolate_package_dir(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(rs, "_running_version", lambda: "1.0.0")

    report = rs.staleness_report()
    assert report is not None
    assert "version_changed" in report["reasons"]
    assert report["running_version"] == "1.0.0"
    assert report["installed_version"] == "9.9.9"


def test_matching_version_is_not_drift(tmp_path, monkeypatch):
    _write_dist_info(tmp_path, "1.2.3")
    _isolate_package_dir(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(rs, "_running_version", lambda: "1.2.3")
    assert rs.staleness_report() is None


def test_missing_dist_info_is_not_drift(tmp_path, monkeypatch):
    """A source checkout has no installed distribution. Absence of evidence is
    not drift -- the version comparison simply does not apply, and file mtimes
    still do. The resolver is made to find nothing rather than relying on the
    developer's environment lacking the package."""
    import importlib.metadata

    def not_installed(name):
        raise importlib.metadata.PackageNotFoundError(name)

    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)
    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    assert rs._installed_version() is None
    assert rs.staleness_report() is None


def test_unknown_running_version_does_not_claim_drift(tmp_path, monkeypatch):
    """`_running_version` falls back to "unknown". Comparing that against a real
    installed version would report drift on every call forever."""
    _write_dist_info(tmp_path, "5.0.0")
    _isolate_package_dir(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(rs, "_running_version", lambda: "unknown")
    assert rs._installed_version() == "5.0.0"
    assert rs.staleness_report() is None


def test_two_dist_infos_in_one_directory_are_not_drift(tmp_path, monkeypatch):
    """An interrupted upgrade or a failed uninstall can leave two ``.dist-info``
    directories in one site directory. The running version is whatever the
    package's own resolver picked, and the probe asks the same resolver, so they
    agree and a fresh server discloses nothing.

    This pins agreement with the resolver. It cannot separate the old glob from
    the new code: within one directory both list in directory order, so they
    pick the same entry. The leftover-beside-the-package test below is the one
    that does."""
    _write_dist_info(tmp_path, "1.145.5")
    _write_dist_info(tmp_path, "1.146.0")
    _isolate_package_dir(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))
    running = _real_running_version(monkeypatch)
    assert running in {"1.145.5", "1.146.0"}
    monkeypatch.setattr(rs, "_running_version", lambda: running)

    assert rs._installed_version() == running
    assert rs.staleness_report() is None


def test_a_leftover_dist_info_beside_the_package_is_not_drift(tmp_path, monkeypatch):
    """The dist-info the resolver finds need not be the one next to the package
    directory. Site A, first on the path, holds the 1.146.0 the resolver reads;
    site B holds the package and a leftover 1.145.5 from an interrupted upgrade.
    Reading the dist-info beside the package picked the leftover and reported
    ``version_changed`` on every call for the life of the process."""
    site_a = tmp_path / "site_a"
    site_b = tmp_path / "site_b"
    site_a.mkdir()
    _write_dist_info(site_a, "1.146.0")
    pkg = site_b / "jdocmunch_mcp"
    pkg.mkdir(parents=True)
    _write_dist_info(site_b, "1.145.5")
    monkeypatch.syspath_prepend(str(site_b))
    monkeypatch.syspath_prepend(str(site_a))  # prepended last, so first
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)

    running = _real_running_version(monkeypatch)
    assert running == "1.146.0"
    monkeypatch.setattr(rs, "_running_version", lambda: running)

    assert rs.staleness_report() is None


def test_a_real_upgrade_is_still_seen(tmp_path, monkeypatch):
    """Reading through the resolver must not make the probe blind to a reinstall:
    a replaced dist-info is seen on the next probe once the TTL lapses.

    ``importlib.metadata`` keys its directory cache on the directory's mtime, so
    this passes without ``invalidate_caches()`` too. That call is defensive,
    for a filesystem whose directory mtime is too coarse to show a quick
    replace; this test does not pin it."""
    import shutil

    old = _write_dist_info(tmp_path, "1.146.0")
    _isolate_package_dir(tmp_path, monkeypatch)
    monkeypatch.syspath_prepend(str(tmp_path))
    running = _real_running_version(monkeypatch)
    assert running == "1.146.0"
    monkeypatch.setattr(rs, "_running_version", lambda: running)
    assert rs.staleness_report() is None

    shutil.rmtree(old)
    _write_dist_info(tmp_path, "1.147.0")
    # A real reinstall lands seconds after the server started; this one lands
    # in the same clock tick. Windows on Python 3.10 then read the old
    # directory mtime and served the cached listing, which failed this test
    # once on CI (PR #181's run). Move the directory's mtime on the way a real
    # reinstall would, so the test checks what it says and not the clock.
    st = tmp_path.stat()
    os.utime(tmp_path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    rs._cache["checked_at"] = 0.0  # let the TTL lapse

    report = rs.staleness_report()
    assert report is not None
    assert "version_changed" in report["reasons"]
    assert report["running_version"] == "1.146.0"
    assert report["installed_version"] == "1.147.0"


def test_an_unreadable_installed_version_keeps_the_mtime_signal(
    tmp_path, monkeypatch
):
    """The two signals are independent. A METADATA that is not valid UTF-8 makes
    ``importlib.metadata.version`` raise ``UnicodeDecodeError``; that must read
    as "no installed version", not abort the probe and take ``files_changed``
    down with it for the life of the process."""
    dist = tmp_path / "jdocmunch_mcp-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_bytes(b"Name: jdocmunch-mcp\nVersion: 1.0\xff\n")
    pkg = _isolate_package_dir(tmp_path, monkeypatch)
    (pkg / "server.py").write_text("x", encoding="utf-8")
    future = time.time() + 120
    os.utime(pkg / "server.py", (future, future))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(rs, "_running_version", lambda: "1.0.0")

    # The input really does raise in the resolver (a control, so the test is
    # not passing because the file happened to be readable).
    import importlib.metadata

    with pytest.raises(UnicodeDecodeError):
        importlib.metadata.version("jdocmunch-mcp")

    assert rs._installed_version() is None
    report = rs.staleness_report()
    assert report is not None
    assert "files_changed" in report["reasons"]
    assert "version_changed" not in report["reasons"]
    assert "installed_version" not in report


def test_env_var_disables_the_check(tmp_path, monkeypatch):
    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    (pkg / "server.py").write_text("x", encoding="utf-8")
    future = time.time() + 120
    os.utime(pkg / "server.py", (future, future))
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)
    monkeypatch.setenv("JDOCMUNCH_DISABLE_STALENESS_CHECK", "1")
    assert rs.staleness_report() is None


def test_a_broken_probe_never_raises_and_is_logged_once(monkeypatch, caplog):
    """A disclosure is never worth failing a tool call for, but the failure is
    logged once so it is not indistinguishable from a fresh server."""
    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rs, "_package_dir", boom)
    with caplog.at_level(logging.WARNING, logger=rs.logger.name):
        assert rs.staleness_report() is None
        rs._cache["checked_at"] = 0.0  # force a second probe
        assert rs.staleness_report() is None
    failures = [r for r in caplog.records if "probe failed" in r.getMessage()]
    assert len(failures) == 1
    assert "disk on fire" in str(failures[0].exc_info[1])


def test_result_is_cached_within_the_ttl(tmp_path, monkeypatch):
    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    calls = {"n": 0}

    def counting_dir():
        calls["n"] += 1
        return pkg

    monkeypatch.setattr(rs, "_package_dir", counting_dir)
    rs.staleness_report()
    first = calls["n"]
    rs.staleness_report()
    assert calls["n"] == first, "second call inside the TTL re-statted the disk"


def test_a_file_rewritten_after_process_start_but_before_the_first_check_is_reported(
    tmp_path, monkeypatch
):
    """A file rewritten between process start and the module's import must still
    count as newer than the code this process loaded, so where the OS gives a
    process start, that is the load time, not the import."""
    from jdocmunch_mcp import runtime_identity

    started = time.time() - 3600
    iso = datetime.fromtimestamp(started, tz=timezone.utc).isoformat()
    monkeypatch.setattr(
        runtime_identity, "get_process_start", lambda: {"value": iso, "source": "os"}
    )
    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    (pkg / "server.py").write_text("x", encoding="utf-8")
    rewritten = time.time() - 60
    os.utime(pkg / "server.py", (rewritten, rewritten))
    try:
        importlib.reload(rs)  # the "first call": import after the rewrite
        monkeypatch.setattr(rs, "_package_dir", lambda: pkg)
        report = rs.staleness_report()
    finally:
        monkeypatch.undo()
        importlib.reload(rs)
    assert report is not None
    assert report["changed_paths"] == ["server.py"]


def test_load_time_falls_back_to_import_time_without_an_os_start(monkeypatch):
    from jdocmunch_mcp import runtime_identity

    monkeypatch.setattr(
        runtime_identity,
        "get_process_start",
        lambda: {"value": "2000-01-01T00:00:00+00:00", "source": "self_recorded"},
    )
    before = time.time()
    assert rs._process_loaded_at() >= before


def test_the_server_imports_the_probe_when_it_loads():
    """The import-time fallback is only the server's load time if the server
    imports this module when it loads, not on the first tool call. Checked in a
    fresh interpreter, because this test module has already imported it."""
    import subprocess
    import sys

    code = (
        "import sys, jdocmunch_mcp.server; "
        "print('jdocmunch_mcp.runtime_staleness' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True"


def test_a_file_written_just_before_the_recorded_start_is_not_reported(
    tmp_path, monkeypatch
):
    """The Linux OS start is derived from a whole-second boot time and can read up
    to a second early, so a file written in that second must not mark a fresh
    server as superseded for its whole life."""
    pkg = tmp_path / "jdocmunch_mcp"
    pkg.mkdir()
    (pkg / "server.py").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rs, "_package_dir", lambda: pkg)
    loaded = time.time() - 600
    monkeypatch.setattr(rs, "_LOADED_AT", loaded)
    within = loaded + 1.0
    os.utime(pkg / "server.py", (within, within))
    assert rs.staleness_report() is None


@pytest.mark.asyncio
class TestServerAttach:
    """Drive the real `server.call_tool`, under the default config."""

    FAKE = {
        "stale": True,
        "running_version": "1.0.0",
        "reasons": ["version_changed"],
        "action": "Restart the client session.",
    }

    async def _call(self, tmp_path, monkeypatch, report):
        from jdocmunch_mcp.server import call_tool

        monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        monkeypatch.setattr(rs, "staleness_report", lambda: report)
        out = await call_tool("list_repos", {})
        return json.loads(out[0].text)

    async def test_default_config_delivers_the_top_level_key_and_no_meta(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("JDOCMUNCH_META_FIELDS", raising=False)
        body = await self._call(tmp_path, monkeypatch, self.FAKE)
        assert body["server_build_stale"] == self.FAKE
        assert "_meta" not in body

    async def test_meta_copy_follows_the_callers_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "all")
        body = await self._call(tmp_path, monkeypatch, self.FAKE)
        assert body["server_build_stale"] == self.FAKE
        assert body["_meta"]["server_build_stale"] == self.FAKE

    async def test_a_meta_fields_list_naming_the_key_gets_the_meta_copy(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "server_build_stale")
        body = await self._call(tmp_path, monkeypatch, self.FAKE)
        assert body["server_build_stale"] == self.FAKE
        assert body["_meta"]["server_build_stale"] == self.FAKE

    async def test_a_meta_fields_list_without_the_key_gets_only_the_top_level(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "powered_by")
        body = await self._call(tmp_path, monkeypatch, self.FAKE)
        assert body["server_build_stale"] == self.FAKE
        assert "server_build_stale" not in body.get("_meta", {})

    async def test_nothing_is_added_when_there_is_nothing_to_report(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "all")
        body = await self._call(tmp_path, monkeypatch, None)
        assert "server_build_stale" not in body
        assert "server_build_stale" not in body.get("_meta", {})
