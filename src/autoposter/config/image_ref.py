"""Derives Harbor's registry/project/repository coordinates from the image
reference a deployment already knows: the one it pulled to run this pod.

Those three pieces used to be operator-typed config (``version_check:`` in
``autoposter.yaml``), but they are not a preference -- a deployment cannot
point its version check at a different registry than the one its own image
came from without the comparison becoming meaningless. They are a deployment
*fact*, and ``AUTOPOSTER_IMAGE_REF`` is the one env var that already carries
them, e.g. ``registry.example.com/library/autoposter:sha-abc1234``. This
module is the one place that splits it back into the three pieces
``api/version.py`` needs.
"""
import logging

logger = logging.getLogger(__name__)


def parse_image_ref(ref: str) -> tuple[str, str, str] | None:
    """``(registry, project, repository)``, or ``None`` if the check is off.

    An unset or blank ``ref`` switches the check off silently -- today's
    behavior for a deployment that never configured it, unchanged by this
    module existing. A non-blank ``ref`` this cannot parse also switches the
    check off, but logs one WARNING naming the reason first: the ref is never
    in that message, at any level above DEBUG, because it carries the
    registry host -- the same operator-URL rule ``api/version.py`` already
    holds the Harbor URL to.

    The shape assumed, Docker's own: ``[registry/]project/repository[...]
    [:tag][@digest]``, where the registry segment is recognized by carrying a
    ``.`` or a ``:`` (a port) -- exactly what distinguishes
    ``registry.example.com/project/repo`` from a hostless
    ``project/repo``, which this refuses rather than guessing which segment
    is missing. The repository may itself be multiple ``/``-separated
    segments; only its last one can carry a tag, so a registry port's own
    ``:`` is never mistaken for one.
    """
    ref = ref.strip()
    if not ref:
        return None

    # A digest (`name@sha256:...`) can accompany a tag (`name:tag@sha256:...`);
    # stripping it first means the tag-stripping below never has to know digests
    # exist.
    ref = ref.split("@", 1)[0]

    segments = ref.split("/")
    if len(segments) < 3:
        logger.warning(
            "AUTOPOSTER_IMAGE_REF could not be parsed: expected "
            "registry/project/repository, found %d segment(s)", len(segments),
        )
        return None

    registry = segments[0]
    if "." not in registry and ":" not in registry:
        logger.warning(
            "AUTOPOSTER_IMAGE_REF could not be parsed: the first segment "
            "does not look like a registry host (no '.' or ':')"
        )
        return None

    project = segments[1]
    repository_segments = segments[2:]
    last = repository_segments[-1]
    if ":" in last:
        repository_segments[-1] = last.rsplit(":", 1)[0]
    repository = "/".join(repository_segments)

    if not project or not repository or any(not s for s in repository_segments):
        logger.warning(
            "AUTOPOSTER_IMAGE_REF could not be parsed: a project or "
            "repository segment was empty"
        )
        return None

    return registry, project, repository
