"""The boot decision, the state directory, and the two entrypoints.

Roadmap row 121. The decision this module tests is re-derived at every boot
and stored nowhere: there is no "setup complete" marker, because a marker is
a second source of truth that can disagree with the credentials, and the
disagreement's failure mode is a process that will not start and will not
offer to be fixed.
"""
import os
import stat
from pathlib import Path

import pytest
import yaml

from autoposter import boot
from autoposter.config import loader as loader_module
from autoposter.config import state as state_module
from autoposter.config.schema import (
    Secrets,
    missing_hard_secret_names,
    resolve_secret_values,
)

REPO = Path(__file__).resolve().parent.parent

HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_PLEX_TOKEN",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)

# Distinctive so the T6 grep gate can prove they never entered src/ or the
# frontend bundle.
FAKE_DB_URL = "postgresql+asyncpg://row121user:row-121-db-secret-4f1a@db.invalid:5432/ap"
FAKE_PLEX_TOKEN = "row-121-plex-token-9c2a"


@pytest.fixture(autouse=True)
def clean_secret_environment(monkeypatch, tmp_path):
    """No inherited credentials, and a state directory nobody else shares.

    The container's own environment carries none of these today, but a suite
    that depends on that is one `docker run -e` away from passing for the
    wrong reason.
    """
    for name in (*HARD, "AUTOPOSTER_ADMIN_PASSWORD_HASH", "AUTOPOSTER_API_KEY",
                 "AUTOPOSTER_MDBLIST_APIKEY", "AUTOPOSTER_RADARR_APIKEY",
                 "AUTOPOSTER_SONARR_APIKEY", "AUTOPOSTER_HARBOR_TOKEN",
                 "AUTOPOSTER_PLEX_ACCOUNT_TOKEN", "AUTOPOSTER_TRACEARR_APIKEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(state_module.STATE_DIR_ENV, str(tmp_path / "state"))


def _write_state_secrets(values: dict[str, str]) -> None:
    path = state_module.secrets_file_path()
    state_module.write_state_file(path, state_module.render_secrets_file(values))


# --- precedence: environment first, state file second -----------------------


def test_a_hard_secret_missing_from_the_environment_is_read_from_the_state_file(monkeypatch):
    _write_state_secrets({name: "from-file" for name in HARD})

    secrets = Secrets.load()

    assert secrets.plex_token == "from-file"
    assert secrets.database_url == "from-file"


def test_the_environment_wins_over_the_state_file(monkeypatch):
    _write_state_secrets({name: "from-file" for name in HARD})
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "from-env")

    assert Secrets.load().plex_token == "from-env"


def test_a_soft_secret_reads_from_the_state_file_too():
    _write_state_secrets({**{name: "x" for name in HARD}, "AUTOPOSTER_API_KEY": "keyed"})

    assert Secrets.load().api_key == "keyed"


def test_a_complete_environment_never_opens_the_state_file(monkeypatch):
    """The GitOps/ExternalSecrets exemption, pinned rather than assumed.

    The row's claim that those deployments "never enter setup mode and are
    unaffected" is true by CONSTRUCTION only if a complete environment never
    consults the file at all -- otherwise it is true only for as long as the
    trigger predicate happens to be right.
    """
    for name in HARD:
        monkeypatch.setenv(name, "from-env")

    def explode(path):
        raise AssertionError("the state file was read despite a complete environment")

    monkeypatch.setattr(state_module, "read_secrets_file", explode)
    monkeypatch.setattr("autoposter.config.schema.read_secrets_file", explode)

    assert Secrets.load().plex_token == "from-env"


def test_from_env_keeps_its_name_and_its_refusal_sentence(monkeypatch):
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("AUTOPOSTER_TMDB_TOKEN")

    with pytest.raises(RuntimeError, match="AUTOPOSTER_TMDB_TOKEN"):
        Secrets.from_env()


# --- the atomic writer ------------------------------------------------------


def test_the_written_file_is_0600_inside_a_0700_directory():
    _write_state_secrets({"AUTOPOSTER_API_KEY": "value"})
    path = state_module.secrets_file_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_failed_write_leaves_neither_a_partial_file_nor_a_temp_file(monkeypatch):
    """The whole point of rename-into-place: a reader sees the whole previous
    file or the whole new one, never half a credential."""
    path = state_module.secrets_file_path()
    state_module.write_state_file(path, "AUTOPOSTER_API_KEY=first\n")

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(state_module.os, "replace", boom)
    with pytest.raises(OSError):
        state_module.write_state_file(path, "AUTOPOSTER_API_KEY=second\n")

    assert path.read_text(encoding="utf-8") == "AUTOPOSTER_API_KEY=first\n"
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_value_with_a_line_break_is_refused_without_echoing_it():
    with pytest.raises(ValueError) as caught:
        state_module.render_secrets_file({"AUTOPOSTER_PLEX_TOKEN": "one\ntwo"})

    assert "AUTOPOSTER_PLEX_TOKEN" in str(caught.value)
    assert "one" not in str(caught.value)


def test_an_absent_state_file_is_an_empty_mapping_not_an_error():
    assert state_module.read_secrets_file(state_module.secrets_file_path()) == {}


def test_merging_keeps_the_names_an_earlier_step_wrote():
    state_module.merge_secrets_file({"AUTOPOSTER_PLEX_TOKEN": "a"})
    state_module.merge_secrets_file({"AUTOPOSTER_TMDB_TOKEN": "b"})

    held = state_module.read_secrets_file(state_module.secrets_file_path())
    assert held == {"AUTOPOSTER_PLEX_TOKEN": "a", "AUTOPOSTER_TMDB_TOKEN": "b"}


