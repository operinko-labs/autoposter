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
  ``config/loader.py`` when ``AUTOPOSTER_CONFIG`` names nothing or names a
  file that is not there (the image bakes that variable, so "set" alone
  cannot mean "mounted").

Reading order is the environment first and the file second, everywhere. That
one rule is what makes "a GitOps/ExternalSecrets deployment is unaffected"
true by construction rather than by a mode check happening to be right: a
deployment whose environment is complete never opens either file.
"""

import logging
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

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


def example_config_path() -> Path:
    """The shipped example document the setup wizard starts from.

    A fresh deployment has no ``/config/autoposter.yaml`` and ``Config`` has
    eight fields with no default, so nothing can be synthesised -- the example
    document is the only honest starting point, and the image therefore has to
    carry it.

    ``AUTOPOSTER_EXAMPLE_CONFIG`` in the image, because the package is
    pip-installed into site-packages there and nothing is findable relative to
    the module; the repository copy otherwise. The same shape, and for the same
    reason, as ``api/spa.py``'s ``spa_dist()``.
    """
    configured = os.environ.get("AUTOPOSTER_EXAMPLE_CONFIG")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parent.parent.parent.parent
    return root / "config" / "autoposter.example.yaml"


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
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        # The NAME is stripped, the value never is: a credential that ends in
        # a space is a credential, and eating it would corrupt it silently.
        values[name.strip()] = value
    return values


def render_secrets_file(values: Mapping[str, str]) -> str:
    """The file's text, sorted so a rewrite produces a stable document.

    The refusal names the VARIABLE and never the value: this module's whole
    subject is credentials, and an exception message reaches the log.

    What is refused is any value the READER would see as more than one line.
    ``str.splitlines`` -- which ``read_secrets_file`` parses with -- also
    splits on ``\\v``, ``\\f``, ``\\x1c``-``\\x1e``, ``\\x85`` and
    U+2028/U+2029, so refusing only ``\\n`` and ``\\r`` would let such a value
    be written whole and read back as two, with a tail containing ``=``
    becoming a second ``NAME=value`` entry. One definition for both halves,
    expressed as the round trip itself. The empty string is the one value that
    is not a line at all (``"".splitlines() == []``) and passes.
    """
    lines = [
        "# Written by autoposter's first-start setup wizard.",
        "# The process environment wins over every name here; see the",
        '# "First-start setup" section of deploy/README.md.',
    ]
    for name in sorted(values):
        value = values[name]
        if value and value.splitlines() != [value]:
            raise ValueError(f"{name} contains a line break and cannot be stored")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"


def _tighten_directory(directory: Path) -> None:
    """0700, when this process is allowed to say so.

    Best-effort by design, and only when the mode is wider than 0700 already.
    The production shape is a Kubernetes PVC mounted at /state with
    ``runAsUser: 568`` and ``fsGroup: 568``: fsGroup fixes GROUP ownership and
    adds group bits, but the mount root's owning uid stays 0, and ``chmod``
    requires ownership -- so an unconditional call raises ``PermissionError``
    on the one deployment shape this row targets, before a single credential
    can be stored.

    Losing this is survivable; the file's own 0600 is what actually protects
    the credentials, and ``tempfile.mkstemp`` gives it that from creation
    rather than after a window.

    "Wider than 0700" is ``& 0o077`` -- group or other can see it -- and not
    ``& ~_DIR_MODE``, which counts the SETGID bit as excess permission. An
    fsGroup volume root is 0o2700: already private, and its setgid bit is what
    gives the group inheritance fsGroup exists to provide. Under the old test
    that directory was chmod'ed anyway -- harmlessly on the PVC, where the
    call is denied, but on any deployment where this process does own the
    directory the setgid bit was stripped for no gain.
    """
    try:
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            os.chmod(directory, _DIR_MODE)
    except PermissionError:
        # The PATH, never a value: everything this module writes is a secret.
        logger.warning("cannot set 0700 on the state directory: %s", directory)


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
    _tighten_directory(directory)
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
