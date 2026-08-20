import hashlib
from pathlib import Path

import yaml

from autoposter.config.schema import Config


def load_config(path: Path) -> Config:
    """Load and validate the YAML config.

    ``version`` is a content hash of the file. It feeds render fingerprints, so any
    config edit that changes rendering marks the affected assets stale.
    """
    raw_bytes = Path(path).read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config at {path} must be a YAML mapping")
    data["version"] = hashlib.sha256(raw_bytes).hexdigest()[:16]
    return Config(**data)
