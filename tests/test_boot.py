"""The boot decision, the state directory, and the two entrypoints.

Roadmap row 121. The decision this module tests is re-derived at every boot
and stored nowhere: there is no "setup complete" marker, because a marker is
a second source of truth that can disagree with the credentials, and the
disagreement's failure mode is a process that will not start and will not
offer to be fixed.

CONFIGURED is credentials plus a config document. The database is not part of
it -- the last section here pins the probe that used to be, which is now the
setup wizard's step-2 validation and nothing else.

There are three outcomes and only one of them is the wizard: a missing hard
secret is setup mode, a missing config document with the credentials all
present is a configuration error that exits non-zero, and both halves is the
boot every deployment does today.
"""
import os
import socket
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

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
from autoposter.db import base as db_base

REPO = Path(__file__).resolve().parent.parent

HARD = (
    "AUTOPOSTER_DATABASE_URL",
    "AUTOPOSTER_PLEX_TOKEN",
    "AUTOPOSTER_TMDB_TOKEN",
    "AUTOPOSTER_TVDB_APIKEY",
    "AUTOPOSTER_FANART_APIKEY",
    "AUTOPOSTER_WEBHOOK_SECRET",
)

SOFT = (
    "AUTOPOSTER_ADMIN_PASSWORD_HASH",
    "AUTOPOSTER_API_KEY",
    "AUTOPOSTER_MDBLIST_APIKEY",
    "AUTOPOSTER_RADARR_APIKEY",
    "AUTOPOSTER_SONARR_APIKEY",
    "AUTOPOSTER_HARBOR_TOKEN",
    "AUTOPOSTER_PLEX_ACCOUNT_TOKEN",
    "AUTOPOSTER_TRACEARR_APIKEY",
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

    Saved and restored by hand rather than through `monkeypatch.delenv`
    alone: `boot._export` assigns into `os.environ` directly, and monkeypatch
    records no undo entry for a name that was absent when the test began, so
    a boot test would otherwise leak a credential into every test after it.
    """
    names = (*HARD, *SOFT, "AUTOPOSTER_CONFIG")
    saved = {name: os.environ[name] for name in names if name in os.environ}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(state_module.STATE_DIR_ENV, str(tmp_path / "state"))
    yield
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(saved)


def _write_state_secrets(values: dict[str, str]) -> None:
    path = state_module.secrets_file_path()
    state_module.write_state_file(path, state_module.render_secrets_file(values))


def _write_state_config() -> Path:
    """The document the wizard's last step writes: the second half of
    CONFIGURED."""
    path = state_module.state_config_path()
    state_module.write_state_file(path, yaml.safe_dump({"workers": 2}))
    return path


def _must_not_run(*args, **kwargs):
    raise AssertionError("this boot must not migrate, exec, or open a database")


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


def test_an_empty_environment_value_counts_as_absent(monkeypatch):
    """`AUTOPOSTER_PLEX_TOKEN=` in a copied .env is a name nobody supplied.

    Empty-means-absent has to hold in every reader at once, or the reader
    that decides the boot mode and the reader that builds `Secrets` disagree
    about whether this deployment has credentials at all.
    """
    _write_state_secrets({name: "from-file" for name in HARD})
    monkeypatch.setenv("AUTOPOSTER_PLEX_TOKEN", "")

    assert resolve_secret_values()["AUTOPOSTER_PLEX_TOKEN"] == "from-file"
    assert Secrets.load().plex_token == "from-file"
    assert missing_hard_secret_names({name: "" for name in HARD}) == list(HARD)


# --- the atomic writer ------------------------------------------------------


def test_the_written_file_is_0600_inside_a_0700_directory():
    _write_state_secrets({"AUTOPOSTER_API_KEY": "value"})
    path = state_module.secrets_file_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_private_setgid_directory_keeps_its_setgid_bit():
    """0o2700 is the fsGroup volume root's mode, and it is already private.

    "Wider than 0700" has to mean "group or other can see it" (``& 0o077``);
    ``& ~0o700`` counts setgid as excess permission and chmods it away -- on
    the PVC the call is denied and nothing happens, but on any deployment
    where this process owns the directory it strips the bit the group
    inheritance depends on.
    """
    path = state_module.secrets_file_path()
    path.parent.mkdir(parents=True)
    os.chmod(path.parent, 0o2700)

    _write_state_secrets({"AUTOPOSTER_API_KEY": "value"})

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_directory_this_process_may_not_chmod_is_still_written_to(monkeypatch, caplog):
    """The production shape is a PVC whose mount root is owned by uid 0 while
    the process runs as 568: fsGroup fixes group ownership, not the owner, and
    chmod requires the owner. An unconditional chmod would leave the wizard
    unable to store anything on the one deployment shape this row targets."""
    path = state_module.secrets_file_path()
    real_chmod = state_module.os.chmod

    def chmod(target, mode):
        if Path(target) == path.parent:
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(target, mode)

    monkeypatch.setattr(state_module.os, "chmod", chmod)

    with caplog.at_level("WARNING"):
        _write_state_secrets({"AUTOPOSTER_API_KEY": FAKE_PLEX_TOKEN})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert state_module.read_secrets_file(path) == {"AUTOPOSTER_API_KEY": FAKE_PLEX_TOKEN}
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert str(path.parent) in logged
    assert FAKE_PLEX_TOKEN not in logged


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


def test_a_value_with_an_exotic_line_separator_is_refused_too():
    """`read_secrets_file` parses with `str.splitlines`, which splits on more
    than `\\n` and `\\r`. A value carrying one of those was written whole and
    read back as two lines, and a tail containing `=` became a second entry --
    so the writer's refusal uses the reader's own definition of a line."""
    for separator in ("\x85", "\u2028"):
        with pytest.raises(ValueError) as caught:
            state_module.render_secrets_file(
                {"AUTOPOSTER_PLEX_TOKEN": f"one{separator}AUTOPOSTER_API_KEY=two"}
            )

        assert "AUTOPOSTER_PLEX_TOKEN" in str(caught.value)
        assert "two" not in str(caught.value)


def test_a_value_that_ends_in_a_space_round_trips_intact():
    """A credential ending in whitespace is a credential; eating it would
    corrupt the one thing this file exists to hold, silently."""
    _write_state_secrets({"AUTOPOSTER_API_KEY": " value "})

    held = state_module.read_secrets_file(state_module.secrets_file_path())

    assert held == {"AUTOPOSTER_API_KEY": " value "}


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
    written = _write_state_config()

    assert loader_module._default_config_path() == written


def test_a_present_configured_document_always_wins(monkeypatch, tmp_path):
    """C5: a deployment that mounts a document at the path it names keeps
    exactly today's path, whatever the state directory holds."""
    mounted = tmp_path / "mounted.yaml"
    mounted.write_text(yaml.safe_dump({"workers": 9}), encoding="utf-8")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mounted))
    _write_state_config()

    assert loader_module._default_config_path() == mounted
    assert loader_module.config_document_path() == mounted


