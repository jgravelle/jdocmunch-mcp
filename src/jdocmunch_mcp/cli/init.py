"""jdocmunch-mcp init — one-command onboarding for MCP clients."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_MD_MARKER = "## Doc Exploration Policy"

_CLAUDE_MD_POLICY = """\
## Doc Exploration Policy

Always use jDocMunch-MCP tools for documentation navigation. Never fall back to Read for doc exploration.
**Exception:** Use `Read` when you need exact line numbers for `Edit`.

**Start any session:**
1. `doc_list_repos` — check what's indexed. If your docs aren't there: `index_local { "path": "." }`

**Finding content:**
- keyword/topic search -> `search_sections` (returns summaries only)
- browse structure -> `get_toc` (flat) or `get_toc_tree` (nested)
- single document -> `get_document_outline`

**Reading content:**
- one section -> `get_section` (full content via byte-range)
- multiple sections -> `get_sections` (batch)
- section + context -> `get_section_context` (ancestors + children)

**Maintenance:**
- broken internal links -> `get_broken_links`
- code/doc coverage gap -> `get_doc_coverage`
"""

_MCP_ENTRY = {
    "command": "uvx",
    "args": ["jdocmunch-mcp"],
}

def _hook_invocation() -> str:
    """Return the executable path used in hook command strings (#39).

    Claude Code spawns hooks via /bin/sh (macOS/Linux) or bash (Windows
    Git Bash / MSYS), which uses a minimal PATH that excludes ~/.local/bin,
    user venvs, and pipx shims. Writing the bare name ``jdocmunch-mcp`` works
    only when the subshell's PATH happens to match — fragile. Resolve to an
    absolute path at install time so hooks run regardless of the spawning
    shell. On Windows, normalise to forward slashes: bash treats every ``\\``
    as an escape and silently eats them, mangling the path at execution time.
    """
    resolved = shutil.which("jdocmunch-mcp")
    if not resolved:
        # Fall back to bare name; user gets a clear error if PATH is wrong.
        return "jdocmunch-mcp"
    if platform.system() == "Windows":
        resolved = resolved.replace("\\", "/")
    if " " in resolved:
        return f'"{resolved}"'
    return resolved


def _enforcement_hooks() -> dict[str, list]:
    """Build the enforcement hook entries from the resolved executable (#39)."""
    exe = _hook_invocation()
    return {
        "PreToolUse": [{
            "matcher": "Read",
            "hooks": [{"type": "command", "command": f"{exe} hook-pretooluse"}],
        }],
        "PostToolUse": [{
            "matcher": "Edit|Write",
            "hooks": [{"type": "command", "command": f"{exe} hook-posttooluse"}],
        }],
        "PreCompact": [{
            "matcher": "",
            "hooks": [{"type": "command", "command": f"{exe} hook-precompact"}],
        }],
        # #131: PreCompact has no model-facing output channel. The snapshot is
        # restored here, on the sources whose prior state is this session's.
        "SessionStart": [{
            "matcher": "compact|resume|fork",
            "hooks": [{"type": "command", "command": f"{exe} hook-sessionstart"}],
        }],
    }

# Cursor rules use MDC format (frontmatter + markdown).
_CURSOR_RULES_CONTENT = """\
---
description: Use jDocMunch MCP tools for all documentation navigation instead of built-in search
alwaysApply: true
---

""" + _CLAUDE_MD_POLICY

# Windsurf uses a plain-text .windsurfrules file in the project root.
_WINDSURF_RULES_CONTENT = _CLAUDE_MD_POLICY


# ---------------------------------------------------------------------------
# Client detection
# ---------------------------------------------------------------------------

class MCPClient:
    """Represents a detected MCP client and how to configure it."""

    def __init__(self, name: str, config_path: Optional[Path], method: str):
        self.name = name
        self.config_path = config_path
        self.method = method  # "cli" | "json_patch"

    def __repr__(self) -> str:
        if self.config_path:
            return f"{self.name} ({self.config_path})"
        return self.name


def _find_executable(name: str) -> Optional[str]:
    """Return path to executable or None."""
    return shutil.which(name)


def _expand_appdata(*parts: str) -> Path:
    """Expand %APPDATA% on Windows, ~/ on others."""
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata, *parts)
    return Path.home().joinpath(*parts)


def _detect_clients() -> list[MCPClient]:
    """Detect installed MCP clients."""
    clients: list[MCPClient] = []

    # Claude Code CLI
    if _find_executable("claude"):
        clients.append(MCPClient("Claude Code", None, "cli"))

    # Claude Desktop
    if platform.system() == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif platform.system() == "Windows":
        p = _expand_appdata("Claude", "claude_desktop_config.json")
    else:
        p = Path.home() / ".config" / "claude" / "claude_desktop_config.json"
    if p.parent.exists():
        clients.append(MCPClient("Claude Desktop", p, "json_patch"))

    # Cursor
    cursor_dir = Path.home() / ".cursor"
    if cursor_dir.exists():
        clients.append(MCPClient("Cursor", cursor_dir / "mcp.json", "json_patch"))

    # Windsurf
    for d in [Path.home() / ".windsurf", Path.home() / ".codeium" / "windsurf"]:
        if d.exists():
            clients.append(MCPClient("Windsurf", d / "mcp_config.json", "json_patch"))
            break

    # Continue
    continue_dir = Path.home() / ".continue"
    if continue_dir.exists():
        clients.append(MCPClient("Continue", continue_dir / "config.json", "json_patch"))

    return clients


# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning {} if it doesn't exist."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any], *, backup: bool = True) -> None:
    """Write JSON, optionally creating a .bak backup first."""
    if backup and path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _has_jdocmunch_entry(data: dict[str, Any]) -> bool:
    """Check if jdocmunch is already configured in an MCP config."""
    servers = data.get("mcpServers", {})
    return "jdocmunch" in servers


def _patch_mcp_config(path: Path, *, backup: bool = True, dry_run: bool = False) -> str:
    """Add jdocmunch entry to an MCP client JSON config.

    Returns a status message.
    """
    data = _read_json(path)
    if _has_jdocmunch_entry(data):
        return f"  already configured in {path}"

    if dry_run:
        return f"  would add jdocmunch to {path}"

    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"]["jdocmunch"] = _MCP_ENTRY
    _write_json(path, data, backup=backup)
    return f"  added jdocmunch to {path}"


def _configure_claude_code(*, dry_run: bool = False) -> str:
    """Run `claude mcp add` for Claude Code CLI."""
    if dry_run:
        return "  would run: claude mcp add jdocmunch uvx jdocmunch-mcp"
    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "jdocmunch", "uvx", "jdocmunch-mcp"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if result.returncode == 0:
            return "  ran: claude mcp add jdocmunch uvx jdocmunch-mcp"
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            return "  already configured in Claude Code"
        return f"  claude mcp add failed: {stderr or result.stdout.strip()}"
    except FileNotFoundError:
        return "  claude CLI not found — skipped"
    except subprocess.TimeoutExpired:
        return "  claude mcp add timed out"


def configure_client(client: MCPClient, *, backup: bool = True, dry_run: bool = False) -> str:
    """Configure a single MCP client. Returns a status message."""
    if client.method == "cli":
        return _configure_claude_code(dry_run=dry_run)
    elif client.method == "json_patch" and client.config_path:
        return _patch_mcp_config(client.config_path, backup=backup, dry_run=dry_run)
    return f"  unknown method for {client.name}"


# ---------------------------------------------------------------------------
# CLAUDE.md injection
# ---------------------------------------------------------------------------

def _claude_md_path(scope: str) -> Path:
    """Return the CLAUDE.md path for the given scope."""
    if scope == "global":
        return Path.home() / ".claude" / "CLAUDE.md"
    return Path.cwd() / "CLAUDE.md"


def _has_policy(path: Path) -> bool:
    """Check if the Doc Exploration Policy marker already exists."""
    if not path.exists():
        return False
    return _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8")


def install_claude_md(scope: str = "global", *, dry_run: bool = False, backup: bool = True) -> str:
    """Append the Doc Exploration Policy to CLAUDE.md.

    scope: "global" or "project"
    Returns a status message.
    """
    path = _claude_md_path(scope)
    if _has_policy(path):
        return f"  policy already present in {path}"
    if dry_run:
        return f"  would append policy to {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".md.bak"))

    with open(path, "a", encoding="utf-8") as f:
        if path.exists() and path.stat().st_size > 0:
            f.write("\n\n")
        f.write(_CLAUDE_MD_POLICY)

    return f"  appended policy to {path}"


# ---------------------------------------------------------------------------
# Cursor rules injection
# ---------------------------------------------------------------------------

def _cursor_rules_path() -> Path:
    """Return the project-level Cursor rules path for jdocmunch."""
    return Path.cwd() / ".cursor" / "rules" / "jdocmunch.mdc"


def install_cursor_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    """Write .cursor/rules/jdocmunch.mdc in the current project.

    Returns a status message.
    """
    path = _cursor_rules_path()
    if path.exists() and _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8"):
        return f"  already present in {path}"
    if dry_run:
        return f"  would write {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".mdc.bak"))

    path.write_text(_CURSOR_RULES_CONTENT, encoding="utf-8")
    return f"  wrote {path}"


# ---------------------------------------------------------------------------
# Windsurf rules injection
# ---------------------------------------------------------------------------

def _windsurf_rules_path() -> Path:
    """Return the project-level .windsurfrules path."""
    return Path.cwd() / ".windsurfrules"


def install_windsurf_rules(*, dry_run: bool = False, backup: bool = True) -> str:
    """Append the Doc Exploration Policy to .windsurfrules.

    Returns a status message.
    """
    path = _windsurf_rules_path()
    if path.exists() and _CLAUDE_MD_MARKER in path.read_text(encoding="utf-8"):
        return f"  already present in {path}"
    if dry_run:
        return f"  would append policy to {path}"

    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".windsurfrules.bak"))

    with open(path, "a", encoding="utf-8") as f:
        if path.exists() and path.stat().st_size > 0:
            f.write("\n\n")
        f.write(_WINDSURF_RULES_CONTENT)

    return f"  appended policy to {path}"


