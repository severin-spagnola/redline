"""
Pytest suite for sealed_gate.py — the deterministic sealed-ratchet promotion gate.

Behavior contract under test lives in SEALED_RATCHET.md. The three properties
that matter most, and are tested hardest here, are:

  * the RATCHET ASYMMETRY — target-improves AND no-previously-passing-family-
    regresses; a quiet regression elsewhere is fatal even when the target went up
    (the overfit-catch). See test_ratchet_* / test 2.
  * the TOLERANCE BOUNDARY — a passing family may drop by *exactly* tolerance
    (allowed) but not by more (blocked). The predicate is `delta < -abs(tol)`.
    See test_tolerance_boundary_* / test 6.
  * the LOWER-IS-BETTER INVERSION — with --lower-is-better, "improve" means the
    score goes DOWN and a passing family going UP beyond tolerance is a regression.
    See test_lower_is_better_* / test 7.
  * BLINDNESS OF STDOUT — the loop receives only the words PROMOTE/REJECT and the
    exit code; the raw sealed scores must never appear on stdout. See test 19-21.

The module is a single stdlib-only file (Python 3.11), NOT a package. We load it
by absolute path (relative to THIS test file) so the suite runs from any cwd,
following test_arch_gate.py's importlib pattern. sealed_gate.py does not import
arch_gate, so it loads cleanly on its own. The gate is treated as read-only:
where a real bug is found it is reported and marked xfail rather than worked
around.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load sealed_gate.py by absolute path (robust to any cwd).
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_GATE_PATH = _HERE / "sealed_gate.py"

_spec = importlib.util.spec_from_file_location("sealed_gate", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
sg = importlib.util.module_from_spec(_spec)
# register so dataclasses / __module__ resolve cleanly
sys.modules["sealed_gate"] = sg
_spec.loader.exec_module(sg)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_SH = shutil.which("sh") or ("/bin/sh" if Path("/bin/sh").exists() else None)
requires_sh = pytest.mark.skipif(_SH is None, reason="/bin/sh not available")


def ratchet(baseline, candidate, target, *,
            pass_threshold=0.5, tolerance=0.0,
            allow_target_equal=False, lower_is_better=False):
    """Thin wrapper over apply_ratchet with the module's defaults for brevity."""
    return sg.apply_ratchet(
        baseline, candidate, target,
        pass_threshold=pass_threshold, tolerance=tolerance,
        allow_target_equal=allow_target_equal, lower_is_better=lower_is_better)


def write_eval_script(tmp_path: Path, body: str, name: str = "score_sealed.sh") -> Path:
    """Write an executable /bin/sh script under tmp_path that prints to stdout.

    `body` is the shell source AFTER the shebang. The script is what the module's
    eval contract expects: it prints a JSON {family: score} object on stdout and
    may read REDLINE_SEALED=1 from its environment.
    """
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def eval_cmd_for(script: Path) -> str:
    """A shell command string that runs the script through /bin/sh explicitly.

    run_eval uses shell=True, so quoting the path keeps spaces in tmp_path safe.
    """
    return f'{_SH} "{script}"'


def echo_json_cmd(obj: dict) -> str:
    """A one-liner eval command that echoes a fixed JSON object (no script file).

    Uses printf for portability; json.dumps produces a compact object and we
    escape it for the shell with a single-quoted printf argument.
    """
    payload = json.dumps(obj)
    # single-quote for the shell; there are no single quotes in JSON of numbers/ascii keys
    return "printf '%s' '" + payload + "'"