def test_a_configured_path_that_is_not_there_falls_back_to_the_state_document(
    monkeypatch, tmp_path
):
    """The shape every container from this image has on its first start: the
    variable is baked into the image and nothing is mounted at it. Keying the
    fallback on "the variable is set" makes it unreachable exactly there --
    the wizard would write its document, the next boot would still read
    /config/autoposter.yaml, raise FileNotFoundError, and crash-loop with no
    wizard left to fix it."""
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "config" / "autoposter.yaml"))
    written = _write_state_config()

    assert loader_module._default_config_path() == written
    assert loader_module.config_document_path() == written


def test_the_image_bakes_a_config_path_a_first_start_container_has_no_file_at():
    """Why the predicate above is about presence and not about the variable."""
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")

    assert "ENV AUTOPOSTER_CONFIG=/config/autoposter.yaml" in dockerfile


def test_with_neither_the_path_is_the_mount_it_has_always_been(monkeypatch):
    monkeypatch.delenv("AUTOPOSTER_CONFIG", raising=False)

    assert loader_module._default_config_path() == Path("/config/autoposter.yaml")


def test_no_document_anywhere_is_reported_as_none(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "absent.yaml"))

    assert loader_module.config_document_path() is None


# --- the decision matrix ----------------------------------------------------


def test_configured_needs_every_hard_name_and_a_config_document(monkeypatch):
    for name in HARD:
        monkeypatch.setenv(name, "x")
    _write_state_config()

    assert boot.is_configured(resolve_secret_values()) is True


def test_a_missing_hard_name_means_setup_mode(monkeypatch):
    for name in HARD[1:]:
        monkeypatch.setenv(name, "x")
    _write_state_config()

    assert boot.is_configured(resolve_secret_values()) is False


def test_credentials_without_a_config_document_exit_instead_of_serving_the_wizard(
    monkeypatch, tmp_path
):
    """Setup mode has exactly one door: a hard secret that does not resolve.

    A deployment holding every credential was configured by somebody, so a
    config document that is not there is that somebody's mistake -- and the
    answer to a mistake is the restart loop it caused before this module
    existed, plus a line naming the two paths. Serving the wizard instead
    would put an unauthenticated credential-collecting form on the port the
    Service and Ingress already point at.
    """
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(tmp_path / "absent.yaml"))
    monkeypatch.setattr(boot, "_migrate", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)

    assert boot.is_configured(resolve_secret_values()) is False
    with pytest.raises(SystemExit) as caught:
        boot.main([])

    assert caught.value.code == 1


