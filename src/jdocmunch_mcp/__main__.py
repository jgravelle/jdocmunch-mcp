"""Enable `python -m jdocmunch_mcp ...` invocation.

The watch login service (service_installer._exec_cmd) launches the daemon via
`sys.executable -m jdocmunch_mcp watch`, so this entry point must exist.

⚠ It dispatches through `cli.main:main`, exactly like the console script, and
NOT through `.server`. Importing `.server` loads the MCP SDK, which only the
`serve` branch needs; `cli.main` imports `..server` inside that branch alone.
`tests/test_main_module_entry.py` pins that a `-m` hook invocation loads no SDK.
"""
from .cli.main import main

if __name__ == "__main__":
    main()
