"""Command-line entry point for jdocmunch-mcp.

⚠⚠ MODULE-LEVEL IMPORTS HERE ARE STDLIB ONLY, and that is the whole point of
this module existing. ``pyproject``'s console script points at ``main`` below,
so EVERY CLI and hook invocation pays whatever this module's import graph costs
before argparse has even looked at the subcommand. It used to point at
``server:main``, which pulled the MCP SDK in on a path that never touches it.

Everything heavy is imported INSIDE the branch that needs it. ``run_server`` in
particular is imported only by the ``serve`` branch: that single line is the
gate between "this process speaks MCP" and "this process re-indexed a file".

A ratchet in tests/test_cli_import_cost.py asserts ``mcp`` is absent from
``sys.modules`` after a hook invocation, so a stray top-level import here fails
the suite rather than quietly costing every hook fire a second.
"""

import argparse
import json
import os
import sys
from typing import Optional

# ⚠ ``pathlib`` is deliberately NOT here either. Exactly one function below
# wants it -- ``_load_paths_from_arg``,
# reachable only from ``index-local --paths-from``. Leaving it at module scope
# put it on every ``Read``, because the PreToolUse hook imports this module.

# ⚠ ``asyncio`` is deliberately NOT here: only the ``serve`` and ``watch``
# branches ever use it. It is imported inside those two.


class _LazyVersionAction(argparse.Action):
    """``--version`` that resolves the version only when asked for.

    argparse's built-in ``version`` action interpolates its string at
    ``add_argument`` time, so the old ``version=f"%(prog)s {_ver}"`` resolved
    ``__version__`` on EVERY invocation -- which defeats the lazy
    ``importlib.metadata`` lookup in the package ``__init__`` entirely. Reading
    it inside ``__call__`` is what makes that laziness reach the CLI path.

    ⚠ That sentence described an intent, not the code: until the PEP 562
    ``__getattr__`` landed in ``jdocmunch_mcp/__init__.py`` the package resolved
    the version in its module body, so this Action was deferring an f-string in
    front of an eager lookup that every import already paid for. Both halves
    are lazy now.
    """

    def __init__(self, option_strings, dest, **kwargs):
        kwargs.setdefault("nargs", 0)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from .. import __version__
        # stdout + exit 0, matching argparse's built-in version action.
        # parser.exit(message=...) would write to STDERR, which silently
        # breaks `jdocmunch-mcp --version | ...` for anyone piping it.
        print(f"{parser.prog} {__version__}")
        parser.exit()