# ---------------------------------------------------------------------------
# Hooks injection
# ---------------------------------------------------------------------------

def _settings_json_path() -> Path:
    """Return the Claude Code settings.json path."""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _merge_hooks(
    data: dict[str, Any],
    hook_defs: dict[str, list],
    marker: str,
) -> list[str]:
    """Merge hook definitions into settings data, returning names of added events.

    ``marker`` is a substring used to detect whether our hook is already
    installed (e.g. ``"jdocmunch-mcp hook-p"``).

    Each rule is checked individually: if a rule's command already exists
    in the event's hook list, it is skipped.
    """
    hooks = data.setdefault("hooks", {})
    added: list[str] = []

    for event_name, event_hooks in hook_defs.items():
        existing_cmds: set[str] = set()
        if event_name in hooks:
            for rule in hooks[event_name]:
                for h in rule.get("hooks", []):
                    existing_cmds.add(h.get("command", ""))

        new_rules = []
        for rule in event_hooks:
            rule_cmds = [h.get("command", "") for h in rule.get("hooks", [])]
            if any(cmd in existing_cmds for cmd in rule_cmds if cmd):
                continue
            if any(marker in cmd for cmd in existing_cmds):
                if any(marker in cmd for cmd in rule_cmds):
                    continue
            new_rules.append(rule)

        if new_rules:
            if event_name in hooks:
                hooks[event_name].extend(new_rules)
            else:
                hooks[event_name] = new_rules
            added.append(event_name)

    return added


