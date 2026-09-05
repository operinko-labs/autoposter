"""The private state directory a first-start deployment writes to.

Two files live here and they are deliberately different objects with
different lifetimes and different readers:

* ``secrets.env`` -- ``KEY=value`` lines under exactly the names ``Secrets``
  already reads from the environment, mode 0600. It is NOT the config
  document: ``config/overrides.py::_reject_secrets`` makes a ``secrets`` key
  in that document a hard error precisely because storing one there would
  write a token into a database row and into every config API response that
  echoes the overrides. A separate file with its own reader is what lets that
  rule stay.
* ``autoposter.yaml`` -- the config document, consulted by
  ``config/loader.py`` only when ``AUTOPOSTER_CONFIG`` names nothing.

Reading order is the environment first and the file second, everywhere. That
one rule is what makes "a GitOps/ExternalSecrets deployment is unaffected"
true by construction rather than by a mode check happening to be right: a
deployment whose environment is complete never opens either file.
"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

STATE_DIR_ENV = "AUTOPOSTER_STATE_DIR"
DEFAULT_STATE_DIR = Path("/state")
SECRETS_FILE_NAME = "secrets.env"
CONFIG_FILE_NAME = "autoposter.yaml"

# Set by this module on every write rather than trusted from the mount: a
# Kubernetes PVC arrives with whatever the storage class gave it, and fsGroup
# fixes group ownership, not the mode.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


def state_dir() -> Path:
    return Path(os.environ.get(STATE_DIR_ENV) or DEFAULT_STATE_DIR)


def secrets_file_path() -> Path:
    return state_dir() / SECRETS_FILE_NAME


def state_config_path() -> Path:
    return state_dir() / CONFIG_FILE_NAME


def read_secrets_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines as a dict; an absent file is an empty dict.

    Only a missing file is swallowed. A file that exists and cannot be read is
    an operator mistake that must be loud, not one that silently drops the
    deployment into setup mode with an unauthenticated wizard on its port.

    Values are taken verbatim after the first ``=``, unquoted and unescaped:
    every name here is an opaque credential, and a quoting scheme would be a
    second thing that can be got wrong. ``render_secrets_file`` refuses the
    one value that could not survive the round trip.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return {}
    values: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value
    return values


def render_secrets_file(values: Mapping[str, str]) -> str:
    """The file's text, sorted so a rewrite produces a stable document.

    The refusal names the VARIABLE and never the value: this module's whole
    subject is credentials, and an exception message reaches the log.
    """
    lines = [
        "# Written by autoposter's first-start setup wizard.",
        "# The process environment wins over every name here; see the",
        '# "First-start setup" section of deploy/README.md.',
    ]
    for name in sorted(values):
        value = values[name]
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} contains a line break and cannot be stored")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def write_state_file(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, 0600, in a 0700 directory.

    Temp file in the SAME directory -> fsync -> ``os.replace``: a rename
    within one directory is atomic on POSIX, so a reader -- including the next
    boot, which decides the whole mode from this file -- sees either the whole
    previous file or the whole new one, never half a credential. The directory
    is fsynced afterwards so the rename itself survives a power loss, which is
    the half a bare replace leaves undone.

    There was no atomic-write precedent anywhere in ``src/`` before this; the
    durability story is written here rather than copied, which is why it is
    spelled out.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, _DIR_MODE)
    handle, temp_name = tempfile.mkstemp(dir=directory, prefix=".autoposter-", suffix=".partial")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_path, _FILE_MODE)
        os.replace(temp_path, path)
    except BaseException:
        # Including KeyboardInterrupt: a .partial file left in a directory the
        # next boot reads is exactly the ambiguity this function exists to
        # remove.
        temp_path.unlink(missing_ok=True)
        raise
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def merge_secrets_file(values: Mapping[str, str]) -> None:
    """Add or replace ``values``, leaving every other name alone.

    Each wizard step persists only what it collected, so a later step must not
    drop an earlier one's names.
    """
    path = secrets_file_path()
    merged = read_secrets_file(path)
    merged.update(values)
    write_state_file(path, render_secrets_file(merged))