def write_baseline(path: Path, scores: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n")


# ===========================================================================
# 0. Module smoke — public surface exists
# ===========================================================================


def test_public_surface_present():
    for name in ("run_eval", "apply_ratchet", "RatchetResult", "_refuse_in_repo", "main"):
        assert hasattr(sg, name), f"sealed_gate is missing public symbol {name!r}"


# ===========================================================================
# 1. apply_ratchet — the pure heart of the ratchet (no I/O)
# ===========================================================================


def test_target_improves_no_regression_promotes():
    """1. target improves, no family regresses -> promote=True."""
    base = {"target": 0.80, "other": 0.90}
    cand = {"target": 0.85, "other": 0.90}
    r = ratchet(base, cand, "target")
    assert r.promote is True
    assert r.regressions == []
    assert r.target_delta == pytest.approx(0.05)


def test_target_up_but_passing_family_regresses_blocks():
    """2. THE overfit-catch: target improves but a previously-PASSING family
    regresses beyond tolerance -> promote=False, and the regression is captured."""
    base = {"target": 0.80, "other": 0.90}   # 'other' is passing (>=0.5)
    cand = {"target": 0.95, "other": 0.60}   # target up a lot, 'other' quietly down
    r = ratchet(base, cand, "target", tolerance=0.0)
    assert r.promote is False, "a regression on a previously-passing family must be fatal"
    fams = [fam for (fam, _b, _c) in r.regressions]
    assert "other" in fams
    # target itself did improve — the block is purely from the ratchet asymmetry
    assert r.target_delta == pytest.approx(0.15)


def test_target_does_not_improve_blocks_even_with_no_regression():
    """3. target does NOT improve -> promote=False, even though nothing regresses."""
    base = {"target": 0.80, "other": 0.90}
    cand = {"target": 0.70, "other": 0.90}   # target down, other unchanged
    r = ratchet(base, cand, "target")
    assert r.promote is False
    assert r.regressions == []               # nothing else moved
    assert r.target_delta == pytest.approx(-0.10)


def test_target_holds_exactly_respects_allow_target_equal():
    """4. target holds exactly (delta 0): allowed only under allow_target_equal."""
    base = {"target": 0.80, "other": 0.90}
    cand = {"target": 0.80, "other": 0.90}   # exact hold
    r_allow = ratchet(base, cand, "target", allow_target_equal=True)
    assert r_allow.promote is True
    r_strict = ratchet(base, cand, "target", allow_target_equal=False)
    assert r_strict.promote is False
    assert r_strict.target_delta == pytest.approx(0.0)


def test_not_previously_passing_family_may_drop_without_blocking():
    """5. a family BELOW pass_threshold in baseline may drop further and it does
    NOT block — only previously-passing families are ratchet-protected."""
    base = {"target": 0.80, "weak": 0.30}    # weak is NOT passing (<0.5)
    cand = {"target": 0.85, "weak": 0.10}    # weak drops further
    r = ratchet(base, cand, "target", pass_threshold=0.5)
    assert r.promote is True, "an already-failing family is not protected by the ratchet"
    assert r.regressions == []


# --- 6. tolerance boundary (BOTH sides). Values are power-of-two-friendly so the
#        float subtraction is EXACT: 1.0 - 0.75 == 0.25 exactly, so "drop by
#        exactly tolerance" is unambiguous. The predicate is `delta < -abs(tol)`,
#        so a drop == tol is ALLOWED and a drop > tol is BLOCKED.


def test_tolerance_boundary_drop_exactly_tolerance_is_allowed():
    """6a. a passing family drops by EXACTLY tolerance -> allowed (not a regression)."""
    tol = 0.25
    base = {"target": 0.80, "other": 1.00}
    cand = {"target": 0.85, "other": 0.75}   # 1.00 - 0.75 == 0.25 == tol, exactly
    assert (1.00 - 0.75) == tol              # guard: exact float arithmetic
    r = ratchet(base, cand, "target", tolerance=tol)
    assert r.regressions == [], "a drop of exactly tolerance must not count as a regression"
    assert r.promote is True


def test_tolerance_boundary_drop_beyond_tolerance_blocks():
    """6b. a passing family drops by MORE than tolerance -> blocked."""
    tol = 0.25
    base = {"target": 0.80, "other": 1.00}
    cand = {"target": 0.85, "other": 0.50}   # drop 0.50 > tol
    r = ratchet(base, cand, "target", tolerance=tol)
    assert ("other", 1.00, 0.50) in r.regressions
    assert r.promote is False


# --- 7. lower_is_better inversion. Mirror of tests 1-3 (and the tolerance
#        boundary) with the direction flipped: lower score = better (e.g. error
#        rate). "Passing" is baseline <= pass_threshold; "improve" is going DOWN;
#        a passing family going UP beyond tolerance is a regression.


def test_lower_is_better_target_improves_going_down_promotes():
    """7a. (mirror of 1) target improves means it goes DOWN; nothing regresses up."""
    base = {"target": 0.40, "other": 0.10}   # both passing (<=0.5)
    cand = {"target": 0.20, "other": 0.10}   # target error dropped => better
    r = ratchet(base, cand, "target", lower_is_better=True)
    assert r.promote is True
    assert r.regressions == []
    assert r.target_delta == pytest.approx(0.20)   # signed improvement is positive


def test_lower_is_better_passing_family_goes_up_blocks():
    """7b. (mirror of 2) a passing family goes UP beyond tolerance -> regression."""
    base = {"target": 0.40, "other": 0.10}   # other passing (0.10 <= 0.5)
    cand = {"target": 0.20, "other": 0.45}   # target better, but 'other' error rose
    r = ratchet(base, cand, "target", lower_is_better=True, tolerance=0.0)
    assert r.promote is False
    fams = [fam for (fam, _b, _c) in r.regressions]
    assert "other" in fams


def test_lower_is_better_target_does_not_improve_going_up_blocks():
    """7c. (mirror of 3) target got WORSE (went up) -> blocked, nothing else moved."""
    base = {"target": 0.40, "other": 0.10}
    cand = {"target": 0.50, "other": 0.10}   # target error rose => not improved
    r = ratchet(base, cand, "target", lower_is_better=True)
    assert r.promote is False
    assert r.regressions == []
    assert r.target_delta == pytest.approx(-0.10)


def test_lower_is_better_tolerance_boundary_exact_up_is_allowed():
    """7d. (mirror of 6) under lower_is_better, a passing family rising by EXACTLY
    tolerance is allowed; rising by more is blocked. Exact float values again."""
    tol = 0.25
    base = {"target": 0.40, "other": 0.50}       # other passing (0.50 <= 0.5)
    cand_ok = {"target": 0.20, "other": 0.75}    # up exactly 0.25 == tol
    assert (0.75 - 0.50) == tol
    r_ok = ratchet(base, cand_ok, "target", lower_is_better=True, tolerance=tol)
    assert r_ok.regressions == [] and r_ok.promote is True
    cand_bad = {"target": 0.20, "other": 1.00}   # up 0.50 > tol
    r_bad = ratchet(base, cand_bad, "target", lower_is_better=True, tolerance=tol)
    assert ("other", 0.50, 1.00) in r_bad.regressions
    assert r_bad.promote is False


def test_new_target_family_treated_as_improvement():
    """8. a target family present in candidate but absent from baseline is treated
    as an improvement (from nothing) — it must NOT falsely block on the target."""
    base = {"other": 0.90}                    # no 'target' key
    cand = {"target": 0.30, "other": 0.90}    # new target family, low score even
    r = ratchet(base, cand, "target")
    assert r.promote is True, "a brand-new target family should not block on the target"
    assert r.target_delta is None             # no baseline to diff against
    assert r.regressions == []


def test_previously_passing_family_missing_from_candidate_is_regression():
    """9. a previously-passing family MISSING from candidate scores -> regression
    (blocks), and it appears in result.regressions with NaN as the candidate value."""
    base = {"target": 0.80, "other": 0.90}
    cand = {"target": 0.85}                    # 'other' dropped out entirely
    r = ratchet(base, cand, "target")
    assert r.promote is False
    missing = [(fam, b, c) for (fam, b, c) in r.regressions if fam == "other"]
    assert len(missing) == 1
    fam, b, c = missing[0]
    assert b == 0.90
    assert math.isnan(c), "a vanished family is recorded with NaN candidate score"


def test_multiple_regressions_all_captured():
    """10. every regressing previously-passing family is captured in regressions."""
    base = {"target": 0.60, "a": 0.90, "b": 0.85, "c": 0.95, "weak": 0.20}
    cand = {"target": 0.70, "a": 0.50, "b": 0.40, "c": 0.95, "weak": 0.05}
    r = ratchet(base, cand, "target", tolerance=0.0)
    fams = sorted(fam for (fam, _b, _c) in r.regressions)
    # a and b regressed; c held; weak was never passing (not protected)
    assert fams == ["a", "b"]
    assert r.promote is False


# --- extra ratchet corner: a family that IS passing and stays put does not block.


def test_passing_family_unchanged_does_not_block():
    base = {"target": 0.70, "steady": 0.88}
    cand = {"target": 0.75, "steady": 0.88}
    r = ratchet(base, cand, "target")
    assert r.promote is True and r.regressions == []


# ===========================================================================
# 2. _refuse_in_repo — blindness of the baseline storage location
# ===========================================================================


def test_refuse_baseline_inside_repo_raises(tmp_path):
    """11. baseline INSIDE repo_root with allow_in_repo=False -> SystemExit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "sealed.json"
    with pytest.raises(SystemExit):
        sg._refuse_in_repo(inside, repo, allow_in_repo=False)


def test_refuse_baseline_outside_repo_ok(tmp_path):
    """12. baseline OUTSIDE repo_root -> no raise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "sealed" / "baseline.json"   # sibling of repo, not under it
    # must not raise
    sg._refuse_in_repo(outside, repo, allow_in_repo=False)


def test_refuse_baseline_inside_repo_allowed_with_override(tmp_path):
    """13. inside repo + allow_in_repo=True -> no raise."""
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "sealed.json"
    sg._refuse_in_repo(inside, repo, allow_in_repo=True)   # must not raise


# ===========================================================================
# 3. run_eval — subprocess parsing of {family: score} JSON (uses /bin/sh)
# ===========================================================================


@requires_sh
def test_run_eval_parses_valid_json(tmp_path):
    """14. eval cmd prints valid {family: score} JSON -> dict of floats."""
    script = write_eval_script(tmp_path, 'echo \'{"axi": 0.94, "csr": 1}\'\n')
    scores = sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)
    assert scores == {"axi": 0.94, "csr": 1.0}
    assert all(isinstance(v, float) for v in scores.values()), "values coerced to float"


