"""Code splitting survives the build (perf spec A3).

Asserted against ``frontend/dist`` for the reason tests/test_attribution_present.py
gives: a component test passes whatever the bundler did with the imports.
"What the shell loads" is every script ``index.html`` names -- the entry and
its modulepreload links -- which is exactly what a first visit downloads
before any route renders. Skipped locally without a build, a hard failure in
CI (``_require_dist``).
"""
import re
from pathlib import Path

from test_attribution_present import TMDB_NOTICE, _require_dist

# A class name recharts puts on every chart's outer element; nothing else in
# this bundle emits it, so its presence in a chunk means recharts is in it.
RECHARTS_MARKER = "recharts-wrapper"
_SHELL_SCRIPT = re.compile(r"""["'](/assets/[^"']+\.js)["']""")


def _shell_scripts(dist: Path) -> list[Path]:
    index = (dist / "index.html").read_text(encoding="utf-8")
    return [dist / path.lstrip("/") for path in _SHELL_SCRIPT.findall(index)]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_the_shell_loads_at_least_one_script():
    """Guards the two below: with nothing extracted they would pass vacuously."""
    scripts = _shell_scripts(_require_dist())
    assert scripts and all(path.is_file() for path in scripts), scripts


def test_recharts_is_built_but_not_loaded_by_the_shell():
    dist = _require_dist()
    every_chunk = sorted((dist / "assets").glob("*.js"))
    assert any(RECHARTS_MARKER in _text(path) for path in every_chunk), (
        f"no chunk contains {RECHARTS_MARKER!r}: recharts is missing from the "
        "build or no longer emits that class -- pick another recharts-only literal"
    )
    loaded = [path.name for path in _shell_scripts(dist) if RECHARTS_MARKER in _text(path)]
    assert loaded == [], f"recharts is in what the shell loads eagerly: {loaded}"


def test_the_settings_page_is_not_loaded_by_the_shell():
    """TMDB's notice is rendered only by the Settings and item pages, so it
    stands in for "a page's code": in the entry means pages are not split."""
    dist = _require_dist()
    loaded = [path.name for path in _shell_scripts(dist) if TMDB_NOTICE in _text(path)]
    assert loaded == [], f"a page's code is in what the shell loads eagerly: {loaded}"
