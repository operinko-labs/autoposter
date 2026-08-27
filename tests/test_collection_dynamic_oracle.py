"""THE DYNAMIC-COLLECTIONS ORACLE -- phase 10a's acceptance for keys and titles.

``src/autoposter/collections/dynamic_keys.py`` and
``src/autoposter/collections/dynamic_titles.py`` are TRANSCRIPTIONS of Kometa's
``dynamic_collections`` expansion pass, and the danger is the one 9b's search
oracle exists for: an addon member that keeps its own collection, or a key name
whose prefix was stripped when upstream would not have stripped it, produces a
full, plausible, WRONG family. Every other test in this phase asserts the
transcription against itself.

This file asserts it against Kometa. The driver is
``tests/oracle/10a/kometa_dynamic.py`` -- under ``tests/``, not under
``.superpowers/``, because this test READS it and ``.superpowers/`` is
gitignored; a file an assertion depends on has to be in the checkout that runs
the assertion. It imports nothing from this repository, and this file imports
nothing from it: the enumerations both need are shared BY VALUE, and a test
below holds the two copies equal.

Do not edit a golden here to make a test pass: if ours differs, ours is wrong.
"""
import ast
import json
from pathlib import Path

import pytest

from autoposter.collections.dynamic_keys import derive_keys

ORACLE_DRIVER = Path(__file__).parent / "oracle" / "10a" / "kometa_dynamic.py"

# COPIES of the driver's enumerations, shared by value. The next test holds
# them equal.
RATINGS = [
    ("G", "G"), ("PG", "PG"), ("PG-13", "PG-13"), ("R", "R"),
    ("NC-17", "NC-17"), ("Unrated", "Unrated"),
]
DECADES = [("1980", "1980s"), ("1990", "1990s"), ("2000", "2000s")]

