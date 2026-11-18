"""Cross-platform login-service installer for `jdocmunch-mcp watch`.

- Linux: systemd --user unit at ~/.config/systemd/user/jdocmunch-watch.service
- macOS: launchd plist at ~/Library/LaunchAgents/us.gravelle.jdocmunch-watch.plist
- Windows: Task Scheduler task named `jdocmunch-watch`

The installer deliberately invokes the *same interpreter* currently running
(via `sys.executable -m jdocmunch_mcp watch`) so the service picks up whatever
virtualenv the user installed into, avoiding a whole class of PATH issues.

Ported from jcodemunch-mcp's watch-all service installer, scoped to the doc
watcher: service name `jdocmunch-watch`, exec `watch`, logs under DOC_INDEX_PATH.
"""
from __future__ import annotations

import logging
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

SERVICE_NAME = "jdocmunch-watch"
LAUNCHD_LABEL = "us.gravelle.jdocmunch-watch"


class InstallerError(RuntimeError):
    pass


# ── Path helpers ────────────────────────────────────────────────────────────


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _log_dir() -> Path:
    from .storage.paths import default_root  # jdoc#146
    base = default_root()
    return base / "logs"


def _exec_cmd(watch_args: Optional[Sequence[str]] = None) -> list[str]:
    """How the service should invoke the doc watcher.

    ``watch_args`` are appended verbatim, so the installed service runs the
    same daemon with the same flags the caller would have typed for a
    foreground ``jdocmunch-mcp watch`` (jdoc#120). One source of truth for the
    daemon's arguments, identical on all three platforms.
    """
    return [sys.executable, "-m", "jdocmunch_mcp", "watch", *(watch_args or [])]


# ⚠ jdoc#120: every installer below REWRITES its whole service definition on
# each run, and `watch-install` is a normal step in an upgrade routine. A
# hand-edited definition is therefore reverted. The revert stays — a merge
# would make the installed argv unpredictable — but it is no longer SILENT:
# each installer reports the argv it replaced so the caller can say so.
def _replaced_exec(current, planned) -> Optional[dict]:
    """Describe a customised definition this install is about to overwrite.

    Both readings are whatever the platform natively stores: an argv list for
    systemd and launchd, a command string for Task Scheduler.
    """
    if not current or current == planned:
        return None
    return {"previous": current, "installed": planned}


def _installed_systemd_exec() -> Optional[str]:
    """ExecStart line of the currently-installed unit, or None if unreadable.

    ⚠ Returns the raw string, NOT an argv. Splitting it back into arguments
    means guessing which quoting convention wrote it, and the first cut of this
    got that wrong: `shlex.split` in POSIX mode eats backslashes, so a path with
    one round-tripped to something that never equalled what we were about to
    write, and every re-install claimed to have overwritten a customisation.
    The comparison target is a string this module generated, so compare strings.
    """
    try:
        text = _systemd_unit_path().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart="):].strip() or None
    return None


def _installed_launchd_exec() -> Optional[list[str]]:
    """ProgramArguments of the currently-installed plist, or None."""
    try:
        with _launchd_plist_path().open("rb") as fh:
            data = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    args = data.get("ProgramArguments")
    if isinstance(args, list) and all(isinstance(a, str) for a in args):
        return args
    return None


def _installed_windows_exec() -> Optional[str]:
    """Task-To-Run command line of the registered task, or None.

    Returns the raw string rather than an argv, because that is what
    `_install_windows` writes and comparing the two needs no round-trip
    through a quoting convention `schtasks` never promised to preserve.

    ⚠ `schtasks` labels its output in the SYSTEM's display language, so the
    field name is not stable. An unparsed reading returns None and the caller
    simply says nothing — a false "we overwrote your customisation" warning is
    worse than no warning.
    """
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", SERVICE_NAME, "/FO", "LIST", "/V"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        label, sep, value = line.partition(":")
        if sep and label.strip().lower() == "task to run":
            return value.strip() or None
    return None


# ── systemd (Linux) ─────────────────────────────────────────────────────────


_SYSTEMD_TEMPLATE = """[Unit]
Description=jdocmunch-mcp: auto-reindex every locally-indexed doc repo
After=default.target

[Service]
Type=simple
ExecStart={exec_cmd}
Restart=on-failure
RestartSec=5
StandardOutput=append:{log_dir}/watch.log
StandardError=append:{log_dir}/watch.err
Environment=PYTHONUNBUFFERED=1
{env_lines}

[Install]
WantedBy=default.target
"""


