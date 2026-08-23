"""The config generation swap: one mutable box around an immutable ``Config``.

Reload is not an in-place edit. A new ``Config`` is built from base+overrides
and validated whole, then the holder is pointed at it in a single assignment;
nothing ever observes a half-applied config, because a half-applied config is
never constructed. Consumers that were handed the *holder* rather than the
instance -- the worker handler, the scheduler job factories, the broadcaster --
pick the new generation up on their next read with no restart.

No lock. The swap is a single attribute assignment and every reader is on the
one event loop, so a reader either sees the old object or the new one; there is
no interleaving to guard against. A lock here would only add the impression
that a reader could hold a consistent view across several reads, which it
cannot and does not need to -- each read yields one complete, valid ``Config``.
"""
from autoposter.config.schema import Config


class ConfigHolder:
    def __init__(self, initial: Config):
        self._config = initial

    @property
    def current(self) -> Config:
        return self._config

    def swap(self, new: Config) -> None:
        self._config = new