@requires_sh
def test_run_eval_takes_last_json_after_log_lines(tmp_path):
    """15. leading log lines THEN the JSON -> module takes the last {...} and parses."""
    script = write_eval_script(
        tmp_path,
        'echo "loading sealed corpus..."\n'
        'echo "scored 3 families"\n'
        'echo \'{"axi": 0.5, "fifo": 0.77}\'\n',
    )
    scores = sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)
    assert scores == {"axi": 0.5, "fifo": 0.77}


@requires_sh
def test_run_eval_nonzero_exit_raises(tmp_path):
    """16. eval cmd exits nonzero -> SystemExit."""
    script = write_eval_script(tmp_path, 'echo "boom" 1>&2\nexit 3\n')
    with pytest.raises(SystemExit):
        sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)


@requires_sh
def test_run_eval_non_json_raises(tmp_path):
    """17a. eval cmd prints non-JSON -> SystemExit."""
    script = write_eval_script(tmp_path, 'echo "not json at all"\n')
    with pytest.raises(SystemExit):
        sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)


@requires_sh
def test_run_eval_non_dict_json_raises(tmp_path):
    """17b. eval cmd prints valid JSON that is not a dict -> SystemExit."""
    script = write_eval_script(tmp_path, 'echo "[1, 2, 3]"\n')
    with pytest.raises(SystemExit):
        sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)


