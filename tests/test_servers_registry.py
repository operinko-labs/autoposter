import pytest
from fastapi import HTTPException

from autoposter.config.loader import build_config, read_config_document
from autoposter.config.schema import Secrets
from autoposter.servers.registry import PLEX_REQUIRED, Servers, build_servers, plex_configured, require_plex
from conftest import EXAMPLE_CONFIG as EXAMPLE  # bare-name sibling import: `from tests.x` does not collect on the runner


def _secrets(**kw):
    return Secrets(database_url="postgresql+asyncpg://unused", tmdb_token="x", tvdb_apikey="x",
                   fanart_apikey="x", webhook_secret="x", **kw)


def test_plex_only_registry_holds_plex_and_nothing_else():
    cfg = build_config(read_config_document(EXAMPLE))
    servers = build_servers(cfg, _secrets(plex_token="t"), http=None)
    assert servers.names == ["plex"] and servers.plex is not None and servers.jellyfin is None
    assert list(servers) == ["plex"]


def test_jellyfin_only_registry_has_no_plex():
    doc = read_config_document(EXAMPLE)
    doc.pop("plex")
    doc["jellyfin"] = {"url": "https://jf"}
    with pytest.raises(NotImplementedError):  # until Task 14 lands the client
        build_servers(build_config(doc), _secrets(jellyfin_api_key="k"), http=None)


class _App:
    def __init__(self, servers): self.state = type("S", (), {"servers": servers})()


def test_the_gate_predicate_and_dependency():
    assert plex_configured(_App(Servers({}))) is False
    with pytest.raises(HTTPException) as caught:
        require_plex(type("R", (), {"app": _App(Servers({}))})())
    assert caught.value.status_code == 409 and caught.value.detail == PLEX_REQUIRED