# --- the config document fallback ------------------------------------------


def test_the_config_path_falls_back_to_the_state_directory(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)
    written = state_module.state_config_path()
    state_module.write_state_file(written, yaml.safe_dump({"workers": 5}))

    assert loader_module._default_config_path() == written


def test_an_explicit_autoposter_config_is_never_overridden(monkeypatch):
    """C5: the new lookup fires only when AUTOPOSTER_CONFIG is unset. Both
    real deployments set it, so both keep exactly today's path."""
    monkeypatch.setenv("AUTOPOSTER_CONFIG", "/config/autoposter.yaml")
    state_module.write_state_file(state_module.state_config_path(), yaml.safe_dump({"a": 1}))

    assert loader_module._default_config_path() == Path("/config/autoposter.yaml")


def test_with_neither_the_path_is_the_mount_it_has_always_been(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)

    assert loader_module._default_config_path() == Path("/config/autoposter.yaml")


# --- the decision matrix ----------------------------------------------------


def test_configured_needs_every_hard_name_and_a_database_that_answers(monkeypatch):
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(boot, "database_answers", _answering(True))

    assert boot.is_configured(resolve_secret_values()) is True


def test_a_missing_hard_name_means_setup_mode_without_touching_the_database(monkeypatch):
    for name in HARD[1:]:
        monkeypatch.setenv(name, "x")

    def never(url):
        raise AssertionError("the database was probed with an incomplete credential set")

    monkeypatch.setattr(boot, "database_answers", never)

    assert boot.is_configured(resolve_secret_values()) is False


def test_a_database_that_refuses_means_setup_mode(monkeypatch):
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(boot, "database_answers", _answering(False, "ConnectionRefusedError"))

    assert boot.is_configured(resolve_secret_values()) is False


def test_the_refusal_is_logged_as_a_class_name_and_never_as_the_url(monkeypatch, caplog):
    for name in HARD[1:]:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", FAKE_DB_URL)
    monkeypatch.setattr(boot, "database_answers", _answering(False, "ConnectionRefusedError"))

    with caplog.at_level("INFO"):
        boot.is_configured(resolve_secret_values())

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ConnectionRefusedError" in text
    assert "row-121-db-secret" not in text
    assert FAKE_DB_URL not in text


def test_missing_hard_names_are_reported_as_names_only():
    assert missing_hard_secret_names({"AUTOPOSTER_PLEX_TOKEN": "x"}) == [
        "AUTOPOSTER_DATABASE_URL",
        "AUTOPOSTER_TMDB_TOKEN",
        "AUTOPOSTER_TVDB_APIKEY",
        "AUTOPOSTER_FANART_APIKEY",
        "AUTOPOSTER_WEBHOOK_SECRET",
    ]


# --- the handover -----------------------------------------------------------


def _answering(ok: bool, failure: str = ""):
    async def answers(url: str) -> tuple[bool, str]:
        return ok, failure

    return answers


def test_a_configured_boot_exports_migrates_and_execs_the_default_command(monkeypatch):
    _write_state_secrets({name: "from-file" for name in HARD})
    monkeypatch.setattr(boot, "database_answers", _answering(True))
    order: list[str] = []
    monkeypatch.setattr(boot, "_migrate", lambda: order.append("migrate"))
    monkeypatch.setattr(
        boot.os, "execv", lambda path, argv: order.append(f"execv:{argv[-1]}")
    )

    boot.main([])

    assert order == ["migrate", "execv:autoposter.main"]
    # Exported BEFORE the migration: alembic/env.py reads this name from the
    # process environment and would otherwise refuse to run at all.
    assert os.environ["AUTOPOSTER_DATABASE_URL"] == "from-file"


def test_a_configured_boot_execs_the_command_argv_names(monkeypatch):
    """docker-compose.yml's api service passes its uvicorn --reload line; the
    image's CMD passes nothing. That is now the only difference between the
    two boots."""
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(boot, "database_answers", _answering(True))
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    seen: list[list[str]] = []
    monkeypatch.setattr(boot.os, "execvp", lambda file, argv: seen.append(argv))

    boot.main(["uvicorn", "autoposter.main:build", "--factory", "--reload"])

    assert seen == [["uvicorn", "autoposter.main:build", "--factory", "--reload"]]


def test_an_unconfigured_boot_runs_no_migration_and_serves_the_setup_app(monkeypatch):
    served: list[object] = []
    monkeypatch.setattr(boot, "_migrate", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", lambda app, **kwargs: served.append(app))

    boot.main([])

    assert len(served) == 1
    assert served[0].title == "autoposter setup"


def _must_not_run(*args, **kwargs):
    raise AssertionError("an unconfigured boot must not migrate or exec")


# --- the two entrypoints ----------------------------------------------------


def test_both_entrypoints_go_through_the_boot_module():
    """The image's CMD and the compose api command are one contract in two
    files. They drifted before over `alembic upgrade head`; now they drift
    over which module decides whether it runs at all."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))

    assert 'CMD ["sh", "-c", "exec python -m autoposter.boot"]' in dockerfile
    assert "alembic upgrade head" not in compose["services"]["api"]["command"]
    assert "python -m autoposter.boot" in compose["services"]["api"]["command"]


def test_the_image_still_execs_so_python_is_pid_one():
    """The #174 hardening. Without `exec`, whether SIGTERM reaches python at
    all depends on shell-implementation behaviour this Dockerfile does not
    otherwise depend on."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert '"exec python -m autoposter.boot"' in dockerfile