def test_a_config_map_that_lost_its_key_never_becomes_a_wizard(monkeypatch, tmp_path):
    """The reachable production shape, and the reason the rule above exists.

    A ConfigMap whose key is renamed still mounts, so the pod starts and
    `/config/autoposter.yaml` is simply absent (a DELETED ConfigMap is safe --
    the pod never leaves ContainerCreating). Every hard name is in the
    environment, so this is a fully configured deployment with one typo, and
    the setup application must not even be constructed.
    """
    mount = tmp_path / "config"
    mount.mkdir()
    for name in HARD:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(mount / "autoposter.yaml"))
    monkeypatch.setitem(
        sys.modules,
        "autoposter.api.setup",
        SimpleNamespace(build_setup_app=_must_not_run),
    )
    monkeypatch.setattr(boot, "_migrate", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)

    with pytest.raises(SystemExit) as caught:
        boot.main([])

    assert caught.value.code == 1


def test_the_decision_never_opens_a_database(monkeypatch):
    """The amended C1. A configured deployment whose database is down keeps
    failing its migration and restarting, exactly as the shell line this
    replaced did -- it never demotes itself into an unauthenticated wizard on
    the port the Service and Ingress already point at."""
    for name in HARD[1:]:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", FAKE_DB_URL)
    _write_state_config()
    monkeypatch.setattr(db_base, "make_engine", _must_not_run)
    monkeypatch.setattr(db_base, "database_answers", _must_not_run)

    assert boot.is_configured(resolve_secret_values()) is True


def test_missing_hard_names_are_reported_as_names_only():
    assert missing_hard_secret_names({"AUTOPOSTER_PLEX_TOKEN": "x"}) == [
        "AUTOPOSTER_DATABASE_URL",
        "AUTOPOSTER_TMDB_TOKEN",
        "AUTOPOSTER_TVDB_APIKEY",
        "AUTOPOSTER_FANART_APIKEY",
        "AUTOPOSTER_WEBHOOK_SECRET",
    ]


def test_both_refusals_log_names_and_paths_and_never_a_value(monkeypatch, tmp_path, caplog):
    absent = tmp_path / "absent.yaml"
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", FAKE_DB_URL)
    monkeypatch.setenv("AUTOPOSTER_CONFIG", str(absent))

    with caplog.at_level("INFO"):
        assert boot.is_configured(resolve_secret_values()) is False
        for name in HARD[1:]:
            monkeypatch.setenv(name, "x")
        assert boot.is_configured(resolve_secret_values()) is False

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "AUTOPOSTER_PLEX_TOKEN" in logged
    assert str(absent) in logged
    assert "row-121-db-secret" not in logged
    assert FAKE_DB_URL not in logged


# --- the handover -----------------------------------------------------------


def test_a_configured_boot_exports_migrates_and_execs_the_default_command(monkeypatch):
    _write_state_secrets({name: "from-file" for name in HARD})
    _write_state_config()
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


def test_an_empty_environment_value_is_overwritten_by_the_file_value(monkeypatch):
    """The half `setdefault` got wrong. A present-but-empty name resolves to
    the file's value, so boot decides the deployment is configured -- and
    then has to publish that same value, or `alembic/env.py` raises on the
    empty string and the container dies with no wizard to fix it."""
    _write_state_secrets(
        {**{name: "from-file" for name in HARD}, "AUTOPOSTER_DATABASE_URL": FAKE_DB_URL}
    )
    _write_state_config()
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", "")
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    monkeypatch.setattr(boot.os, "execv", lambda path, argv: None)

    boot.main([])

    assert os.environ["AUTOPOSTER_DATABASE_URL"] == FAKE_DB_URL


def test_a_configured_boot_execs_the_command_argv_names(monkeypatch):
    """docker-compose.yml's api service passes its uvicorn --reload line; the
    image's CMD passes nothing. That is now the only difference between the
    two boots."""
    for name in HARD:
        monkeypatch.setenv(name, "x")
    _write_state_config()
    monkeypatch.setattr(boot, "_migrate", lambda: None)
    seen: list[list[str]] = []
    monkeypatch.setattr(boot.os, "execvp", lambda file, argv: seen.append(argv))

    boot.main(["uvicorn", "autoposter.main:build", "--factory", "--reload"])

    assert seen == [["uvicorn", "autoposter.main:build", "--factory", "--reload"]]