def _systemd_env_lines() -> str:
    """Forward DOC_INDEX_PATH and JDOCMUNCH_* env into the unit."""
    lines = []
    for key, val in os.environ.items():
        if key == "DOC_INDEX_PATH" or key.startswith("JDOCMUNCH_"):
            lines.append(f"Environment={key}={val}")
    return "\n".join(lines)


def _install_systemd(watch_args: Optional[Sequence[str]] = None) -> dict:
    if shutil.which("systemctl") is None:
        raise InstallerError("systemctl not found — is this a systemd system?")
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    _log_dir().mkdir(parents=True, exist_ok=True)

    quoted = " ".join(_shell_quote(x) for x in _exec_cmd(watch_args))
    replaced = _replaced_exec(_installed_systemd_exec(), quoted)
    unit_path.write_text(
        _SYSTEMD_TEMPLATE.format(
            exec_cmd=quoted,
            log_dir=str(_log_dir()),
            env_lines=_systemd_env_lines(),
        ),
        encoding="utf-8",
    )

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"], check=True)
    out = {"platform": "systemd", "unit": str(unit_path), "status": "enabled"}
    if replaced:
        out["replaced_exec"] = replaced
    return out


def _uninstall_systemd() -> dict:
    unit_path = _systemd_unit_path()
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"], check=False)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    removed = False
    if unit_path.exists():
        unit_path.unlink()
        removed = True
    return {"platform": "systemd", "unit": str(unit_path), "removed": removed}


# What `systemctl is-active` prints when it reached the service manager. Any
# other output (an empty stdout, a bus or permission error on stderr) means the
# query did not happen, so the service's state is UNKNOWN, never inactive (#206).
_SYSTEMD_ACTIVE_STATES = frozenset({"active", "reloading", "refreshing"})
# `unknown` is what older systemd (219, RHEL/CentOS 7) prints for a unit it
# never loaded: a query that succeeded, settled by the unit-file check below.
_SYSTEMD_INACTIVE_STATES = frozenset(
    {"inactive", "failed", "activating", "deactivating", "maintenance", "unknown"}
)
_PROBE_TIMEOUT_SECONDS = 5.0


def _systemd_probe_error(stderr: str) -> str:
    """A stable class for a probe that did not reach the user's service manager."""
    low = stderr.lower()
    if "permission denied" in low or "access denied" in low:
        return "permission_denied"
    if (
        "dbus_session_bus_address" in low
        or "xdg_runtime_dir" in low
        or "user scope bus" in low
        or "failed to connect to bus" in low
    ):
        return "no_user_bus"
    return "probe_failed"


def _status_systemd() -> dict:
    """The watch service's state on systemd: `active`, `inactive`,
    `not_installed` or `unknown`.

    `active` keeps its 1.x meaning (`is-active` printed `active`), so it is
    False when the probe could not ask; `status` says why. A process started without
    the user's session bus (`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`) gets
    a bus error and a nonzero exit, and reading that as inactive told the user
    a running watcher was stopped (#206). The state is read from what
    `is-active` printed, never from its exit code.
    """
    if shutil.which("systemctl") is None:
        # The installer refuses without systemctl, so no unit of ours can run.
        return {
            "platform": "systemd", "active": False, "status": "not_installed",
            "reason": "systemctl not found",
        }
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{SERVICE_NAME}.service"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "platform": "systemd", "active": False, "status": "unknown",
            "probe_error": "timeout",
            "detail": f"systemctl --user is-active did not answer in {_PROBE_TIMEOUT_SECONDS:g} s",
        }
    except OSError as exc:
        return {
            "platform": "systemd", "active": False, "status": "unknown",
            "probe_error": "probe_failed", "detail": str(exc),
        }
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    state = out.splitlines()[0].strip() if out else ""
    if state in _SYSTEMD_ACTIVE_STATES:
        return {"platform": "systemd", "active": state == "active", "status": "active", "state": state}
    if state in _SYSTEMD_INACTIVE_STATES:
        installed = _systemd_unit_path().exists()
        return {
            "platform": "systemd", "active": False,
            "status": "inactive" if installed else "not_installed",
            "state": state,
        }
    return {
        "platform": "systemd", "active": False, "status": "unknown",
        "probe_error": _systemd_probe_error(err),
        "state": out or err,
        "detail": err or out or f"systemctl --user is-active exited {result.returncode} with no output",
    }


# ── launchd (macOS) ─────────────────────────────────────────────────────────


_LAUNCHD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log_dir}/watch.log</string>
  <key>StandardErrorPath</key><string>{log_dir}/watch.err</string>
  <key>EnvironmentVariables</key>
  <dict>
{env}
  </dict>