KEY_CASES = [
    ("plain", RATINGS, {}),
    ("cs-shaped", RATINGS, {
        "include": ["Kids", "Teens"],
        "addons": {"Kids": ["G", "PG"], "Teens": ["PG-13", "R"]},
    }),
    ("custom-keys-false", RATINGS, {
        "addons": {"Kids": ["G", "PG"]}, "custom_keys": False,
    }),
    ("addon-key-the-library-has", RATINGS, {"addons": {"PG": ["G"]}}),
    ("addon-list-holds-its-own-key", RATINGS, {
        "addons": {"Teens": ["Teens", "PG-13", "R"]},
    }),
    ("exclude-by-value", DECADES, {"exclude": ["1990s"]}),
    ("include-names-a-missing-key", DECADES, {"include": ["1980", "1970"]}),
    ("integer-keys", [("12", "12"), ("16", "16"), ("18", "18")], {
        "include": [12, 16], "addons": {12: [16]},
    }),
    ("addon-key-excluded-by-value", DECADES, {
        "exclude": ["1990s"], "addons": {"1990": ["1980"]},
    }),
    ("custom-keys-false-with-a-key-the-library-has", RATINGS, {
        "addons": {"PG": ["G"]}, "custom_keys": False,
    }),
    ("falsy-include-elements", [("", ""), ("0", "0"), ("PG", "PG")], {
        "include": ["PG", "", 0],
    }),
]

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``tests/oracle/10a/kometa_dynamic.py``, Kometa v2.4.8's ``meta.py`` key
# derivation transcribed standalone. The raw run is in the Task 3 report.
KOMETA_KEYS = {
    "plain": {
        "keys": [
            {"key": "G", "value": "G", "values": ["G"]},
            {"key": "PG", "value": "PG", "values": ["PG"]},
            {"key": "PG-13", "value": "PG-13", "values": ["PG-13"]},
            {"key": "R", "value": "R", "values": ["R"]},
            {"key": "NC-17", "value": "NC-17", "values": ["NC-17"]},
            {"key": "Unrated", "value": "Unrated", "values": ["Unrated"]},
        ],
        "other_keys": [],
    },
    "cs-shaped": {
        "keys": [
            {"key": "Kids", "value": "Kids", "values": ["G", "PG"]},
            {"key": "Teens", "value": "Teens", "values": ["PG-13", "R"]},
        ],
        "other_keys": ["NC-17", "Unrated"],
    },
    "custom-keys-false": {
        "keys": [
            {"key": "PG-13", "value": "PG-13", "values": ["PG-13"]},
            {"key": "R", "value": "R", "values": ["R"]},
            {"key": "NC-17", "value": "NC-17", "values": ["NC-17"]},
            {"key": "Unrated", "value": "Unrated", "values": ["Unrated"]},
            {"key": "G", "value": "G", "values": ["G"]},
            {"key": "PG", "value": "PG", "values": ["PG"]},
        ],
        "other_keys": [],
    },
    "addon-key-the-library-has": {
        "keys": [
            {"key": "PG", "value": "PG", "values": ["PG", "G"]},
            {"key": "PG-13", "value": "PG-13", "values": ["PG-13"]},
            {"key": "R", "value": "R", "values": ["R"]},
            {"key": "NC-17", "value": "NC-17", "values": ["NC-17"]},
            {"key": "Unrated", "value": "Unrated", "values": ["Unrated"]},
        ],
        "other_keys": [],
    },
    "addon-list-holds-its-own-key": {
        "keys": [
            {"key": "G", "value": "G", "values": ["G"]},
            {"key": "PG", "value": "PG", "values": ["PG"]},
            {"key": "NC-17", "value": "NC-17", "values": ["NC-17"]},
            {"key": "Unrated", "value": "Unrated", "values": ["Unrated"]},
            {"key": "Teens", "value": "Teens", "values": ["PG-13", "R"]},
        ],
        "other_keys": [],
    },
    "exclude-by-value": {
        "keys": [
            {"key": "1980", "value": "1980s", "values": ["1980"]},
            {"key": "2000", "value": "2000s", "values": ["2000"]},
        ],
        "other_keys": [],
    },
    "include-names-a-missing-key": {
        "keys": [{"key": "1980", "value": "1980s", "values": ["1980"]}],
        "other_keys": ["1990", "2000"],
    },
    "integer-keys": {
        "keys": [{"key": "12", "value": "12", "values": ["12", "16"]}],
        "other_keys": ["18"],
    },
    "addon-key-excluded-by-value": {
        "keys": [{"key": "2000", "value": "2000s", "values": ["2000"]}],
        "other_keys": [],
    },
    "custom-keys-false-with-a-key-the-library-has": {
        "keys": [
            {"key": "PG", "value": "PG", "values": ["PG", "G"]},
            {"key": "PG-13", "value": "PG-13", "values": ["PG-13"]},
            {"key": "R", "value": "R", "values": ["R"]},
            {"key": "NC-17", "value": "NC-17", "values": ["NC-17"]},
            {"key": "Unrated", "value": "Unrated", "values": ["Unrated"]},
        ],
        "other_keys": [],
    },
    "falsy-include-elements": {
        "keys": [
            {"key": "0", "value": "0", "values": ["0"]},
            {"key": "PG", "value": "PG", "values": ["PG"]},
        ],
        "other_keys": [""],
    },
}


def _ours(pairs, options):
    derived = derive_keys(
        pairs,
        include=options.get("include", ()),
        exclude=options.get("exclude", ()),
        addons=options.get("addons"),
        custom_keys=options.get("custom_keys", True),
    )
    return {
        "keys": [
            {"key": k.key, "value": k.value, "values": list(k.values)}
            for k in derived.keys
        ],
        "other_keys": list(derived.other_keys),
    }


@pytest.mark.parametrize(
    ("name", "pairs", "options"), KEY_CASES, ids=[c[0] for c in KEY_CASES]
)
def test_our_keys_are_kometas(name, pairs, options):
    assert _ours(pairs, options) == KOMETA_KEYS[name]


def test_the_oracles_enumerations_match_this_files_copies():
    """The driver imports nothing from here and this file imports nothing from
    there, so the two enumerations are shared by VALUE. This reads the driver
    as text and compares the literals -- the only coupling that does not break
    the isolation. Anchored to ``__file__``, not to the working directory: a
    guarantee that can quietly stop applying is not a guarantee."""
    tree = ast.parse(ORACLE_DRIVER.read_text(encoding="utf-8"))
    literals = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("RATINGS", "DECADES")
    }
    assert literals["RATINGS"] == [list(pair) for pair in RATINGS] or \
        literals["RATINGS"] == RATINGS
    assert literals["DECADES"] == [list(pair) for pair in DECADES] or \
        literals["DECADES"] == DECADES