def test_a_configured_boot_with_an_unreachable_database_still_migrates(monkeypatch):
    for name in HARD[1:]:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("AUTOPOSTER_DATABASE_URL", FAKE_DB_URL)
    _write_state_config()
    monkeypatch.setattr(db_base, "make_engine", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", _must_not_run)
    order: list[str] = []
    monkeypatch.setattr(boot, "_migrate", lambda: order.append("migrate"))
    monkeypatch.setattr(boot.os, "execv", lambda path, argv: order.append("execv"))

    boot.main([])

    assert order == ["migrate", "execv"]


def test_a_migration_that_fails_exits_instead_of_serving_the_wizard(monkeypatch):
    """What a configured deployment does while postgres is down: exactly what
    the shell line did -- exit non-zero and let the orchestrator restart it."""
    for name in HARD:
        monkeypatch.setenv(name, "x")
    _write_state_config()
    monkeypatch.setattr(boot, "_migrate", _failing_migration)
    monkeypatch.setattr(boot.uvicorn, "run", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)

    with pytest.raises(SystemExit) as caught:
        boot.main([])

    assert caught.value.code == 3


def _failing_migration() -> None:
    raise SystemExit(3)


def test_an_unconfigured_boot_runs_no_migration_and_serves_the_setup_app(monkeypatch):
    """The contract is pinned, not the collaborator: Task 2 owns the real
    `build_setup_app`, and stubbing it here keeps this commit green on its own
    so every later gate reads B + n with no standing failure for a new one to
    hide behind."""
    monkeypatch.setitem(
        sys.modules,
        "autoposter.api.setup",
        SimpleNamespace(build_setup_app=lambda: SimpleNamespace(title="autoposter setup")),
    )
    served: list[object] = []
    monkeypatch.setattr(boot, "_migrate", _must_not_run)
    monkeypatch.setattr(boot.os, "execv", _must_not_run)
    monkeypatch.setattr(db_base, "make_engine", _must_not_run)
    monkeypatch.setattr(boot.uvicorn, "run", lambda app, **kwargs: served.append(app))

    boot.main([])

    assert len(served) == 1
    assert served[0].title == "autoposter setup"


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


# --- the probe, which is now the wizard's step-2 validation only ------------


async def test_a_database_that_never_answers_fails_within_the_bound(monkeypatch):
    """A host that accepts the connection and then says nothing -- a paused
    VM, a failing-over pgbouncer, a DROP rule applied after accept. No dialect
    bounds the query, and asyncpg's 60 s connect default is incidental rather
    than chosen, so the wizard's step 2 would hold its request open with
    nothing to show for it."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    monkeypatch.setattr(db_base, "PROBE_TIMEOUT_SECONDS", 0.25)

    started = time.monotonic()
    try:
        answered, failure = await db_base.database_answers(
            f"postgresql+asyncpg://u:p@127.0.0.1:{port}/db"
        )
    finally:
        listener.close()

    assert answered is False
    assert failure == "TimeoutError"
    assert time.monotonic() - started < 5.0


async def test_the_probe_reports_a_class_name_and_never_the_url():
    answered, failure = await db_base.database_answers(FAKE_DB_URL)

    assert answered is False
    assert "row-121-db-secret" not in failure
    assert FAKE_DB_URL not in failure


# --- the two places an operator looks --------------------------------------


def test_the_state_directory_is_documented_in_both_places_an_operator_looks():
    """``.env.example`` is the compose path and ``deploy/README.md`` is the
    Kubernetes one (the tests/test_api_key.py rule). A first-start story
    documented in neither is a feature nobody can find."""
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    deploy_readme = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    assert "AUTOPOSTER_STATE_DIR" in env_example
    assert "AUTOPOSTER_STATE_DIR" in deploy_readme
    assert "autoposter-state" in deploy_readme  # the homeops PVC, by name
    assert "First-start setup" in deploy_readme
    assert "First start" in readme


def test_the_deploy_readme_no_longer_claims_migrations_always_run():
    """`deploy/README.md:3-7` said migrations run automatically at container
    startup. They now run only once the boot decision has said this deployment
    is configured, and an operator debugging a pod that came up in setup mode
    needs the document to say so."""
    text = (REPO / "deploy" / "README.md").read_text(encoding="utf-8")

    assert "autoposter.boot" in text
    assert "Migrations run automatically at container\nstartup" not in text


def test_the_compose_stack_mounts_a_private_state_volume():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    api = compose["services"]["api"]

    assert "state:/state" in api["volumes"]
    assert api["environment"]["AUTOPOSTER_STATE_DIR"] == "/state"
    assert "state" in compose["volumes"]