</dict></plist>
"""


def _launchd_env_xml() -> str:
    out = []
    for key, val in os.environ.items():
        if key == "DOC_INDEX_PATH" or key == "PATH" or key.startswith("JDOCMUNCH_"):
            out.append(f"    <key>{_xml_escape(key)}</key><string>{_xml_escape(val)}</string>")
    return "\n".join(out)


def _install_launchd(watch_args: Optional[Sequence[str]] = None) -> dict:
    plist = _launchd_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    _log_dir().mkdir(parents=True, exist_ok=True)
    exec_argv = _exec_cmd(watch_args)
    replaced = _replaced_exec(_installed_launchd_exec(), exec_argv)
    args_xml = "\n".join(f"    <string>{_xml_escape(a)}</string>" for a in exec_argv)
    plist.write_text(
        _LAUNCHD_TEMPLATE.format(
            label=LAUNCHD_LABEL,
            args=args_xml,
            log_dir=str(_log_dir()),
            env=_launchd_env_xml(),
        ),
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    out = {"platform": "launchd", "plist": str(plist), "status": "loaded"}
    if replaced:
        out["replaced_exec"] = replaced
    return out


def _uninstall_launchd() -> dict:
    plist = _launchd_plist_path()
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    removed = False
    if plist.exists():
        plist.unlink()
        removed = True
    return {"platform": "launchd", "plist": str(plist), "removed": removed}


def _status_launchd() -> dict:
    """`active` keeps its meaning (the job is loaded); `status` is read from
    what `launchctl` said, never from its exit code alone. A label launchd
    does not know is `inactive` when our plist exists and `not_installed`
    when it does not; any other failure is `unknown` (#206)."""
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "platform": "launchd", "active": False, "status": "unknown",
            "probe_error": "timeout",
            "detail": f"launchctl list did not answer in {_PROBE_TIMEOUT_SECONDS:g} s",
        }
    except OSError as exc:
        return {
            "platform": "launchd", "active": False, "status": "unknown",
            "probe_error": "probe_failed", "detail": str(exc),
        }
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode == 0:
        # Loaded. A job with no `"PID" =` line is loaded but not running.
        running = '"PID" =' in out
        return {
            "platform": "launchd", "active": True,
            "status": "active" if running else "inactive", "detail": out,
        }
    if "could not find service" in (err + " " + out).lower():
        installed = _launchd_plist_path().exists()
        return {
            "platform": "launchd", "active": False,
            "status": "inactive" if installed else "not_installed",
            "detail": err or out,
        }
    return {
        "platform": "launchd", "active": False, "status": "unknown",
        "probe_error": "permission_denied" if "permission" in err.lower() else "probe_failed",
        "detail": err or out or f"launchctl list exited {result.returncode} with no output",
    }


# ── Task Scheduler (Windows) ────────────────────────────────────────────────


def _install_windows(watch_args: Optional[Sequence[str]] = None) -> dict:
    _log_dir().mkdir(parents=True, exist_ok=True)
    cmd_str = " ".join(_cmd_quote(x) for x in _exec_cmd(watch_args))
    replaced = _replaced_exec(_installed_windows_exec(), cmd_str)
    args = [
        "schtasks", "/Create", "/F",
        "/TN", SERVICE_NAME,
        "/SC", "ONLOGON",
        "/RL", "LIMITED",
        "/TR", cmd_str,
    ]
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise InstallerError(f"schtasks /Create failed: {result.stderr.strip() or result.stdout.strip()}")
    subprocess.run(["schtasks", "/Run", "/TN", SERVICE_NAME], check=False, capture_output=True)
    out = {"platform": "schtasks", "task": SERVICE_NAME, "status": "registered"}
    if replaced:
        out["replaced_exec"] = replaced
    return out


def _uninstall_windows() -> dict:
    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return {"platform": "schtasks", "task": SERVICE_NAME, "removed": result.returncode == 0}


# Task Scheduler's `State` enum, which Get-ScheduledTask prints the same in
# every display language. `Ready` is an enabled task waiting for its logon
# trigger, which the 1.x `active` has always counted (#206).
_TASK_ACTIVE_STATES = frozenset({"Running", "Ready", "Queued"})
_TASK_INACTIVE_STATES = frozenset({"Disabled"})