# --- the title half ----------------------------------------------------------

TITLE_CASES = [
    ("general-default", "Horror", "Horror", "Movie",
     "Top <<key_name>> <<library_type>>s", {}),
    ("general-default-show", "Drama", "Drama", "Show",
     "Top <<key_name>> <<library_type>>s", {}),
    ("decade", "1980", "1980s", "Movie",
     "Best <<library_type>>s of the <<key_name>>", {}),
    ("both-library-type-tokens", "5", "5", "Movie",
     "<<key_name>> <<library_typeU>>s for a <<library_type>> library", {}),
    ("key-name-override-suppresses-the-strip", "BBC One", "BBC One", "Show",
     "Top <<key_name>> <<library_type>>s",
     {"key_name_override": {"BBC One": "the BBC"},
      "remove_prefix": ["BBC ", "the "]}),
    ("prefix-and-suffix", "The Studio Ltd", "The Studio Ltd", "Movie",
     "Top <<key_name>> <<library_type>>s",
     {"remove_prefix": ["The "], "remove_suffix": [" Ltd"]}),
    ("title-override", "R", "R", "Movie", "Top <<key_name>> <<library_type>>s",
     {"title_override": {"R": "Grown-Up Movies"}}),
    ("title-token", "1990", "1990s", "Movie", "<<title>> Cinema", {}),
]

# KOMETA'S OWN ANSWERS for the titles, pinned as data. Same driver, same run.
KOMETA_TITLES = {
    "general-default": {"key_name": "Horror", "title": "Top Horror movies"},
    "general-default-show": {"key_name": "Drama", "title": "Top Drama shows"},
    "decade": {"key_name": "1980s", "title": "Best movies of the 1980s"},
    "both-library-type-tokens": {
        "key_name": "5", "title": "5 Movies for a movie library",
    },
    "key-name-override-suppresses-the-strip": {
        "key_name": "the BBC", "title": "Top the BBC shows",
    },
    "prefix-and-suffix": {"key_name": "Studio", "title": "Top Studio movies"},
    "title-override": {"key_name": "R", "title": "Grown-Up Movies"},
    "title-token": {"key_name": "1990s", "title": "1990s Cinema"},
}


@pytest.mark.parametrize(
    ("name", "key", "value", "library_type", "title_format", "options"),
    TITLE_CASES, ids=[c[0] for c in TITLE_CASES],
)
def test_our_titles_are_kometas(name, key, value, library_type, title_format, options):
    from autoposter.collections.dynamic_keys import DerivedKeys, DynamicKey
    from autoposter.collections.dynamic_titles import family_titles

    derived = DerivedKeys(
        keys=(DynamicKey(key=key, value=value, values=(key,)),),
        other_keys=(),
    )
    titled = family_titles(
        derived, library_type=library_type, title_format=title_format, **options
    )
    assert len(titled) == 1
    assert {"key_name": titled[0].key_name, "title": titled[0].title} == \
        KOMETA_TITLES[name]


def test_the_driver_still_produces_the_pinned_answers():
    """The goldens are data and the driver that produced them is tracked,
    reviewable and editable -- so it can drift from them with nothing noticing.

    Running the driver HERE does not violate "never compare ours against the
    driver at test time": the assertion above still runs against the pinned
    text, and this one never touches our code. Run in-process rather than
    marked slow: the driver imports only the standard library, opens no socket
    and reads no clock."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("kometa_dynamic_driver", ORACLE_DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)

    for (name, pairs, options), (pinned_name, pinned) in zip(
        driver.KEY_CASES, KOMETA_KEYS.items(), strict=True
    ):
        assert name == pinned_name
        assert json.loads(json.dumps(driver.derive(pairs, **options))) == pinned, name

    for case, (pinned_name, pinned) in zip(
        driver.TITLE_CASES, KOMETA_TITLES.items(), strict=True
    ):
        name, key, value, library_type, title_format, options = case
        assert name == pinned_name
        assert driver.key_name_and_title(
            key, value, library_type=library_type,
            title_format=title_format, **options,
        ) == pinned, name
