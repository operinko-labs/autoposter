"""Roadmap row 208's rule, enforced: no test may order two wall-clock readings.

The rule, verbatim from the row: no test in this suite may assert an ORDERING,
or a tolerance under about three seconds, between two wall-clock readings taken
at different instants -- database clock or host clock alike. The justification is
measured, not assumed: docs/research/dev-clock-step/README.md records a composite
-2.705 s step every ~30 s of real time on this development host, with Postgres's
clock_timestamp() and the container's time.time() retreating by the same amount
at the same sample. Exposure is interval / 30 s; the step magnitude supplies the
condition, inverting any ordering whose interval is under 2.705 s.

The row closed with "no outstanding code". That was true of the six ASSERTIONS
the sweep audited and false of the suite: ``_CLAIM_SQL`` (queue/jobs.py:16-35)
compares exactly the same two readings, ``enqueue`` commits before ``claim``
evaluates it, and ~50 test call sites sat under that window. Those are fixed in
tests/queue_support.py's idiom. This file is the half that keeps them fixed.

Four rules, chosen because each is cheap and has near-zero false positives:

1. A HOST-clock reading inside a comparison. ``func.now()`` is deliberately not
   a host-clock reading -- it is a SQL expression compiled into the statement,
   evaluated by Postgres, and immune inside one transaction.
2. A ``timedelta`` under three seconds as an operand of a comparison. Three
   seconds is the step; a tolerance smaller than the step cannot survive it.
3. An ORDERING between two stored clock readings, with a six-entry allowlist.
   Equalities are not flagged: same-transaction identity is precisely what the
   sweep converted the flaky orderings INTO, and flagging it would flag the fix.
4. ``run_after`` stamped from a bare ``now()`` read in a test -- the rule that
   keeps tests/queue_support.py from being quietly undone.

What this guard CANNOT catch, stated plainly so nobody mistakes it for total:
the claim-predicate family that produced nine of the eleven sightings, because
there is no comparison in the test at all -- the comparison lives inside
``_CLAIM_SQL``. It also does not read subscripts (``run_afters[0] <= db_now``,
test_queue.py:566) or SQL expressions built with ``func`` inside a ``select``
(test_queue.py:508, test_scheduler_stale_reclaim_job.py:67), both of which are
deliberate server-side idioms. That is why the guard ships WITH the fix rather
than instead of it.

Scope is ``tests/*.py``, non-recursively: tests/oracle/ is a vendored
transcription of upstream code and is not ours to discipline.
"""

import ast
import pathlib
import re

import pytest

TESTS = pathlib.Path(__file__).resolve().parent

# Rule 1: readings of the HOST clock. `func.now()` is absent on purpose.
HOST_CLOCK_RECEIVERS = frozenset({"datetime", "dt", "time"})
HOST_CLOCK_ATTRS = frozenset({"now", "utcnow", "time"})

# Rule 2: the measured step is 2.705 s, so three seconds is the floor.
MIN_TOLERANCE_SECONDS = 3

# Rule 3: what counts as a stored clock reading. `run_after` earns its place
# beside the `_at` columns -- it is the queue's own due horizon and the column
# every sighting in rows 119 and 193 turned on.
CLOCK_NAME = re.compile(r"^(?:db_now|now)$|_now$")
ORDERINGS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)

# Rule 3's allowlist, keyed by (module, enclosing test) rather than by line so
# an edit above one of them does not silently retarget the entry. Six entries,
# each read at this HEAD; a seventh needs the same treatment, not an extra line.
RULE_3_ALLOWLIST = {
    (
        "test_api_full_pass.py",
        "test_every_job_the_pass_enqueued_is_inside_its_own_window",
    ): (
        "immune by construction: the run row and the job batch are inserted in "
        "ONE transaction, so created_at and started_at are the same "
        "transaction_timestamp() -- db/models.py:1026-1029 states the invariant "
        "in the column's own comment"
    ),
    (
        "test_run_history.py",
        "test_closing_a_run_stamps_the_finish_the_status_and_the_detail",
    ): (
        "immune by construction: open_run and close_run "
        "(scheduler/run_history.py:59-88) run on one session with no commit "
        "between them, so both stamps are one transaction_timestamp()"
    ),
    ("test_queue.py", "test_failure_reschedules_with_backoff"): (
        "safe by direction: asserts run_after > db_now + 5s, and a "
        "backwards-only step SHRINKS db_now, which only makes it easier"
    ),
    ("test_queue.py", "test_reenqueuing_a_deferred_key_wakes_it_instead_of_duplicating"): (
        "safe by direction: asserts the woken row is already due "
        "(run_after <= db_now), and a backwards step shrinks db_now by at most "
        "2.705 s against a wake that stamped now() -- the residual is the "
        "subject, since the wake's whole contract is 'due immediately'"
    ),
    ("test_queue.py", "test_reenqueuing_a_deferred_key_with_a_delay_honours_it"): (
        "safe by direction: asserts run_after > db_now + 5s after a 30s delay"
    ),
    ("test_routes.py", "test_jobs_are_delayed_by_the_settle_window"): (
        "safe by direction: asserts run_after > db_now + 20s"
    ),
}