@requires_sh
def test_run_eval_dict_with_non_number_value_raises(tmp_path):
    """17c. dict whose value is not a number -> SystemExit (contract: {family: number})."""
    script = write_eval_script(tmp_path, 'echo \'{"axi": "high"}\'\n')
    with pytest.raises(SystemExit):
        sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)


@requires_sh
def test_run_eval_sets_REDLINE_SEALED_env(tmp_path):
    """18. run_eval sets REDLINE_SEALED=1 in the child env — the script observes it."""
    # The script reports whether REDLINE_SEALED is present in its environment.
    script = write_eval_script(
        tmp_path,
        'echo "{\\"seen_env\\": $([ -n \\"$REDLINE_SEALED\\" ] && echo 1 || echo 0)}"\n',
    )
    # sanity: ensure the parent process does NOT already have it set, so a pass is meaningful
    assert "REDLINE_SEALED" not in os.environ
    scores = sg.run_eval(eval_cmd_for(script), tmp_path, timeout=30)
    assert scores == {"seen_env": 1.0}, "the eval child must see REDLINE_SEALED=1"


# ===========================================================================
# 4. main / exit codes — end-to-end with a real tmp repo, a script fixture, and
#    a baseline file kept OUTSIDE the repo. The blindness property (no scores on
#    stdout) is asserted in every branch.
# ===========================================================================