def install_hooks(*, dry_run: bool = False, backup: bool = True) -> str:
    """Merge PreToolUse/PostToolUse/PreCompact/SessionStart hooks into ~/.claude/settings.json.

    Returns a status message.
    """
    path = _settings_json_path()
    data = _read_json(path)
    # Marker matches bare, absolute, and .EXE spellings of our command so a
    # re-init dedups against an existing install instead of appending a copy.
    added = _merge_hooks(data, _enforcement_hooks(), "jdocmunch-mcp")

    if not added:
        return f"  hooks already present in {path}"
    if dry_run:
        return f"  would add {', '.join(added)} hooks to {path}"

    _write_json(path, data, backup=backup)
    return f"  added {', '.join(added)} hooks to {path}"


# ---------------------------------------------------------------------------
# Index current directory
# ---------------------------------------------------------------------------

def run_index(*, dry_run: bool = False, use_embeddings: bool | str = "auto") -> str:
    """Index the current working directory using index_local.

    ``use_embeddings`` is passed through as the embeddings offer resolved it.
    An explicit False matters: after a failed model download fastembed IS
    installed, so "auto" would retry the download inside the index step.
    """
    cwd = os.getcwd()
    if dry_run:
        return f"  would index {cwd}"

    try:
        from ..tools.index_local import index_local
        result = index_local(path=cwd, use_embeddings=use_embeddings)
        files = result.get("file_count", result.get("files_indexed", "?"))
        sections = result.get("section_count", result.get("symbols_indexed", "?"))
        return f"  indexed {cwd} ({files} files, {sections} sections)"
    except Exception as e:
        return f"  indexing failed: {e}"


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _prompt_yn(message: str, default: bool = True) -> bool:
    """Prompt for yes/no, with a default."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        answer = input(message + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes")


def _prompt_choice(message: str, options: list[str], allow_all: bool = True) -> list[str]:
    """Prompt user to pick from numbered options. Returns selected option labels."""
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    extra = "/all/none" if allow_all else "/none"
    try:
        raw = input(f"{message} [1-{len(options)}{extra}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return []
    if raw == "none" or raw == "":
        return []
    if raw == "all":
        return options
    selected = []
    for part in raw.replace(",", " ").split():
        try:
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
        except ValueError:
            continue
    return selected


def _prompt_scope(message: str) -> Optional[str]:
    """Prompt for global/project/skip."""
    try:
        raw = input(f"{message} [global/project/skip]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if raw in ("global", "g"):
        return "global"
    if raw in ("project", "p"):
        return "project"
    return None


# ---------------------------------------------------------------------------
# claude-md subcommand
# ---------------------------------------------------------------------------

def run_claude_md(*, install: Optional[str] = None) -> int:
    """Print or install the Doc Exploration Policy.

    If install is None, print policy to stdout.
    If install is "global" or "project", append to CLAUDE.md.
    Returns exit code.
    """
    if install is None:
        print(_CLAUDE_MD_POLICY)
        return 0

    msg = install_claude_md(install)
    print(msg)
    return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_init(
    *,
    clients: Optional[list[str]] = None,
    claude_md: Optional[str] = None,
    hooks: bool = False,
    index: bool = False,
    dry_run: bool = False,
    demo: bool = False,
    yes: bool = False,
    no_backup: bool = False,
    with_embeddings: bool = False,
) -> int:
    """Run the init flow. Returns exit code (0 = success)."""
    if demo:
        dry_run = True
    backup = not no_backup
    interactive = not yes and sys.stdin.isatty()

    if demo:
        print("\njDocMunch init — DEMO MODE (no changes will be made)\n")
    else:
        print("\njDocMunch init — one-command setup\n")

    _demo_actions: list[tuple[str, str]] = []

    # ----- Step 1: MCP client registration -----
    detected = _detect_clients()

    if clients is not None:
        if "auto" in clients:
            targets = detected
        elif "none" in clients:
            targets = []
        else:
            name_map = {c.name.lower().replace(" ", "-"): c for c in detected}
            targets = [name_map[n] for n in clients if n in name_map]
    elif interactive and detected:
        print("Detected MCP clients:")
        names = [repr(c) for c in detected]
        selected = _prompt_choice("Configure which?", names)
        targets = [c for c in detected if repr(c) in selected]
    elif detected:
        targets = detected
    else:
        targets = []
        print("No MCP clients detected.\n")

    for client in targets:
        msg = configure_client(client, backup=backup, dry_run=dry_run)
        print(f"  {client.name}:{msg}")
        if demo and "would" in msg:
            loc = str(client.config_path) if client.config_path else "via CLI"
            _demo_actions.append((
                f"Register jdocmunch with {client.name} ({loc})",
                "Your AI assistant could immediately call all jDocMunch tools without any manual setup or restart",
            ))

    # ----- Step 2: Agent policies -----
    selected_names = {c.name for c in targets}

    # 2a: CLAUDE.md
    md_scope = claude_md
    if md_scope is None and interactive:
        print()
        md_scope = _prompt_scope("Install CLAUDE.md policy?")
    elif md_scope is None and yes:
        md_scope = "global"

    if md_scope in ("global", "project"):
        msg = install_claude_md(md_scope, dry_run=dry_run, backup=backup)
        print(f"  CLAUDE.md:{msg}")
        if demo and "would" in msg:
            where = "globally (all projects)" if md_scope == "global" else "in this project only"
            _demo_actions.append((
                f"Inject Doc Exploration Policy into CLAUDE.md {where}",
                "Every future Claude session would automatically navigate docs via jDocMunch — no slow, token-heavy file reads",
            ))

    # 2b: Cursor rules
    if "Cursor" in selected_names:
        do_cursor_rules = yes or not interactive
        if interactive:
            print()
            do_cursor_rules = _prompt_yn(
                "Install Cursor rules (.cursor/rules/jdocmunch.mdc)?",
            )
        if do_cursor_rules:
            msg = install_cursor_rules(dry_run=dry_run, backup=backup)
            print(f"  Cursor rules:{msg}")
            if demo and "would" in msg:
                _demo_actions.append((
                    "Write .cursor/rules/jdocmunch.mdc (alwaysApply: true)",
                    "Cursor and its subagents would prefer jDocMunch tools over built-in search on every turn",
                ))

    # 2c: Windsurf rules
    if "Windsurf" in selected_names:
        do_windsurf_rules = yes or not interactive
        if interactive:
            print()
            do_windsurf_rules = _prompt_yn(
                "Install Windsurf rules (.windsurfrules)?",
            )
        if do_windsurf_rules:
            msg = install_windsurf_rules(dry_run=dry_run, backup=backup)
            print(f"  Windsurf rules:{msg}")
            if demo and "would" in msg:
                _demo_actions.append((
                    "Append Doc Exploration Policy to .windsurfrules",
                    "Windsurf Cascade would prefer jDocMunch tools over built-in search on every turn",
                ))

    # ----- Step 3: Enforcement hooks -----
    do_hooks = hooks
    if not do_hooks and interactive:
        print()
        do_hooks = _prompt_yn(
            "Install enforcement hooks (intercept Read on large doc files, auto-reindex after Edit/Write)?",
            default=True,
        )
    elif not do_hooks and yes:
        do_hooks = True
    if do_hooks:
        msg = install_hooks(dry_run=dry_run, backup=backup)
        print(f"  Hooks:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                "Install PreToolUse + PostToolUse + PreCompact + SessionStart hooks in ~/.claude/settings.json",
                "Large doc files would be routed through jDocMunch (search_sections + get_section) "
                "instead of raw Read, and the index would auto-update after every Edit/Write",
            ))

    # ----- Step 4: Index -----
    do_index = index
    if not do_index and interactive:
        print()
        do_index = _prompt_yn(f"Index current directory ({os.getcwd()})?", default=True)
    # ----- Step 4a: offer offline embeddings (default No; --yes never opts in) -----
    use_embeddings: bool | str = "auto"
    if do_index or with_embeddings:
        from .embeddings_offer import offer_embeddings
        use_embeddings = offer_embeddings(
            interactive=interactive,
            with_embeddings=with_embeddings,
            dry_run=dry_run,
            prompt_yn=_prompt_yn,
        )
    if do_index:
        msg = run_index(dry_run=dry_run, use_embeddings=use_embeddings)
        print(f"  Index:{msg}")
        if demo and "would" in msg:
            _demo_actions.append((
                f"Index {os.getcwd()}",
                "Section search and doc navigation would be available immediately — without opening a single file",
            ))

    # ----- Done -----
    print()
    if demo:
        print("Demo complete — no changes were made.\n")
        if _demo_actions:
            print("Had this NOT been a demo, I would have:\n")
            for action, benefit in _demo_actions:
                print(f"  • {action}")
                print(f"    Benefit: {benefit}")
                print()
        else:
            print("(Nothing to do — everything is already configured.)")
        print()
    elif dry_run:
        print("Dry run complete — no changes were made.")
    else:
        print("Done. Restart your MCP client(s) to connect.")
    print()
    return 0
