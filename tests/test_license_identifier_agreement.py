"""Every surface that declares our license must name the same identifier.

The packaging PR switched metadata to a PEP 639 expression so a commercial
user could allowlist the license BY IDENTIFIER. PyPI currently publishes
neither info.license nor info.license_expression. That fixed the surface
they hit and left two others declaring `LicenseRef-Dual-Use` — no product
prefix — so an allowlist keyed on the identifier still needed two entries.
Same defect as the one that was fixed, one surface over.

LICENSE currently has no Version line, so the identifier has no version
suffix. If a Version line is added later, the identifier must pick it up;
the reverse must also fail. That is the jcodemunch-mcp #518 ratchet.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
LICENSE = REPO_ROOT / "LICENSE"

# SPDX 3.0 §10.1: LicenseRef-[idstring], idstring = [A-Za-z0-9.-]+
_LICENSE_REF = re.compile(r"^LicenseRef-[A-Za-z0-9.-]+$")


def _declared_expression() -> str:
    """pyproject.toml is the source; every other surface is checked against it."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^license\s*=\s*"([^"]+)"', text, re.M)
    assert match, (
        "pyproject.toml no longer declares `license` as a bare string. If it "
        "has no license key, PyPI leaves info.license and info.license_expression "
        "empty and the identifier is unallowlistable again."
    )
    return match.group(1)


def test_the_declared_expression_is_a_well_formed_license_ref() -> None:
    expression = _declared_expression()
    assert _LICENSE_REF.match(expression), (
        f"{expression!r} is not a valid SPDX LicenseRef; PyPI rejects a "
        "malformed license expression at upload, i.e. after the wheel is built"
    )


def test_plugin_manifest_names_the_same_identifier() -> None:
    declared = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["license"]
    assert declared == _declared_expression(), (
        f".claude-plugin/plugin.json says {declared!r}; pyproject.toml says "
        f"{_declared_expression()!r}. Two identifiers for one license means an "
        "allowlist needs two entries."
    )


def test_mcpb_manifest_derives_the_identifier_rather_than_copying_it() -> None:
    """`mcpb/manifest.json` is generated, so the check belongs on the generator.

    Asserting the built value (not the source text) is what makes this fail if
    someone reintroduces a literal, whatever they spell it.
    """
    sys.path.insert(0, str(REPO_ROOT / "mcpb"))
    try:
        from build import build_manifest  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert build_manifest()["license"] == _declared_expression()


def test_the_identifier_tracks_the_license_file_version() -> None:
    """LICENSE version and identifier suffix must stay in lockstep.

    jcodemunch-mcp #518 pins a suffix so 1.2 cannot ship under 1.1's identifier.
    These LICENSE files currently have no Version line, so the identifier must
    not invent one. Adding Version X.Y without a matching suffix (or the reverse)
    fails here.
    """
    expression = _declared_expression()
    suffix = re.search(r"-(\d+\.\d+)$", expression)
    header = LICENSE.read_text(encoding="utf-8")[:400]
    in_file = re.search(r"^Version\s+(\d+\.\d+)", header, re.M)
    if in_file:
        assert suffix, (
            f"{expression!r} carries no version suffix but {LICENSE.name} "
            f"says Version {in_file.group(1)}"
        )
        assert suffix.group(1) == in_file.group(1), (
            f"the identifier claims license version {suffix.group(1)}; "
            f"{LICENSE.name} says {in_file.group(1)}"
        )
    else:
        assert not suffix, (
            f"{expression!r} carries version suffix {suffix.group(1)} but "
            f"{LICENSE.name} has no Version line"
        )


def test_no_license_classifier_survives_beside_the_expression() -> None:
    """PEP 639: a `License ::` classifier alongside an expression is rejected.

    The build succeeds and the UPLOAD fails, which is the expensive ordering.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    offenders = re.findall(r'^\s*"(License :: [^"]+)"', text, re.M)
    assert not offenders, f"remove the classifier(s) {offenders}; the expression supersedes them"