def _load_paths_from_arg(paths_from: str) -> tuple[Optional[list], Optional[str]]:
    """Read explicit paths from a file or stdin for the `index-local --paths-from` CLI flag.

    Returns ``(paths, None)`` on success or ``(None, error_message)`` on failure.
    Filters out empty lines and ``# ...`` comments. An empty list is treated as
    an error so the caller doesn't silently fall through to a full-tree index.
    """
    from pathlib import Path
    try:
        if paths_from == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(paths_from).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"Cannot read --paths-from {paths_from!r}: {e}"
    paths_arg = [
        ln.strip()
        for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not paths_arg:
        return None, f"--paths-from {paths_from!r} contained no usable paths"
    return paths_arg, None


def _force_utf8_stdio() -> None:
    """Make CLI output UTF-8 regardless of the platform locale.

    Suite parity with jcodemunch-mcp v1.108.262, where this was a live crash.

    ⚠⚠ On Windows, `sys.stdout` is the **console** stream (already UTF-8) when
    attached to a terminal and the **locale** stream (cp1252) when piped or
    redirected. So output containing any non-ASCII character works interactively
    and goes out as cp1252 bytes the moment anything consumes it -- mojibake for
    a UTF-8 reader, with nothing raised on our side. A character cp1252 cannot
    encode at all crashes the command outright.

    jdoc's `init` prints `—` and `•` today. Both ARE cp1252-encodable, so this
    repo has the silent half and not (yet) the crash; the fix is at the entry
    point precisely so the next character added cannot decide that for us.

    ⚠ The MCP stdio transport is unaffected: it wraps `sys.stdout.buffer` in its
    own TextIOWrapper and never reads the text layer reconfigured here.

    ⚠ `errors="replace"` is deliberate -- filesystem paths can carry surrogates
    from a `surrogateescape` decode, which raise even under UTF-8. Mangling one
    display character beats killing the command.

    ⚠ `PYTHONIOENCODING` is honoured as an explicit opt-out.
    """
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # replaced by a test harness or captured buffer
            continue
        current = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if current in ("utf8", "utf8mb4"):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: Optional[list] = None):
    """Main entry point."""
    # ⚠⚠ THE PreToolUse FAST PATH. Claude Code fires `hook-pretooluse` as a
    # fresh process on EVERY `Read`, and that process does very little work: a
    # JSON parse, an extension test, a size test, and at most one JSON line on
    # stdout. Everything else it pays is this function's preamble:
    #
    #     verify_package_integrity()   packages_distributions() reads metadata
    #                                  for every installed distribution, and is
    #                                  NOT cached
    #     the argparse tree            built for a subcommand it then ignores
    #
    # ⚠ The argparse tree is the SMALL half, which is the opposite of what this
    # was expected to show. It is worth skipping, but on its own it would not
    # have justified a second dispatch path.
    #
    # ⚠⚠ THE INTEGRITY CHECK IS DELIBERATELY NOT RUN HERE, and that is the one
    # behavioural difference on this path. Its only output is a stderr print,
    # and Claude Code's hook contract sends stderr from a hook that exits 0 to
    # the debug log -- "never the transcript, and Claude never sees it" (the
    # same contract quoted in cli/_hook_probe.py, where it already cost this
    # repo a hint nobody received). A supply-chain warning emitted into a debug
    # log once per Read is not a control; the check still runs on every other
    # subcommand. `hook-posttooluse` deliberately keeps it -- see the note on
    # its branch below.
    #
    # ⚠ The guard is an EXACT one-argument match, so `hook-pretooluse --help`,
    # a typo'd extra argument and every other subcommand all fall through to
    # argparse and behave exactly as before.
    #
    # ⚠ The import below is the SAME line the slow path uses, and that is
    # load-bearing: `cli.hooks` re-exports `run_pretooluse` from the
    # stdlib-only `cli._hook_probe` leaf, so this resolves the attribute on
    # `cli.hooks` at call time exactly as the argparse branch does. Tests that
    # patch `jdocmunch_mcp.cli.hooks.run_pretooluse` keep working, and there is
    # still exactly one definition of the function.
    _argv = sys.argv[1:] if argv is None else argv
    if len(_argv) == 1 and _argv[0] == "hook-pretooluse":
        _force_utf8_stdio()
        from ..cli.hooks import run_pretooluse
        sys.exit(run_pretooluse())

    _force_utf8_stdio()
    from ..security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jdocmunch-mcp",
        description="jDocMunch MCP — structured documentation retrieval server.",
    )
    parser.add_argument(
        "-V", "--version", action=_LazyVersionAction,
        help="show program's version number and exit",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- serve (default) ---
    subparsers.add_parser("serve", help="Run the MCP server (default)")

    # --- init ---
    init_parser = subparsers.add_parser(
        "init",
        help="One-command onboarding: detect clients, write config, install policy, hooks, index",
    )
    init_parser.add_argument(
        "--hooks", action="store_true",
        help="Install enforcement hooks into ~/.claude/settings.json",
    )
    init_parser.add_argument(
        "--index", action="store_true",
        help="Index the current working directory",
    )
    init_parser.add_argument(
        "--client", dest="clients", action="append",
        help="MCP client to configure (auto|none|claude-code|claude-desktop|cursor|windsurf|continue)",
    )
    init_parser.add_argument(
        "--claude-md", dest="claude_md", choices=["global", "project"],
        help="Install Doc Exploration Policy into CLAUDE.md",
    )
    init_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes",
    )
    init_parser.add_argument(
        "--demo", action="store_true",
        help="Demo mode: dry-run with benefit summary",
    )
    init_parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Accept all defaults non-interactively",
    )
    init_parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip creating .bak backups before modifying files",
    )
    init_parser.add_argument(
        "--with-embeddings", dest="with_embeddings", action="store_true",
        help="Install the offline embedding provider (pip install fastembed, about 160 MB) "
             "and download its model (87 MB, huggingface.co) so search is semantic. "
             "Never implied by --yes.",
    )

    # --- claude-md ---
    cmd_parser = subparsers.add_parser(
        "claude-md",
        help="Print or install the Doc Exploration Policy for CLAUDE.md",
    )
    cmd_parser.add_argument(
        "--install", choices=["global", "project"],
        help="Append policy to CLAUDE.md (global or project scope)",
    )

    # --- index-file ---
    if_parser = subparsers.add_parser(
        "index-file",
        help="Re-index a single file within an existing index",
    )
    if_parser.add_argument(
        "file", help="Path to the file to re-index",
    )
    if_parser.add_argument(
        "--name",
        help=(
            "Index name (owner/name or bare name) to update directly, skipping "
            "folder-name detection. Required for a custom --name index or a "
            "corpus root whose folder name is not path-safe (e.g. has spaces)."
        ),
    )

    # --- index-local ---
    il_parser = subparsers.add_parser(
        "index-local",
        help="Index a local folder (CLI equivalent of the MCP index_local tool)",
    )
    il_parser.add_argument(
        "--path", required=True,
        help="Path to the folder to index",
    )
    il_parser.add_argument(
        "--name",
        help="Optional repo identifier override",
    )
    il_parser.add_argument(
        "--paths-from",
        metavar="FILE",
        help=(
            "Read explicit paths to index (one per line) from FILE. Use '-' for "
            "stdin. When set, the directory walk is skipped — only the listed "
            "paths are indexed. Entries may be absolute or relative to --path. "
            "Pipe-friendly with find / fd / fzf / rg."
        ),
    )
    il_parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Rebuild from scratch instead of diffing against the existing index "
            "(equivalent to the MCP tool's incremental=false). Re-parses and "
            "re-embeds every file. Use after changing the embedding model."
        ),
    )
    # jdoc#108: the MCP tool could express both of these per call and the CLI
    # could express neither, so the documented CLI route sent a private corpus
    # to whatever summarizer the environment happened to expose, with nothing
    # in --help saying it would. Same flag spelling as `watch`.
    il_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Skip AI section summaries (no summarizer calls, nothing leaves the machine)",
    )
    # jdoc#116: the third and last member of the #108 set. Worse than the two
    # fixed there, because the CLI did not merely fail to EXPRESS the setting,
    # it DESTROYED one already persisted: passing nothing computed a `full`
    # selection that overwrote the stored `full+shape:<hash>` and re-admitted
    # every excluded file.
    il_parser.add_argument(
        "--extra-ignore-pattern", action="append", default=None,
        metavar="PATTERN", dest="extra_ignore_pattern",
        help=(
            "Gitignore-style pattern to exclude from the corpus; repeatable. "
            "Patterns are stored with the index, so a later refresh that omits "
            "this flag INHERITS them rather than widening the corpus. Pass "
            "--no-extra-ignore-patterns to deliberately clear them."
        ),
    )
    il_parser.add_argument(
        "--no-extra-ignore-patterns", action="store_true",
        help=(
            "Clear any stored exclusion patterns and index the full corpus. "
            "Explicit, because omitting --extra-ignore-pattern now means "
            "'inherit', not 'none'."
        ),
    )
    il_parser.add_argument(
        "--embeddings", choices=("auto", "on", "off"), default="auto",
        help=(
            "Semantic embeddings: 'auto' (default) enables them when a provider "
            "is configured, 'on' requests them even where auto-detect would "
            "decline, 'off' keeps the index lexical-only"
        ),
    )
    il_parser.add_argument(
        "--no-embeddings", action="store_true",
        help="Alias for --embeddings off",
    )

    # --- verify-index (v1.27.0) ---
    vi_parser = subparsers.add_parser(
        "verify-index",
        help="Byte-offset integrity check across an indexed repo",
    )
    vi_parser.add_argument("--repo", required=True, help="jdocmunch repo identifier")
    vi_parser.add_argument("--sample", type=int, default=None,
                           help="Verify only the first N sections (cheap CI mode)")

    # --- delete-index ---
    di_parser = subparsers.add_parser(
        "delete-index",
        help="Delete a repo index and its cached raw files (offline)",
    )
    di_parser.add_argument("--repo", required=True, help="jdocmunch repo identifier")

    # --- hook-pretooluse ---
    subparsers.add_parser(
        "hook-pretooluse",
        help="PreToolUse hook: intercept Read on large doc files (reads stdin)",
    )

    # --- hook-posttooluse ---
    subparsers.add_parser(
        "hook-posttooluse",
        help="PostToolUse hook: auto-reindex doc files after Edit/Write (reads stdin)",
    )

    # --- hook-precompact ---
    subparsers.add_parser(
        "hook-precompact",
        help="PreCompact hook: no-op kept for installed settings.json entries (#131; reads stdin)",
    )

    # --- hook-sessionstart (#131) ---
    subparsers.add_parser(
        "hook-sessionstart",
        help="SessionStart hook: restore the doc session snapshot after compact/resume/fork (reads stdin)",
    )

    # --- hook-reindex ---
    hook_reindex_parser = subparsers.add_parser(
        "hook-reindex",
        help="Throttled background reindex worker spawned by the PostToolUse hook (jdoc#76)",
    )
    hook_reindex_parser.add_argument("file", help="Path to the edited doc file to reindex")

    # --- watch (jdoc#78) ---
    watch_parser = subparsers.add_parser(
        "watch",
        help="Auto-reindex every locally-indexed doc repo on any on-disk doc change (foreground daemon)",
    )
    watch_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Skip AI section summaries on re-index (faster, no summarizer calls)",
    )
    watch_parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-change stderr log lines",
    )

    # jdoc#120: the installed service wraps this same daemon, so it takes the
    # same flags. Same spellings as `watch` — one source of truth for what the
    # watcher does, whether it runs in the foreground or at login.
    watch_install_parser = subparsers.add_parser(
        "watch-install",
        help="Install the doc watcher as a login service (systemd/launchd/Task Scheduler)",
    )
    watch_install_parser.add_argument(
        "--no-ai-summaries", action="store_true",
        help="Install the watcher with AI section summaries off (no summarizer calls)",
    )
    watch_install_parser.add_argument(
        "--quiet", action="store_true",
        help="Install the watcher with per-change log lines suppressed",
    )
    subparsers.add_parser(
        "watch-uninstall",
        help="Remove the installed doc-watcher login service",
    )
    subparsers.add_parser(
        "watch-status",
        help="Print doc-watcher service state + per-repo watch coverage",
    )

    args = parser.parse_args(argv)

    # Default to serve when no subcommand given
    if args.command is None or args.command == "serve":
        # ⚠⚠ THE GATE. This import pulls server.py and with it the MCP SDK.
        # It must stay INSIDE this branch: every other subcommand, and every hook fire,
        # returns without ever touching it.
        import asyncio

        from ..server import run_server
        asyncio.run(run_server())
        return

    if args.command == "init":
        from ..cli.init import run_init
        rc = run_init(
            clients=args.clients,
            claude_md=args.claude_md,
            hooks=args.hooks,
            index=args.index,
            dry_run=args.dry_run,
            demo=args.demo,
            yes=args.yes,
            no_backup=args.no_backup,
            with_embeddings=args.with_embeddings,
        )
        sys.exit(rc)

    if args.command == "claude-md":
        from ..cli.init import run_claude_md
        sys.exit(run_claude_md(install=args.install))

    if args.command == "index-file":
        import contextlib
        from ..tools.index_file import index_file_cli
        # Reserve stdout for the final JSON body only: embedding-provider
        # initialization (e.g. sentence-transformers lazy model load) can write
        # progress/warning chatter to stdout during computation, which would
        # corrupt the machine-readable result. Redirect it to stderr while we
        # compute, then print JSON after leaving the redirect (jdoc#65, same
        # guard the serve path uses for jdoc#19).
        with contextlib.redirect_stdout(sys.stderr):
            result = index_file_cli(args.file, name=getattr(args, "name", None))
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)
        return

    if args.command == "index-local":
        import contextlib
        from ..tools.index_local import index_local
        paths_from = getattr(args, "paths_from", None)
        if paths_from:
            paths_arg, err = _load_paths_from_arg(paths_from)
            if err is not None:
                print(json.dumps({"success": False, "error": err}, indent=2))
                sys.exit(1)
        else:
            paths_arg = None
        # Guard stdout against provider/library chatter during computation; the
        # final JSON is the only thing that should reach stdout (jdoc#65).
        with contextlib.redirect_stdout(sys.stderr):
            # jdoc#108: "off" beats "auto" whichever spelling asked for it —
            # a caller that named a value never has it widened by a default.
            _emb = getattr(args, "embeddings", "auto")
            if getattr(args, "no_embeddings", False):
                _emb = "off"
            _use_embeddings = {"auto": "auto", "on": True, "off": False}[_emb]
            result = index_local(
                path=args.path, name=args.name, paths=paths_arg,
                # jdoc#109: without this the CLI had no way to force a full
                # re-embed short of delete-index, which is the workaround the
                # reporter had to use.
                incremental=not getattr(args, "rebuild", False),
                use_ai_summaries=not getattr(args, "no_ai_summaries", False),
                use_embeddings=_use_embeddings,
                # jdoc#116: None and [] mean different things downstream.
                # None = "said nothing" = inherit the stored patterns;
                # [] = "explicitly none" = widen, with the change disclosed.
                extra_ignore_patterns=(
                    [] if getattr(args, "no_extra_ignore_patterns", False)
                    else getattr(args, "extra_ignore_pattern", None)
                ),
            )
        print(json.dumps(result, indent=2))
        return

    if args.command == "verify-index":
        from ..tools.verify_index import verify_index as _verify
        result = _verify(repo=args.repo, sample=args.sample)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("drift_count", 0) == 0 else 2)

    if args.command == "delete-index":
        from ..tools.delete_index import delete_index as _delete
        result = _delete(repo=args.repo)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("success") else 1)

    if args.command == "hook-pretooluse":
        from ..cli.hooks import run_pretooluse
        sys.exit(run_pretooluse())

    if args.command == "hook-posttooluse":
        # ⚠⚠ NO FAST PATH HERE, ON PURPOSE, and the reason is not that it would
        # not work. It fires on Edit/Write rather than Read -- one to two orders
        # of magnitude rarer in a session -- and unlike the Read hook it does
        # real work: a debounce stamp, a cross-process reindex mark
        # (`reindex_state`), and a spawned `hook-reindex` worker that itself
        # pays a full interpreter start and then indexes a file. The startup
        # is a small part of what it costs, so skipping it buys little there,
        # where on the Read path it is nearly all of the cost.
        #
        # It also needs `hashlib`, `subprocess` and `pathlib` regardless, so the
        # leaf-module saving that makes the Read path cheap does not apply.
        #
        # ⚠ And the decisive half: this is the hook that EXECUTES something. If
        # exactly one hook path keeps the supply-chain integrity check, it is
        # the one that spawns a child process, not the one that reads a stat.
        from ..cli.hooks import run_posttooluse
        sys.exit(run_posttooluse())

    if args.command == "hook-precompact":
        from ..cli.hooks import run_precompact
        sys.exit(run_precompact())

    if args.command == "hook-sessionstart":
        from ..cli.hooks import run_sessionstart
        sys.exit(run_sessionstart())

    if args.command == "hook-reindex":
        from ..cli.hooks import run_hook_reindex
        sys.exit(run_hook_reindex(args.file))

    if args.command == "watch":
        from ..watch import watch_docs
        try:
            import asyncio
            asyncio.run(watch_docs(
                use_ai_summaries=not args.no_ai_summaries,
                quiet=args.quiet,
            ))
        except KeyboardInterrupt:
            pass
        return

    if args.command in ("watch-install", "watch-uninstall", "watch-status"):
        from .. import service_installer
        if args.command == "watch-install":
            watch_args = []
            if getattr(args, "no_ai_summaries", False):
                watch_args.append("--no-ai-summaries")
            if getattr(args, "quiet", False):
                watch_args.append("--quiet")
            try:
                result = service_installer.install_service(watch_args)
                print(json.dumps(result, indent=2))
                # jdoc#120: the install always rewrites the service definition.
                # Say so when that discarded a hand-edited one, so the revert is
                # visible at the moment it happens rather than at the next
                # unexplained re-index.
                replaced = result.get("replaced_exec")
                if replaced:
                    print(
                        "\nWARNING: this replaced a customised service definition.\n"
                        f"  was:  {replaced['previous']}\n"
                        f"  now:  {replaced['installed']}\n"
                        "Pass the watcher's flags to `watch-install` itself "
                        "(--no-ai-summaries, --quiet) so the next install keeps them.",
                        file=sys.stderr,
                    )
                print(
                    "\nInstalled. The doc watcher runs in the background and keeps "
                    "every locally-indexed doc repo fresh. Remove it with "
                    "`jdocmunch-mcp watch-uninstall`.",
                    file=sys.stderr,
                )
            except service_installer.InstallerError as exc:
                print(json.dumps({"error": str(exc)}, indent=2))
                sys.exit(1)
        elif args.command == "watch-uninstall":
            result = service_installer.uninstall_service()
            print(json.dumps(result, indent=2))
        else:  # watch-status
            from ..tools.get_watch_status import get_watch_status
            print(json.dumps(get_watch_status(
                storage_path=os.environ.get("DOC_INDEX_PATH"),
            ), indent=2))
        return