# Distinctive score strings that must NOT leak to stdout. Chosen so their digit
# runs do not coincide with a family count ("2 families") or a tmp path segment.
_SCORE_TARGET = 0.937
_SCORE_OTHER = 0.421
_SCORE_STRINGS = ("0.937", "937", "0.421", "421")


def _assert_no_scores_in(text: str) -> None:
    for s in _SCORE_STRINGS:
        assert s not in text, f"blindness violated: score fragment {s!r} appeared in stdout:\n{text!r}"


def _repo_and_baseline(tmp_path: Path):
    """A repo dir and a baseline path that lives OUTSIDE it (sealed storage)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    baseline = tmp_path / "sealed_store" / "baseline.json"   # sibling of repo
    return repo, baseline


@requires_sh
def test_main_init_baseline_writes_and_is_blind(tmp_path, capsys):
    """19. --init-baseline writes the baseline file, returns 0, and does NOT print
    the score numbers to stdout (blindness)."""
    repo, baseline = _repo_and_baseline(tmp_path)
    script = write_eval_script(
        tmp_path,
        f'echo \'{{"target": {_SCORE_TARGET}, "other": {_SCORE_OTHER}}}\'\n',
    )
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
        "--init-baseline",
    ])
    assert rc == 0
    # the baseline file was written with the real scores
    assert baseline.is_file()
    stored = json.loads(baseline.read_text())
    assert stored == {"target": _SCORE_TARGET, "other": _SCORE_OTHER}
    # ... but stdout must NOT contain the scores (only a count + path).
    out = capsys.readouterr().out
    _assert_no_scores_in(out)


@requires_sh
def test_main_promote_returns_0_and_prints_only_PROMOTE(tmp_path, capsys):
    """20a. a PROMOTE case -> main returns 0, stdout is exactly 'PROMOTE', and the
    raw scores never appear on stdout (blindness)."""
    repo, baseline = _repo_and_baseline(tmp_path)
    # baseline: target 0.80 passing, other 0.90 passing
    write_baseline(baseline, {"target": 0.80, "other": 0.90})
    # candidate: target up to _SCORE_TARGET (0.937), other holds at 0.90 (no regression)
    script = write_eval_script(
        tmp_path, f'echo \'{{"target": {_SCORE_TARGET}, "other": 0.90}}\'\n')
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "PROMOTE"
    _assert_no_scores_in(out)


@requires_sh
def test_main_reject_returns_10_and_prints_only_REJECT(tmp_path, capsys):
    """20b. a REJECT case -> main returns 10, stdout is exactly 'REJECT', and the
    raw scores never appear on stdout (blindness) — the overfit-catch through main."""
    repo, baseline = _repo_and_baseline(tmp_path)
    # baseline: target 0.80, other 0.90 (passing)
    write_baseline(baseline, {"target": 0.80, "other": 0.90})
    # candidate: target UP to 0.937 but 'other' quietly regresses to _SCORE_OTHER (0.421)
    script = write_eval_script(
        tmp_path,
        f'echo \'{{"target": {_SCORE_TARGET}, "other": {_SCORE_OTHER}}}\'\n')
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
    ])
    assert rc == 10
    out = capsys.readouterr().out
    assert out.strip() == "REJECT"
    _assert_no_scores_in(out)


@requires_sh
def test_main_update_baseline_on_promote_rewrites_baseline(tmp_path, capsys):
    """21. --update-baseline on a PROMOTE rewrites the baseline to the candidate."""
    repo, baseline = _repo_and_baseline(tmp_path)
    write_baseline(baseline, {"target": 0.80, "other": 0.90})
    new_scores = {"target": _SCORE_TARGET, "other": 0.92}
    script = write_eval_script(
        tmp_path,
        f'echo \'{{"target": {_SCORE_TARGET}, "other": 0.92}}\'\n')
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
        "--update-baseline",
    ])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "PROMOTE"
    # baseline now equals the candidate scores
    assert json.loads(baseline.read_text()) == new_scores


@requires_sh
def test_main_no_update_baseline_leaves_baseline_unchanged(tmp_path, capsys):
    """21b. WITHOUT --update-baseline, a PROMOTE must not rewrite the baseline."""
    repo, baseline = _repo_and_baseline(tmp_path)
    original = {"target": 0.80, "other": 0.90}
    write_baseline(baseline, original)
    script = write_eval_script(
        tmp_path, f'echo \'{{"target": {_SCORE_TARGET}, "other": 0.90}}\'\n')
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
    ])
    assert rc == 0
    assert json.loads(baseline.read_text()) == original


@requires_sh
def test_main_update_baseline_on_reject_does_not_rewrite(tmp_path, capsys):
    """21c. --update-baseline must NOT rewrite on a REJECT (update is gated on PROMOTE)."""
    repo, baseline = _repo_and_baseline(tmp_path)
    original = {"target": 0.80, "other": 0.90}
    write_baseline(baseline, original)
    # target up but 'other' regresses -> REJECT
    script = write_eval_script(
        tmp_path, f'echo \'{{"target": {_SCORE_TARGET}, "other": {_SCORE_OTHER}}}\'\n')
    rc = sg.main([
        "--repo-root", str(repo),
        "--eval-cmd", eval_cmd_for(script),
        "--target", "target",
        "--baseline-file", str(baseline),
        "--update-baseline",
    ])
    assert rc == 10
    assert json.loads(baseline.read_text()) == original, "REJECT must leave the baseline intact"


@requires_sh
def test_main_refuses_baseline_inside_repo(tmp_path):
    """main wires _refuse_in_repo: a baseline inside the repo must abort (SystemExit)."""
    repo, _ = _repo_and_baseline(tmp_path)
    inside = repo / "sealed.json"
    script = write_eval_script(tmp_path, 'echo \'{"target": 0.9}\'\n')
    with pytest.raises(SystemExit):
        sg.main([
            "--repo-root", str(repo),
            "--eval-cmd", eval_cmd_for(script),
            "--target", "target",
            "--baseline-file", str(inside),
            "--init-baseline",
        ])


@requires_sh
def test_main_missing_baseline_without_init_raises(tmp_path):
    """A ratchet run with no baseline present (and no --init-baseline) aborts."""
    repo, baseline = _repo_and_baseline(tmp_path)   # baseline path does not exist yet
    script = write_eval_script(tmp_path, 'echo \'{"target": 0.9}\'\n')
    with pytest.raises(SystemExit):
        sg.main([
            "--repo-root", str(repo),
            "--eval-cmd", eval_cmd_for(script),
            "--target", "target",
            "--baseline-file", str(baseline),
        ])