def _scheduled_task_probe() -> Optional[str]:
    """The watch task's `State` (`Running`, `Ready`, `Disabled`, ...),
    `absent` when it is not registered, or None when that could not be
    asked. Both answers are fixed tokens, so neither depends on the display
    language that `schtasks` text is printed in."""
    # `absent` only for Get-ScheduledTask's own not-found error id, which is
    # invariant across display languages. A missing cmdlet is also category
    # ObjectNotFound, so the category alone is not enough. Any other failure
    # prints `error` and reads as unknown, never as a task that is not there.
    script = (
        f"try {{ $t = Get-ScheduledTask -TaskName '{SERVICE_NAME}' -ErrorAction Stop; [string]$t.State }}"
        " catch { if ($_.FullyQualifiedErrorId -like '*_NotFound_*,Get-ScheduledTask') { 'absent' } else { 'error' } }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=False, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Get-ScheduledTask probe failed", exc_info=True)
        return None
    if result.returncode != 0:
        return None
    word = (result.stdout or b"").decode("ascii", errors="replace").strip()
    if word == "absent" or word in _TASK_ACTIVE_STATES or word in _TASK_INACTIVE_STATES:
        return word
    return None


def _status_windows() -> dict:
    """An English `Running`/`Ready` from `schtasks` answers at once. Anything
    else asks Get-ScheduledTask for the task's `State`, which does not depend
    on the display language. An answer neither can give is `unknown`, never
    `inactive` (#206). `active` keeps its 1.x meaning, the English text
    match, on every branch."""
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", SERVICE_NAME, "/FO", "LIST"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=_PROBE_TIMEOUT_SECONDS,
        )
        out = (result.stdout or "").strip()
        rc = result.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("schtasks query failed", exc_info=True)
        out, rc = str(exc), None
    # The 1.x `active`, unchanged: an English `Running`/`Ready` in the text.
    legacy_active = "Running" in out or "Ready" in out
    if rc == 0 and legacy_active:
        return {
            "platform": "schtasks", "active": True, "status": "active",
            "state_source": "display_text", "detail": out[:400],
        }
    state = _scheduled_task_probe()
    if state == "absent":
        return {"platform": "schtasks", "active": legacy_active, "status": "not_installed", "detail": out[:400]}
    if state in _TASK_ACTIVE_STATES:
        return {
            "platform": "schtasks", "active": legacy_active, "status": "active", "state": state,
            "state_source": "task_state", "detail": out[:400],
        }
    if state in _TASK_INACTIVE_STATES:
        return {
            "platform": "schtasks", "active": legacy_active, "status": "inactive", "state": state,
            "state_source": "task_state", "detail": out[:400],
        }
    return {
        "platform": "schtasks", "active": legacy_active, "status": "unknown",
        "probe_error": "probe_failed", "detail": out[:400],
    }


# ── Public dispatch ─────────────────────────────────────────────────────────


def install_service(watch_args: Optional[Sequence[str]] = None) -> dict:
    """Install the doc watcher as a login service.

    ``watch_args`` are flags for the ``watch`` daemon itself (jdoc#120), e.g.
    ``["--no-ai-summaries"]``. They default to none, so an existing caller
    installs exactly what it installed before.
    """
    sys_ = platform.system()
    if sys_ == "Linux":
        return _install_systemd(watch_args)
    if sys_ == "Darwin":
        return _install_launchd(watch_args)
    if sys_ == "Windows":
        return _install_windows(watch_args)
    raise InstallerError(f"Unsupported platform: {sys_}")


def uninstall_service() -> dict:
    sys_ = platform.system()
    if sys_ == "Linux":
        return _uninstall_systemd()
    if sys_ == "Darwin":
        return _uninstall_launchd()
    if sys_ == "Windows":
        return _uninstall_windows()
    raise InstallerError(f"Unsupported platform: {sys_}")


def service_status() -> dict:
    """The watch service's state. Every answer carries `status`: `active`,
    `inactive`, `not_installed` or `unknown`, read from what the probe said
    and never from an exit code alone (#206). `active` keeps each platform's
    1.x meaning and stays a boolean, so it reads False for an unknown state;
    a reader that needs the classification reads `status`."""
    sys_ = platform.system()
    if sys_ == "Linux":
        out = _status_systemd()
    elif sys_ == "Darwin":
        out = _status_launchd()
    elif sys_ == "Windows":
        out = _status_windows()
    else:
        out = {"platform": sys_, "active": False, "status": "not_installed", "reason": "unsupported"}
    out.setdefault("status", "unknown")
    return out


# ── escaping helpers ────────────────────────────────────────────────────────


def _shell_quote(s: str) -> str:
    if not s or any(c in s for c in ' \t"\''):
        return "'" + s.replace("'", "'\\''") + "'"
    return s


def _cmd_quote(s: str) -> str:
    if " " in s or "\t" in s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )
