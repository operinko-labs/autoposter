"""Every third-party module `src/` imports must be a declared dependency.

This is not pedantry. Pillow was imported at module scope by the render
pipeline for two phases while never appearing in `pyproject.toml`. Every
test passed and every local run worked, because developers had it installed
for other reasons -- but the built image installs only what is declared, so
the container died on `ModuleNotFoundError: No module named 'PIL'` before it
could serve a request. The suite could not catch it; only building the image
could, and nothing built the image.

Walking the AST catches it in under a second instead.
"""
import ast
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "autoposter"

# Import name on the left, distribution name on the right, for the handful
# where they differ.
IMPORT_TO_DISTRIBUTION = {
    "PIL": "pillow",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "attr": "attrs",
}


def _normalise(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _declared() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    names = set()
    for spec in data["project"]["dependencies"]:
        # "sqlalchemy[asyncio]>=2.0.36" -> "sqlalchemy"
        names.add(_normalise(re.split(r"[<>=\[!;~\s]", spec)[0]))
    return names


def _imported() -> dict[str, set[str]]:
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split(".")[0]]
            else:
                continue
            for module in modules:
                if module in stdlib or module == "autoposter":
                    continue
                found.setdefault(module, set()).add(
                    str(path.relative_to(REPO)).replace("\\", "/")
                )
    return found


def test_every_imported_package_is_declared():
    declared = _declared()
    undeclared = {}
    for module, users in _imported().items():
        distribution = _normalise(IMPORT_TO_DISTRIBUTION.get(module, module))
        if distribution not in declared:
            undeclared[module] = sorted(users)

    assert not undeclared, (
        "these modules are imported by src/ but are not in pyproject.toml's "
        "dependencies, so the built image will not install them:\n"
        + "\n".join("  %s -> imported by %s" % (m, ", ".join(u)) for m, u in sorted(undeclared.items()))
    )


def test_pillow_specifically_is_declared():
    """A named regression: its absence stopped the container starting."""
    assert "pillow" in _declared()


def test_python_multipart_specifically_is_declared():
    """The same class of absence as Pillow's, one layer further out.

    Nothing in ``src/`` imports it, so the AST walk above cannot see it: it is
    ``starlette.formparsers`` that imports ``python_multipart`` at request
    time, and only when a form is parsed. Undeclared, the built image installs
    nothing, and the manual-upload route answers 500 on its first real request
    while every test on a developer's machine passes.
    """
    assert "python_multipart" in _declared()


def test_the_audit_can_actually_fail():
    """Guards the guard: if the AST walk silently found nothing, the test
    above would pass no matter what was missing."""
    imported = _imported()
    assert "PIL" in imported, "the AST walk stopped seeing Pillow's import"
    assert "sqlalchemy" in imported


def test_the_lint_rule_set_is_pinned_explicitly():
    """Ruff's default rule set is not a stable contract.

    With no explicit `select`, ruff 0.15.13 (local) found nothing while
    ruff 0.16.4 (CI, resolved from `ruff>=0.8`) found 127 errors in exactly
    the same code -- because the defaults broadened between them. Pinning the
    selection makes lint results a property of this repository rather than of
    whichever ruff a resolver happened to pick.
    """
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    select = data.get("tool", {}).get("ruff", {}).get("lint", {}).get("select")
    assert select, (
        "pyproject.toml has no [tool.ruff.lint] select, so lint results depend "
        "on the ruff version CI happens to install"
    )
