"""The development environment must not be able to become the shipped one.

Every guard here exists because its failure is silent. A development stage that
becomes the default build target still passes every check in the image job --
it contains a superset of the runtime image -- and would be pushed to Harbor as
production. A vite proxy pointed at a port nothing serves looks like a working
configuration file right up until someone runs the dev server, which is how it
survived in this repository until 2026-08-21.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
VITE_CONFIG = REPO / "frontend" / "vite.config.ts"
MAIN = REPO / "src" / "autoposter" / "main.py"


def _stages() -> list[str]:
    """Every stage name, in file order; unnamed stages appear as ``""``."""
    stages = []
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"FROM\s+\S+(?:\s+AS\s+(\S+))?\s*$", line)
        if match:
            stages.append(match.group(1) or "")
    return stages


def test_the_runtime_stage_is_the_last_one():
    """``docker build .`` targets the last stage and CI passes no ``target:``.

    So a development stage placed after ``runtime`` becomes what
    .forgejo/workflows/ci.yml builds, verifies and pushes -- and every existing
    check in that job would still pass, because the development image contains
    everything the runtime one does plus pytest and ruff. The image would ship
    with the test toolchain in it and nothing would have complained.
    """
    stages = _stages()
    assert stages, "no FROM lines found in the Dockerfile"
    assert stages[-1] == "runtime", (
        f"the last stage in the Dockerfile is {stages[-1]!r}, not 'runtime'; "
        "`docker build .` builds the last stage and the image job passes no "
        "`target:`, so this is what would be pushed to Harbor as production"
    )
