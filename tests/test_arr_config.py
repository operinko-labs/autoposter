"""Radarr/Sonarr/arr_sync configuration.

These assert the *schema* defaults directly, not values loaded from the
example YAML -- the same reasoning as ``tests/test_scheduler_config.py``:
loading the example and checking what came back tests the YAML, not the
default, and a default changed to match the example would still pass that.

Both services must default to off (``enabled: false``) and to not writing
anything (``add_existing: false``) -- an operator who never touches this
section of the config must get a pure no-op, not a background process
quietly registering items with a service it was never told about.
"""
from autoposter.config.schema import ArrSyncConfig, RadarrConfig, Secrets, SonarrConfig


def test_radarr_defaults_to_disabled_and_not_writing():
    defaults = RadarrConfig()
    assert defaults.enabled is False
    assert defaults.add_existing is False


def test_sonarr_defaults_to_disabled_and_not_writing():
    defaults = SonarrConfig()
    assert defaults.enabled is False
    assert defaults.add_existing is False


def test_radarr_path_defaults_match_the_live_mapping():
    """Verified live: Plex mounts /mnt/Media (capital M), Radarr sees the
    same files at /mnt/media (lowercase)."""
    defaults = RadarrConfig()
    assert defaults.plex_path == "/mnt/Media"
    assert defaults.arr_path == "/mnt/media"


def test_sonarr_path_defaults_match_the_live_mapping():
    defaults = SonarrConfig()
    assert defaults.plex_path == "/mnt/Media"
    assert defaults.arr_path == "/mnt/media"


def test_arr_sync_defaults_to_enabled_with_a_500_batch_size():
    """The safety net runs even when neither service is configured -- that
    is the whole point of it being independent of radarr/sonarr.enabled."""
    defaults = ArrSyncConfig()
    assert defaults.enabled is True
    assert defaults.batch_size == 500
    assert defaults.hours == 24


def test_radarr_and_sonarr_config_carry_no_api_key_field():
    """The key comes from Secrets/the environment, never from Config -- see
    Secrets.radarr_apikey/sonarr_apikey below. A field here would invite a
    key to be pasted straight into the YAML file."""
    assert "api_key" not in RadarrConfig.model_fields
    assert "api_key" not in SonarrConfig.model_fields


def test_secrets_default_arr_apikeys_to_empty_and_read_them_from_env(monkeypatch):
    """Soft secrets, the same posture as mdblist_apikey: both services
    default to disabled, so a deployment that never configures either one
    must still boot without these environment variables set."""
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "t")
    monkeypatch.setenv("AUTOPOSTER_TMDB_TOKEN", "t")
    monkeypatch.setenv("AUTOPOSTER_TVDB_APIKEY", "t")
    monkeypatch.setenv("AUTOPOSTER_FANART_APIKEY", "t")
    monkeypatch.setenv("AUTOPOSTER_WEBHOOK_SECRET", "t")
    monkeypatch.delenv("AUTOPOSTER_RADARR_APIKEY", raising=False)
    monkeypatch.delenv("AUTOPOSTER_SONARR_APIKEY", raising=False)

    secrets = Secrets.from_env()

    assert secrets.radarr_apikey == ""
    assert secrets.sonarr_apikey == ""

    monkeypatch.setenv("AUTOPOSTER_RADARR_APIKEY", "radarr-key")
    monkeypatch.setenv("AUTOPOSTER_SONARR_APIKEY", "sonarr-key")

    secrets = Secrets.from_env()

    assert secrets.radarr_apikey == "radarr-key"
    assert secrets.sonarr_apikey == "sonarr-key"
