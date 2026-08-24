"""``text_file``: a collection from a list the operator keeps as a file.

The one builder whose source is neither a provider nor the library, and the
answer to "I already have this list, I just want it in Plex". The file lives
on the manual assets mount -- the same mount an operator drops a poster on --
because that is the mount that exists for operator-supplied files, and because
it means the list can be edited without touching the config or restarting
anything.

Two rules, and they are the whole module.

**The path is contained.** ``params.path`` is operator input naming a file
this service then reads, so it goes through the double-``realpath`` idiom
``api/manual.py:94-117`` and ``collections/posters.py:77-114`` use: the root
and the candidate are *both* resolved before being compared, so a symlink
sitting inside the mount and pointing at ``/etc/shadow`` is refused on its
target. A lexical check -- ``normpath``, a ``startswith``, or rejecting
literal ``..`` segments -- passes that case, and the path would then be read
and its contents parsed as ids. Absolute paths are refused one layer earlier,
at params validation, so an operator gets a config-load error rather than a
mid-pass one; joining an absolute path onto a root yields the absolute path,
so the containment check would catch it anyway.

**A line that does not parse raises.** Every id in this file looks like every
other id, so a typo is invisible in the file and, if it were skipped, invisible
everywhere else too: the collection would simply be one title short, forever,
with nothing to say so. The line number is in the message because the file is
the only place the operator can fix it.
"""
import asyncio
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel, ConfigDict, field_validator

from autoposter.collections.builders.base import (
    BuilderContext,
    BuilderResult,
    ExternalId,
)

__all__ = ["TextFileBuilder", "TextFileParams", "TextFileRefused"]


class TextFileRefused(Exception):
    """This list could not be read, for any of the reasons above.

    One class rather than one per reason: the engine logs the class name with
    the traceback and contains the definition either way, and the message --
    which reaches the log, never an action string -- is what tells the two
    apart. Nothing in it is derived from anything but the operator's own
    ``path`` and their own file.
    """


# The prefixes the format accepts. ``NAMESPACES`` itself is not reused because
# ``plex:`` is deliberately absent here: a rating key identifies an item on one
# server, which is meaningless in a file whose whole point is that it is
# portable and hand-edited.
_PREFIXES = frozenset({"imdb", "tmdb", "tvdb"})


class TextFileParams(BaseModel):
    """``text_file``'s params: which file, relative to the manual mount."""

    model_config = ConfigDict(extra="forbid")

    path: str

    @field_validator("path")
    @classmethod
    def _must_be_a_relative_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("path is empty; it names a file on the manual assets mount")
        # Both flavours, because the config is often written on the machine
        # the operator sits at rather than the one the container runs on: a
        # POSIX check alone reads "C:\lists\x.txt" as an ordinary relative
        # name and would resolve it to a nonsense path inside the mount.
        if PurePosixPath(cleaned).is_absolute() or PureWindowsPath(cleaned).is_absolute():
            raise ValueError(
                f"{value!r} is an absolute path; path is relative to the manual "
                "assets mount"
            )
        return cleaned


def _read(root: Path, relative: str) -> str:
    """The file's text, or a refusal. Synchronous: called from a thread.

    Off the event loop for ``api/manual.py``'s reason -- the manual assets
    root is typically NFS, so the realpath walk and the read are both calls
    that can block for as long as the mount feels like.
    """
    real_root = Path(os.path.realpath(root))
    resolved = Path(os.path.realpath(real_root / relative))
    if resolved == real_root or not resolved.is_relative_to(real_root):
        # The relative path only. The resolved path is the container's mount
        # layout, and this message reaches logs that get pasted into tickets.
        raise TextFileRefused(
            f"{relative!r} is not inside the manual assets mount"
        )
    try:
        return resolved.read_text(encoding="utf-8")
    except OSError as error:
        raise TextFileRefused(
            f"could not read {relative!r} from the manual assets mount "
            f"({type(error).__name__})"
        ) from error


def parse_ids(text: str, relative: str) -> list[ExternalId]:
    """Every id in the file, in the order it was written.

    ``#`` starts a comment anywhere on a line, which no id can contain, so a
    line annotated with the title it names still parses -- the alternative
    turns an obviously-intended annotation into a hard failure. Blank lines
    are skipped. Everything else must be an id, and an unprefixed one is read
    as IMDb: ``tt`` is that namespace's prefix and nothing else uses it.
    """
    ids: list[ExternalId] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        prefix, separator, rest = line.partition(":")
        if separator:
            namespace, value = prefix.strip().lower(), rest.strip()
        else:
            namespace, value = "imdb", line
        if namespace not in _PREFIXES or not _looks_like(namespace, value):
            raise TextFileRefused(
                f"line {number} of {relative!r} is not an id: {line!r}. "
                "Write 'imdb:tt0111161', 'tmdb:438631', 'tvdb:371980', or a "
                "bare 'tt0111161'."
            )
        ids.append((namespace, value))
    if not ids:
        raise TextFileRefused(
            f"{relative!r} contains no ids; an empty list would be read as "
            "'make no changes' rather than reported"
        )
    return ids


def _looks_like(namespace: str, value: str) -> bool:
    """Whether ``value`` is shaped like an id in ``namespace``.

    Shape only -- whether the id exists is the resolver's question, and an id
    the library does not own is an ordinary outcome. This catches the mistake
    that is *not* ordinary: an id filed under the wrong namespace, which
    resolves to nothing and reports nothing.
    """
    if namespace == "imdb":
        return value.startswith("tt") and value[2:].isdigit()
    return value.isdigit()


class TextFileBuilder:
    """The ids in one operator-maintained file, in file order."""

    type_name = "text_file"
    params_model = TextFileParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TextFileParams.model_validate(ctx.config)
        root = ctx.sources.manual_assets_root
        if root is None:
            # The bundle's absent-means-raise rule, applied to the one entry
            # in it that is not a client.
            raise TextFileRefused(
                "no manual assets mount is available to read the list from"
            )
        text = await asyncio.to_thread(_read, Path(root), params.path)
        return BuilderResult(ids=parse_ids(text, params.path))
