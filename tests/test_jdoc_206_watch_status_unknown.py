"""A failed watch-service probe reads as unknown, never as inactive (#206).

On Linux, `systemctl --user is-active` needs the user's session bus. A process
started without `DBUS_SESSION_BUS_ADDRESS` or `XDG_RUNTIME_DIR` (a managed MCP
host, a cron job, an ssh shell) gets "Failed to connect to user scope bus" on
stderr and a nonzero exit. `_status_systemd` compared the output with "active"
and reported `active: false`, and `get_watch_status` published
`installed_active: false`: a probe that observed nothing was answered as "the
watcher is not running" (reported by @dkiaulakis).

The cases are the reporter's: a successful active query, a successful inactive
or missing-unit query, a missing user bus, a permission or transport failure,
and a timeout. The state is read from what the probe printed, never from its
exit code alone.

Ruling B (the owner, 2026-11-18): the fix is additive. `active` and
`installed_active` keep their 1.x boolean meaning, so they read False for an
unknown state, and the new `status` and `probe_error` keys carry the answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jdocmunch_mcp import service_installer as si

NO_BUS = (
    "Failed to connect to user scope bus via local transport: $DBUS_SESSION_BUS_ADDRESS "
    "and $XDG_RUNTIME_DIR not defined (consider using --machine=<user>@.host --user "
    "to connect to bus of other user)"
)


@pytest.fixture
def systemd(monkeypatch, tmp_path: Path):
    """A systemctl that answers what the test says, and a unit file the test owns."""
    unit = tmp_path / "jdocmunch-watch.service"
    monkeypatch.setattr(si, "_systemd_unit_path", lambda: unit)
    monkeypatch.setattr(si.shutil, "which", lambda name: "/usr/bin/systemctl")
    answer: dict = {}

    def run(args, **kw):
        answer["kw"] = kw
        if "raise" in answer:
            if not kw.get("timeout"):
                raise AssertionError("the probe must pass a timeout to subprocess.run")
            raise answer["raise"]
        return subprocess.CompletedProcess(
            args, answer.get("rc", 0), answer.get("out", ""), answer.get("err", "")
        )

    monkeypatch.setattr(si.subprocess, "run", run)
    return unit, answer


def test_a_successful_active_query_is_active(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="active\n", rc=0)
    s = si._status_systemd()
    assert s["active"] is True and s["status"] == "active", s


def test_a_reloading_unit_is_active_and_keeps_the_1x_flag(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="reloading\n", rc=0)
    s = si._status_systemd()
    assert s["status"] == "active" and s["active"] is False, s


def test_a_successful_inactive_query_is_inactive(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="inactive\n", rc=3)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "inactive", s
    assert "probe_error" not in s


def test_a_failed_unit_is_inactive_and_keeps_its_state(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="failed\n", rc=3)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "inactive" and s["state"] == "failed", s


def test_a_missing_unit_is_not_installed(systemd):
    unit, answer = systemd
    answer.update(out="inactive\n", rc=4)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_old_systemd_unknown_for_an_unloaded_unit_is_not_installed(systemd):
    """systemd 219 prints `unknown` for a unit it never loaded: a successful query."""
    unit, answer = systemd
    answer.update(out="unknown\n", rc=3)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_a_missing_user_bus_is_unknown_and_keeps_the_diagnostic(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err=NO_BUS + "\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "unknown", s
    assert s["probe_error"] == "no_user_bus", s
    assert s["detail"] == NO_BUS, s


def test_a_permission_failure_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="Failed to connect to bus: Permission denied\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "unknown", s
    assert s["probe_error"] == "permission_denied", s


def test_a_transport_failure_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="Transport endpoint is not connected\n", rc=1)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "unknown", s
    assert s["probe_error"] == "probe_failed", s


def test_a_transport_error_naming_no_bus_is_not_called_a_missing_bus(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="Device or resource busy\n", rc=1)
    s = si._status_systemd()
    assert s["status"] == "unknown" and s["probe_error"] == "probe_failed", s


def test_a_probe_timeout_is_unknown(systemd):
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer["raise"] = subprocess.TimeoutExpired(["systemctl"], 5)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "unknown", s
    assert s["probe_error"] == "timeout", s
    assert answer["kw"]["timeout"] == si._PROBE_TIMEOUT_SECONDS


def test_a_nonzero_exit_alone_is_not_inactive(systemd):
    """Classify the output, not the exit code."""
    unit, answer = systemd
    unit.write_text("[Unit]\n")
    answer.update(out="", err="", rc=1)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "unknown", s


def test_no_systemctl_means_no_systemd_service(monkeypatch, tmp_path):
    monkeypatch.setattr(si, "_systemd_unit_path", lambda: tmp_path / "x.service")
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    s = si._status_systemd()
    assert s["active"] is False and s["status"] == "not_installed", s


@pytest.fixture
def launchd(monkeypatch, tmp_path: Path):
    plist = tmp_path / "us.gravelle.jdocmunch-watch.plist"
    monkeypatch.setattr(si, "_launchd_plist_path", lambda: plist)
    answer: dict = {}

    def run(args, **kw):
        assert kw.get("timeout"), "the probe must pass a timeout"
        return subprocess.CompletedProcess(
            args, answer.get("rc", 0), answer.get("out", ""), answer.get("err", "")
        )

    monkeypatch.setattr(si.subprocess, "run", run)
    return plist, answer


def test_launchd_a_running_job_is_active(launchd):
    plist, answer = launchd
    answer.update(rc=0, out='{\n\t"PID" = 42;\n};')
    s = si._status_launchd()
    assert s["active"] is True and s["status"] == "active", s


def test_launchd_a_loaded_job_with_no_process_is_inactive(launchd):
    """Loaded, so `active` keeps its meaning; no PID, so nothing is running."""
    plist, answer = launchd
    plist.write_text("<plist/>")
    answer.update(rc=0, out='{\n\t"LastExitStatus" = 256;\n\t"Label" = "x";\n};')
    s = si._status_launchd()
    assert s["active"] is True and s["status"] == "inactive", s


@pytest.mark.parametrize("has_plist,status", [(True, "inactive"), (False, "not_installed")])
def test_launchd_an_unknown_label_is_inactive_or_not_installed(launchd, has_plist, status):
    plist, answer = launchd
    if has_plist:
        plist.write_text("<plist/>")
    answer.update(rc=113, err='Could not find service "us.gravelle.jdocmunch-watch" in domain for port')
    s = si._status_launchd()
    assert s["active"] is False and s["status"] == status, s


def test_launchd_another_failure_is_unknown_not_inactive(launchd):
    plist, answer = launchd
    plist.write_text("<plist/>")
    answer.update(rc=1, err="Bootstrap failed: 5: Input/output error")
    s = si._status_launchd()
    assert s["active"] is False and s["status"] == "unknown", s
    assert s["probe_error"] == "probe_failed", s


def test_launchd_a_permission_failure_is_unknown(launchd):
    plist, answer = launchd
    plist.write_text("<plist/>")
    answer.update(rc=1, err="Permission denied")
    s = si._status_launchd()
    assert s["active"] is False and s["probe_error"] == "permission_denied", s


@pytest.mark.parametrize(
    "exc,probe_error",
    [(subprocess.TimeoutExpired(["launchctl"], 5), "timeout"), (OSError("no launchctl"), "probe_failed")],
)
def test_launchd_a_probe_that_could_not_run_is_unknown(monkeypatch, exc, probe_error):
    def run(args, **kw):
        assert kw.get("timeout") == si._PROBE_TIMEOUT_SECONDS
        raise exc

    monkeypatch.setattr(si.subprocess, "run", run)
    s = si._status_launchd()
    assert s["active"] is False and s["status"] == "unknown" and s["probe_error"] == probe_error, s


def _windows(monkeypatch, rc, out, state, calls=None):
    def run(args, **kw):
        assert kw.get("timeout") == si._PROBE_TIMEOUT_SECONDS, "schtasks must have a timeout"
        return subprocess.CompletedProcess(args, rc, out, "")

    def probe():
        if calls is not None:
            calls.append(1)
        return state

    monkeypatch.setattr(si.subprocess, "run", run)
    monkeypatch.setattr(si, "_scheduled_task_probe", probe)


def test_windows_an_english_running_task_is_active_without_powershell(monkeypatch):
    calls: list = []
    _windows(monkeypatch, 0, "TaskName: \\jdocmunch-watch\nStatus: Running\n", None, calls)
    s = si._status_windows()
    assert s["active"] is True and s["status"] == "active", s
    assert calls == [], "a readable English answer must not spawn PowerShell"


def test_windows_english_text_on_a_failed_query_is_not_trusted(monkeypatch):
    """`status` needs a successful query; `active` keeps its 1.x text match."""
    _windows(monkeypatch, 1, "Running", None)
    s = si._status_windows()
    assert s["status"] == "unknown" and s["active"] is True, s


@pytest.mark.parametrize(
    "state,active,status",
    [
        # `active` keeps its 1.x meaning, the English text match, so German
        # text reads False; `status` is the answer.
        ("Running", False, "active"), ("Ready", False, "active"), ("Queued", False, "active"),
        ("Disabled", False, "inactive"),
    ],
)
def test_windows_a_localized_task_is_read_from_its_state(monkeypatch, state, active, status):
    """German display text, so only the State enum can answer."""
    _windows(monkeypatch, 0, "Aufgabenname: \\jdocmunch-watch\nStatus: Bereit\n", state)
    s = si._status_windows()
    assert s["active"] is active and s["status"] == status and s["state"] == state, s


def test_windows_a_task_that_is_not_registered_is_not_installed(monkeypatch):
    _windows(monkeypatch, 1, "", "absent")
    s = si._status_windows()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_windows_a_task_that_could_not_be_read_is_unknown_not_inactive(monkeypatch):
    _windows(monkeypatch, 0, "Aufgabenname: \\jdocmunch-watch\nStatus: Bereit\n", None)
    s = si._status_windows()
    assert s["active"] is False and s["status"] == "unknown", s


@pytest.mark.parametrize(
    "rc,stdout,expected",
    [
        (0, b"Running\r\n", "Running"),
        (0, b"Disabled\r\n", "Disabled"),
        (0, b"absent\r\n", "absent"),
        (0, b"error\r\n", None),
        (0, b"", None),
        (1, b"absent\r\n", None),
    ],
)
def test_windows_the_task_probe_reads_a_fixed_token(monkeypatch, rc, stdout, expected):
    monkeypatch.setattr(
        si.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, rc, stdout, b""),
    )
    assert si._scheduled_task_probe() == expected


def test_an_unsupported_platform_has_no_service(monkeypatch):
    monkeypatch.setattr(si.platform, "system", lambda: "Plan9")
    s = si.service_status()
    assert s["active"] is False and s["status"] == "not_installed", s


def test_a_probe_with_no_classification_is_unknown(monkeypatch):
    monkeypatch.setattr(si.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(si, "_status_launchd", lambda: {"platform": "launchd", "active": False})
    assert si.service_status()["status"] == "unknown"


@pytest.mark.parametrize(
    "service,installed_active",
    [
        ({"platform": "systemd", "active": True, "status": "active"}, True),
        ({"platform": "systemd", "active": False, "status": "inactive"}, False),
        ({"platform": "systemd", "active": False, "status": "not_installed"}, False),
        ({"platform": "systemd", "active": False, "status": "unknown", "probe_error": "no_user_bus"}, False),
        # launchd: loaded but not running keeps its 1.x `true`; `status` says inactive.
        ({"platform": "launchd", "active": True, "status": "inactive"}, True),
    ],
)
def test_get_watch_status_adds_status_and_keeps_installed_active(
    monkeypatch, tmp_path, service, installed_active
):
    monkeypatch.setattr(si, "service_status", lambda: dict(service))
    from jdocmunch_mcp.tools.get_watch_status import get_watch_status

    out = get_watch_status(storage_path=str(tmp_path / "empty"))["service"]
    assert out["installed_active"] is installed_active, out
    assert out["status"] == service["status"], out
    assert out["detail"]["status"] == service["status"], out


def test_get_watch_status_reports_a_raising_probe_as_unknown(monkeypatch, tmp_path):
    def boom():
        raise OSError("probe exploded")

    monkeypatch.setattr(si, "service_status", boom)
    from jdocmunch_mcp.tools.get_watch_status import get_watch_status

    out = get_watch_status(storage_path=str(tmp_path / "empty"))["service"]
    assert out["installed_active"] is False and out["status"] == "unknown", out
    assert out["detail"]["probe_error"] == "probe_failed", out
    assert "probe exploded" in out["detail"]["detail"], out
