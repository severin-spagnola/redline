#!/usr/bin/env python3
"""
sealed_gate.py — the generic sealed-ratchet promotion gate (see SEALED_RATCHET.md).

This is the ONLY layer of the anti-overfit stack that measures *generalization*.
It is deterministic, project-agnostic, and — critically — it returns to the
calling loop only a BINARY promote/reject, never the scores. The loop can adapt
to the binary verdict (that's fine — it means "find a better change"); it must
never see the magnitude, or it will optimize against the held-out set (Goodhart).

Held-out evaluation + no-regression ratchets are standard rigorous practice
(private-leaderboard eval, train/val/test discipline, CI regression gates). Only
two things are project-specific and you supply them:
  * the EVAL COMMAND that scores a candidate on the sealed set, and
  * WHICH family is the declared target of this change.
Everything else — running it loop-blind, comparing to baseline, the ratchet, the
binary return — is generic and lives here.

## The eval contract

You provide `--eval-cmd` (e.g. `./score_sealed.sh`). redline runs it and expects
it to print a JSON object of {family_name: score} on stdout, e.g.:

    {"axi_ordering": 0.94, "csr_conservation": 0.88, "fifo_causality": 0.77}

Higher score = better (precision/recall/catch-rate — redline doesn't care what it
means, only that up is good; use --lower-is-better to invert). redline runs this
on the SEALED set; the command MUST read the held-out data from a location the
coding loop cannot access. redline sets `REDLINE_SEALED=1` in the eval env so your
script can select the sealed corpus.

## The ratchet

A candidate is PROMOTED iff BOTH:
  1. the declared --target family improves (or holds, with --allow-target-equal), and
  2. NO family that was PASSING in the baseline regresses beyond --tolerance.

"Passing" = baseline score >= --pass-threshold. A regression on any
previously-passing family REJECTS, even if the target improved. That asymmetry is
the whole point: overfit edits read as "up here, quietly down there".

## Blindness

The baseline scores live in --baseline-file, which you MUST keep OUTSIDE the
loop's readable workspace (redline refuses to write it under the repo by default;
override with --allow-in-repo if you know what you're doing). The loop receives
this tool's EXIT CODE and the words PROMOTE/REJECT — nothing else. Do not pipe the
--verbose score breakdown into any channel the loop reads; --verbose is for a
human reviewing out-of-band.

Exit codes: 0 = PROMOTE, 10 = REJECT (ratchet failed), 2 = usage/eval error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def run_eval(eval_cmd: str, repo_root: Path, timeout: int) -> Dict[str, float]:
    """Run the project's eval command on the sealed set; parse {family: score}."""
    env = dict(os.environ)
    env["REDLINE_SEALED"] = "1"   # the script selects the held-out corpus on this
    try:
        out = subprocess.run(
            eval_cmd, shell=True, cwd=str(repo_root), env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(f"[sealed-gate] ERROR: eval command timed out after {timeout}s")
    if out.returncode != 0:
        raise SystemExit(f"[sealed-gate] ERROR: eval command failed (exit {out.returncode}):\n{out.stderr.strip()[:500]}")
    raw = out.stdout.strip()
    start = raw.rfind("{")   # tolerate leading log lines; take the last JSON object
    try:
        scores = json.loads(raw[start:]) if start >= 0 else json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise SystemExit(f"[sealed-gate] ERROR: eval command did not print a JSON "
                         f"{{family: score}} object. Got:\n{raw[:300]}")
    if not isinstance(scores, dict) or not all(isinstance(v, (int, float)) for v in scores.values()):
        raise SystemExit("[sealed-gate] ERROR: eval output must be {family: number}.")
    return {k: float(v) for k, v in scores.items()}


@dataclass
class RatchetResult:
    promote: bool
    reasons: List[str]          # human-facing, for --verbose ONLY (never to the loop)
    target_delta: Optional[float]
    regressions: List[Tuple[str, float, float]]   # (family, baseline, candidate)


def apply_ratchet(baseline: Dict[str, float], candidate: Dict[str, float],
                  target: str, *, pass_threshold: float, tolerance: float,
                  allow_target_equal: bool, lower_is_better: bool) -> RatchetResult:
    def better(new: float, old: float) -> float:
        # signed improvement (positive = better) accounting for direction
        return (old - new) if lower_is_better else (new - old)

    reasons: List[str] = []
    regressions: List[Tuple[str, float, float]] = []

    # 1. target must improve (or hold)
    target_delta = None
    if target not in candidate:
        reasons.append(f"target family '{target}' missing from eval output")
        return RatchetResult(False, reasons, None, regressions)
    if target in baseline:
        target_delta = better(candidate[target], baseline[target])
        improved = target_delta > 0 or (allow_target_equal and abs(target_delta) <= 1e-12)
        if not improved:
            reasons.append(f"target '{target}' did not improve "
                           f"(baseline {baseline[target]:.4f} → {candidate[target]:.4f})")
    else:
        # new family: any score counts as "improvement" (from nothing)
        target_delta = None
        reasons.append(f"target '{target}' is new (no baseline) — treated as improvement")

    target_ok = (target not in baseline) or (target_delta is not None and
                 (target_delta > 0 or (allow_target_equal and abs(target_delta) <= 1e-12)))

    # 2. no previously-PASSING family may regress beyond tolerance
    for fam, base_score in baseline.items():
        if fam == target:
            continue
        was_passing = base_score >= pass_threshold if not lower_is_better else base_score <= pass_threshold
        if not was_passing:
            continue
        if fam not in candidate:
            regressions.append((fam, base_score, float("nan")))
            reasons.append(f"previously-passing family '{fam}' missing from candidate eval")
            continue
        delta = better(candidate[fam], base_score)
        if delta < -abs(tolerance):
            regressions.append((fam, base_score, candidate[fam]))
            reasons.append(f"regression on previously-passing '{fam}' "
                           f"({base_score:.4f} → {candidate[fam]:.4f}, Δ{delta:+.4f})")

    promote = target_ok and not regressions
    if promote:
        reasons.append("ratchet held: target improved and no previously-passing family regressed")
    return RatchetResult(promote, reasons, target_delta, regressions)


def _refuse_in_repo(baseline_file: Path, repo_root: Path, allow_in_repo: bool) -> None:
    try:
        baseline_file.resolve().relative_to(repo_root.resolve())
        inside = True
    except ValueError:
        inside = False
    if inside and not allow_in_repo:
        raise SystemExit(
            "[sealed-gate] REFUSING to store the baseline INSIDE the repo "
            f"({baseline_file}). The coding loop must not be able to read the sealed "
            "scores, or it will optimize against them. Put --baseline-file outside the "
            "repo (e.g. ~/.redline-sealed/<project>.json), or pass --allow-in-repo if "
            "you are certain the loop cannot read this path.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sealed-ratchet promotion gate (returns only PROMOTE/REJECT; see SEALED_RATCHET.md).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--eval-cmd", required=True,
                    help="Command that scores the candidate on the SEALED set and prints "
                         "{family: score} JSON. Run with REDLINE_SEALED=1 in its env.")
    ap.add_argument("--target", required=True, help="The family this change is meant to improve.")
    ap.add_argument("--baseline-file", required=True,
                    help="Where baseline scores live. MUST be outside the loop-readable repo.")
    ap.add_argument("--pass-threshold", type=float, default=0.5,
                    help="A family with baseline score >= this is 'passing' and is ratchet-protected.")
    ap.add_argument("--tolerance", type=float, default=0.0,
                    help="Allowed noise-level regression on a passing family before it blocks.")
    ap.add_argument("--allow-target-equal", action="store_true",
                    help="Allow promotion if the target holds (does not strictly improve).")
    ap.add_argument("--lower-is-better", action="store_true",
                    help="Scores where lower is better (e.g. error rate).")
    ap.add_argument("--timeout", type=int, default=3600, help="Eval command timeout (s).")
    ap.add_argument("--update-baseline", action="store_true",
                    help="On PROMOTE, write the candidate scores as the new baseline.")
    ap.add_argument("--init-baseline", action="store_true",
                    help="Just run the eval and WRITE the baseline (first-time setup); no ratchet.")
    ap.add_argument("--allow-in-repo", action="store_true",
                    help="Override the refusal to store the baseline inside the repo (unsafe).")
    ap.add_argument("--verbose", action="store_true",
                    help="Print the score breakdown. FOR A HUMAN ONLY — never pipe to the loop.")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    baseline_file = Path(args.baseline_file)
    _refuse_in_repo(baseline_file, repo_root, args.allow_in_repo)

    # init: run eval, store baseline, done.
    if args.init_baseline:
        scores = run_eval(args.eval_cmd, repo_root, args.timeout)
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(json.dumps(scores, indent=2, sort_keys=True) + "\n")
        # deliberately do NOT print scores to stdout (blindness); a count is safe.
        print(f"[sealed-gate] baseline initialized: {len(scores)} families "
              f"→ {baseline_file} (scores withheld).")
        if args.verbose:
            print(json.dumps(scores, indent=2, sort_keys=True), file=sys.stderr)
        return 0

    if not baseline_file.is_file():
        raise SystemExit(f"[sealed-gate] no baseline at {baseline_file}. Run once with "
                         f"--init-baseline first.")
    baseline = json.loads(baseline_file.read_text())

    candidate = run_eval(args.eval_cmd, repo_root, args.timeout)
    result = apply_ratchet(
        baseline, candidate, args.target,
        pass_threshold=args.pass_threshold, tolerance=args.tolerance,
        allow_target_equal=args.allow_target_equal, lower_is_better=args.lower_is_better)

    # THE ONLY THING THE LOOP SEES: the word, and the exit code. No scores.
    print("PROMOTE" if result.promote else "REJECT")

    # The reasons/scores go to STDERR and only under --verbose — for a human
    # reviewing out-of-band. Never let this reach the loop's context.
    if args.verbose:
        print("\n[sealed-gate] (HUMAN-ONLY — do not feed to the loop)", file=sys.stderr)
        for r in result.reasons:
            print(f"  - {r}", file=sys.stderr)
        if result.regressions:
            print("  regressions:", file=sys.stderr)
            for fam, b, c in result.regressions:
                print(f"    {fam}: {b:.4f} → {c:.4f}", file=sys.stderr)

    if result.promote and args.update_baseline:
        baseline_file.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n")
        print(f"[sealed-gate] baseline updated ({len(candidate)} families).", file=sys.stderr)

    return 0 if result.promote else 10


if __name__ == "__main__":
    raise SystemExit(main())