def _modules():
    """Every top-level test module, parsed. Non-recursive: oracle/ is vendored."""
    for path in sorted(TESTS.glob("*.py")):
        yield path.name, ast.parse(path.read_text(encoding="utf-8"))


def _enclosing(tree, lineno):
    """The innermost function containing ``lineno``, or None at module level."""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno <= lineno <= (node.end_lineno or node.lineno):
            if best is None or node.lineno > best.lineno:
                best = node
    return best.name if best else None


def _comparisons(tree):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]


def _calls(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _called_name(call):
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", None)


def rule_1_host_clock_in_a_comparison(module, tree):
    found = []
    for comparison in _comparisons(tree):
        for call in _calls(comparison):
            func = call.func
            if not isinstance(func, ast.Attribute) or func.attr not in HOST_CLOCK_ATTRS:
                continue
            receiver = func.value
            name = None
            if isinstance(receiver, ast.Name):
                name = receiver.id
            elif isinstance(receiver, ast.Attribute):
                name = receiver.attr
            if name in HOST_CLOCK_RECEIVERS:
                found.append((module, comparison.lineno, f"{name}.{func.attr}()"))
    return found


def rule_2_narrow_tolerance_in_a_comparison(module, tree):
    found = []
    scale = {"seconds": 1, "milliseconds": 1e-3, "microseconds": 1e-6}
    for comparison in _comparisons(tree):
        for call in _calls(comparison):
            if _called_name(call) != "timedelta":
                continue
            for keyword in call.keywords:
                if keyword.arg not in scale or not isinstance(keyword.value, ast.Constant):
                    continue
                raw = keyword.value.value
                if not isinstance(raw, (int, float)):
                    continue
                if raw * scale[keyword.arg] < MIN_TOLERANCE_SECONDS:
                    found.append((module, comparison.lineno, f"timedelta({keyword.arg}={raw})"))
    return found


def _clock_ref(node):
    """The name of the stored clock reading this operand holds, or None."""
    if isinstance(node, ast.Attribute):
        if node.attr.endswith("_at") or node.attr == "run_after":
            return node.attr
        if CLOCK_NAME.search(node.attr):
            return node.attr
    if isinstance(node, ast.Name) and CLOCK_NAME.search(node.id):
        return node.id
    if isinstance(node, ast.BinOp):
        return _clock_ref(node.left) or _clock_ref(node.right)
    return None


def rule_3_ordered_clock_readings(module, tree):
    found = []
    for comparison in _comparisons(tree):
        if not any(isinstance(op, ORDERINGS) for op in comparison.ops):
            continue
        refs = [_clock_ref(o) for o in [comparison.left, *comparison.comparators]]
        named = [r for r in refs if r]
        if len(named) < 2:
            continue
        found.append(
            (module, comparison.lineno, " vs ".join(named), _enclosing(tree, comparison.lineno))
        )
    return found


def _stamps_a_bare_now(value):
    """True when ``value`` reads a clock and does no arithmetic on it."""
    if any(isinstance(n, ast.BinOp) for n in ast.walk(value)):
        return False
    for call in ast.walk(value):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
            if call.func.attr in ("now", "utcnow"):
                return True
    return False


def rule_4_run_after_from_a_bare_now(module, tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name == "run_after" and _stamps_a_bare_now(value):
                found.append((module, node.lineno, "run_after = <a bare now() read>"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Anchored on what FOLLOWS the read, so an UPDATE that backdates
            # (`run_after = now() - interval '1 hour'`) and one that pushes the
            # horizon out (test_queue.py:430-434) are both left alone -- only a
            # stamp with no arithmetic at all is the defect. Adjacent string
            # literals are one Constant by the time this walks them, so a
            # multi-line UPDATE is matched as the single statement it is.
            if re.search(
                r"\bset\b[^;]*\brun_after\s*=\s*now\(\)\s*(?:,|where\b|$)", node.value, re.I
            ):
                found.append((module, node.lineno, "a bare now() stamped onto run_after in SQL"))
    return found


ADVICE = (
    "Roadmap row 208: this development host's clock steps backwards 2.705 s "
    "every ~30 s (docs/research/dev-clock-step/). Make the job due through "
    "tests/queue_support.py, or restructure onto a same-transaction identity."
)


def test_no_test_reads_the_host_clock_inside_a_comparison():
    hits = [
        h for module, tree in _modules() for h in rule_1_host_clock_in_a_comparison(module, tree)
    ]
    assert hits == [], f"host-clock readings compared at {hits}. {ADVICE}"


def test_no_comparison_is_bounded_more_narrowly_than_the_clock_step():
    hits = [
        h
        for module, tree in _modules()
        for h in rule_2_narrow_tolerance_in_a_comparison(module, tree)
    ]
    assert hits == [], (
        f"tolerances under {MIN_TOLERANCE_SECONDS}s in a comparison at {hits}. {ADVICE}"
    )


def test_no_test_orders_two_clock_readings_outside_the_allowlist():
    hits = [
        hit
        for module, tree in _modules()
        for hit in rule_3_ordered_clock_readings(module, tree)
        if (hit[0], hit[3]) not in RULE_3_ALLOWLIST
    ]
    assert hits == [], (
        f"orderings between two clock readings at {hits}. {ADVICE} If the "
        "ordering IS the behaviour under test, add it to RULE_3_ALLOWLIST with "
        "a written reason -- never a bare entry."
    )


def test_no_test_stamps_run_after_from_a_bare_now():
    hits = [
        h for module, tree in _modules() for h in rule_4_run_after_from_a_bare_now(module, tree)
    ]
    assert hits == [], (
        f"run_after stamped at exactly now() at {hits}. That leaves zero margin "
        f"for the step. Use tests/queue_support.py's make_due. {ADVICE}"
    )


BAD_SNIPPETS = {
    "rule 1": ("assert row.fetched_at > datetime.now(UTC)\n", rule_1_host_clock_in_a_comparison),
    "rule 2": (
        "assert later - earlier < timedelta(seconds=1)\n",
        rule_2_narrow_tolerance_in_a_comparison,
    ),
    "rule 3": (
        "def test_x():\n    assert job.run_after <= db_now\n",
        rule_3_ordered_clock_readings,
    ),
    "rule 4": ("job.run_after = func.now()\n", rule_4_run_after_from_a_bare_now),
}


@pytest.mark.parametrize("rule", sorted(BAD_SNIPPETS))
def test_each_rule_fires_on_a_deliberately_bad_snippet(rule):
    """A guard that cannot fail guards nothing.

    Every rule above asserts an empty list against a tree that is already clean,
    so each one passes vacuously if its detector is broken. These four snippets
    are the proof that it is not.
    """
    source, detector = BAD_SNIPPETS[rule]
    assert detector("<snippet>", ast.parse(source)), f"{rule} did not fire on {source!r}"


def test_the_allowlist_has_no_stale_entries():
    """Six reasoned exemptions, not a rot farm.

    An entry whose test was renamed, deleted, or restructured onto a
    same-transaction identity must leave with it -- otherwise the allowlist
    grows monotonically and the rule quietly stops meaning anything.
    """
    live = {
        (module, where)
        for module, tree in _modules()
        for _m, _lineno, _what, where in rule_3_ordered_clock_readings(module, tree)
    }
    stale = sorted(set(RULE_3_ALLOWLIST) - live)
    assert stale == [], f"RULE_3_ALLOWLIST entries no longer trip rule 3: {stale}. Remove them."
