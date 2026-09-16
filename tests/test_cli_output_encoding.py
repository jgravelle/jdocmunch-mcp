"""CLI output is UTF-8 even when piped.

Suite parity with jcodemunch-mcp v1.108.262, where this was a live crash.

On Windows `sys.stdout` is the CONSOLE stream (already UTF-8) when attached to a
terminal and the LOCALE stream (cp1252) when piped or redirected. So output
containing any non-ASCII character works interactively and goes out as cp1252
bytes the moment anything consumes it.

⚠ This repo had the SILENT half, not the crash. `cli/init.py` prints `—` and `•`,
both of which cp1252 CAN encode, so nothing raised -- a UTF-8 consumer simply got
mojibake. jcm had a character cp1252 cannot encode at all (U+2212) and died
outright. Same defect, different symptom, and the quiet one is the harder to
notice. Fixed at the entry point so the next character added does not decide
which symptom this repo gets.

⚠ These run the CLI in a SUBPROCESS with a pipe, because that is the only
configuration that reproduces it. In-process, pytest's capture layer accepts any
string and the test cannot fail.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(args, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env.pop("PYTHONIOENCODING", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "jdocmunch_mcp.server", *args],
        capture_output=True, env=env, timeout=300,
    )


class TestOutputIsUtf8:
    @pytest.mark.parametrize("args", [["--help"], ["--version"]])
    def test_output_decodes_as_utf8(self, args):
        out = _run(args)
        out.stdout.decode("utf-8")  # raises if we emitted cp1252
        assert b"UnicodeEncodeError" not in out.stderr

    def test_no_encode_error_on_any_command(self):
        out = _run(["--help"])
        assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-1500:]


class TestTheHelperItself:
    def test_it_does_not_raise_without_reconfigure(self, monkeypatch):
        """Captured buffers replace sys.stdout with objects that have no
        reconfigure(). The CLI must still start."""
        import io
        from jdocmunch_mcp import server as S
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        S._force_utf8_stdio()

    def test_a_refusing_stream_is_survivable(self, monkeypatch):
        from jdocmunch_mcp import server as S

        class Stubborn:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                raise OSError("no")

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Stubborn())
        monkeypatch.setattr(sys, "stderr", Stubborn())
        S._force_utf8_stdio()

    def test_an_already_utf8_stream_is_left_alone(self, monkeypatch):
        from jdocmunch_mcp import server as S
        calls = []

        class Fine:
            encoding = "UTF-8"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Fine())
        monkeypatch.setattr(sys, "stderr", Fine())
        S._force_utf8_stdio()
        assert calls == []

    def test_a_cp1252_stream_is_reconfigured_with_replace(self, monkeypatch):
        from jdocmunch_mcp import server as S
        calls = []

        class Locale:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.setattr(sys, "stdout", Locale())
        monkeypatch.setattr(sys, "stderr", Locale())
        S._force_utf8_stdio()
        assert len(calls) == 2
        assert all(c["encoding"] == "utf-8" and c["errors"] == "replace" for c in calls)

    def test_pythonioencoding_is_honoured(self, monkeypatch):
        from jdocmunch_mcp import server as S
        calls = []

        class Locale:
            encoding = "cp1252"

            def reconfigure(self, **kw):
                calls.append(kw)

        monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
        monkeypatch.setattr(sys, "stdout", Locale())
        S._force_utf8_stdio()
        assert calls == [], "an operator who named an encoding made a decision"

    def test_main_calls_it_first(self):
        """Nothing that can emit output may run before the reconfigure, on ANY
        path out of ``main``.

        ⚠ This used to assert a CHARACTER OFFSET -- the call had to appear in
        the first 200 chars of the function body -- which is a proxy for the
        rule, not the rule. A comment block explaining a new dispatch branch
        broke it while the branch itself called the helper on its very first
        line. Offsets measure prose; this walks the AST and measures
        statements, so it can only fail when the ordering is actually wrong.
        """
        import ast
        import inspect
        from jdocmunch_mcp import server as S

        fn = ast.parse(inspect.getsource(S.main)).body[0]
        body = fn.body
        if (isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)):
            body = body[1:]                                    # docstring

        def _is_guard(stmt) -> bool:
            return (isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", None) == "_force_utf8_stdio")

        for stmt in body:
            if _is_guard(stmt):
                break
            # An assignment cannot print and cannot import; argv normalisation
            # ahead of the dispatch decision is fine.
            if isinstance(stmt, ast.Assign):
                continue
            # An early-dispatch branch is fine ONLY if it reconfigures first.
            assert isinstance(stmt, ast.If) and stmt.body and _is_guard(stmt.body[0]), (
                f"main() runs {ast.dump(stmt)[:80]}... before _force_utf8_stdio(). "
                f"Every path out of main must reconfigure stdio before it can "
                f"emit anything."
            )
        else:
            raise AssertionError("main() never calls _force_utf8_stdio()")


class TestTheMcpTransportIsNotAffected:
    def test_stdio_transport_uses_the_binary_layer(self):
        """The reason this is safe to apply unconditionally in main()."""
        import inspect
        import mcp.server.stdio as stdio_mod
        assert "sys.stdout.buffer" in inspect.getsource(stdio_mod), (
            "the MCP stdio transport no longer wraps the binary layer; re-check "
            "whether _force_utf8_stdio can still run for `serve`"
        )
