"""Security utilities for path validation, secret detection, and binary filtering."""

import os
from pathlib import Path
from typing import Optional


# --- Package Integrity Check ---

def verify_package_integrity() -> None:
    """Warn at startup if this code is running from an unofficial distribution.

    Detects supply-chain attacks where the package is re-published under a
    different name (e.g. jdocmunch-mcp-fork instead of jdocmunch-mcp).
    Uses packages_distributions() to find which distribution actually owns
    the running code — catches renamed forks that install under a different name.
    """
    import sys

    expected_dist = "jdocmunch-mcp"
    canonical_url = "https://github.com/jgravelle/jdocmunch-mcp"

    try:
        from importlib.metadata import packages_distributions

        distributions = packages_distributions().get("jdocmunch_mcp", [])
        if not distributions:
            # Running from source / editable install without dist metadata — skip.
            return

        actual_dist = distributions[0]
        if actual_dist != expected_dist:
            print(
                f"\nSECURITY WARNING: jdocmunch_mcp is running from distribution "
                f"'{actual_dist}' instead of the official '{expected_dist}'.\n"
                f"This may indicate a supply-chain attack or unofficial fork.\n"
                f"Install only from PyPI: pip install {expected_dist}\n"
                f"Official source: {canonical_url}\n",
                file=sys.stderr,
            )
    except Exception:
        pass  # Never block startup due to integrity check errors


# --- Path Traversal & Symlink Protection ---

def validate_path(root: Path, target: Path) -> bool:
    """Check that target path resolves within root directory."""
    try:
        resolved = target.resolve()
        resolved_root = root.resolve()
        return os.path.commonpath([resolved_root, resolved]) == str(resolved_root)
    except (OSError, ValueError):
        return False


def is_symlink_escape(root: Path, path: Path) -> bool:
    """Check if a symlink points outside the root directory."""
    try:
        if path.is_symlink():
            resolved = path.resolve()
            resolved_root = root.resolve()
            return os.path.commonpath([resolved_root, resolved]) != str(resolved_root)
    except (OSError, ValueError):
        return True
    return False


# --- Secret File Detection ---

SECRET_PATTERNS = [
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.credentials",
    "*.keystore",
    "*.jks",
    "*.token",
    "*secret*",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_dsa",
    "id_ecdsa",
    ".htpasswd",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "service-account*.json",
    "*.secrets",
]


def is_secret_file(file_path: str) -> bool:
    """Check if a file path matches known secret file patterns."""
    import fnmatch

    name = os.path.basename(file_path).lower()
    path_lower = file_path.lower()

    for pattern in SECRET_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(path_lower, pattern):
            return True
    return False


# --- Binary File Detection ---

# Doc extensions are NOT binary — .pdf/.doc/.docx reserved for Phase 2
BINARY_EXTENSIONS = frozenset([
    # Executables
    ".exe", ".dll", ".so", ".dylib", ".bin", ".out",
    # Object files
    ".o", ".obj", ".a", ".lib",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".webp", ".tiff", ".tif",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".ogg", ".webm",
    # Compiled / bytecode
    ".pyc", ".pyo", ".class", ".wasm",
    # Database
    ".db", ".sqlite", ".sqlite3",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Other
    ".jar", ".war", ".ear",
    ".min.js.map", ".min.css.map",
])


def is_binary_extension(file_path: str) -> bool:
    """Check if a file has a known binary extension."""
    _, ext = os.path.splitext(file_path)
    return ext.lower() in BINARY_EXTENSIONS


def is_binary_content(data: bytes, check_size: int = 8192) -> bool:
    """Detect binary content by checking for null bytes."""
    sample = data[:check_size]
    return b"\x00" in sample


def is_binary_file(file_path: Path, check_size: int = 8192) -> bool:
    """Check if a file is binary using extension check + content sniffing."""
    if is_binary_extension(str(file_path)):
        return True

    try:
        with open(file_path, "rb") as f:
            data = f.read(check_size)
        return is_binary_content(data, check_size)
    except OSError:
        return True


# --- Encoding Safety ---

def safe_decode(data: bytes, encoding: str = "utf-8") -> str:
    """Decode bytes to string with replacement for invalid sequences."""
    return data.decode(encoding, errors="replace")


# --- Composite Filters ---

# jdoc#130: 5 MB, raised from 500 KB, and overridable.
#
# ⚠⚠ 500 KB is a sane ceiling for a SOURCE file and the wrong one for a
# DOCUMENT. It silently dropped jcodemunch-mcp's 1.25 MB `CHANGELOG.md` --
# 1,515 heading-delimited sections at a 565-byte median, by a wide margin the
# highest-leverage retrieval target in that repository and the only file
# excluded from it. A changelog, a spec, or a generated API reference
# legitimately runs to megabytes.
#
# ⚠ The number is MEASURED, not guessed: that real 1.25 MB file parses in
# 1.03 s with an 8.3 MB peak, so the parser is not the constraint. The same
# walk already grants OFFICE_MAX_FILE_SIZE = 25 MB to `.pdf`/`.docx`, so the
# pre-jdoc#130 rule accepted a 25 MB PowerPoint and refused a 600 KB Markdown
# file -- the asymmetry, not the absolute value, is what makes 500 KB
# indefensible here.
#
# ⚠ Raising a cap WIDENS coverage, so it ships with the disclosure half
# (`skip_counts` in the index response). A cap without a route out was jcm's
# reported defect too (`JCODEMUNCH_MAX_FILE_SIZE`, v1.108.193).
_DEFAULT_MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_FILE_SIZE_ENV = "JDOCMUNCH_MAX_FILE_SIZE"


def resolve_max_file_size(env: Optional[dict] = None) -> int:
    """Per-file size ceiling for text documents, in bytes.

    ``JDOCMUNCH_MAX_FILE_SIZE`` overrides it. ⚠ Fails OPEN on anything
    unparseable or non-positive: a typo in an env var must not silently shrink
    a corpus, which is the failure mode this whole change exists to remove.
    """
    raw = (env if env is not None else os.environ).get(MAX_FILE_SIZE_ENV, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_FILE_SIZE_BYTES
    if value <= 0:
        return _DEFAULT_MAX_FILE_SIZE_BYTES
    return value


DEFAULT_MAX_FILE_SIZE = _DEFAULT_MAX_FILE_SIZE_BYTES


def should_exclude_file(
    file_path: Path,
    root: Path,
    max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    check_secrets: bool = True,
    check_binary: bool = True,
    check_symlinks: bool = True,
) -> Optional[str]:
    """Run all security checks on a file. Returns reason string if excluded, None if ok."""
    if check_symlinks and is_symlink_escape(root, file_path):
        return "symlink_escape"

    if not validate_path(root, file_path):
        return "path_traversal"

    try:
        rel_path = file_path.relative_to(root).as_posix()
    except ValueError:
        return "outside_root"

    if check_secrets and is_secret_file(rel_path):
        return "secret_file"

    try:
        size = file_path.stat().st_size
        if size > max_file_size:
            return "file_too_large"
    except OSError:
        return "unreadable"

    if check_binary and is_binary_extension(rel_path):
        return "binary_extension"

    return None
