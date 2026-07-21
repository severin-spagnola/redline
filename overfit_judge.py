#!/usr/bin/env python3
"""
overfit_judge.py — a FRESH-CONTEXT LLM review for disguised instance-fitting.

The static lint (overfit_lint.py) catches the lazy, deterministic forms of
instance-fitting (magic constants, hardcoded names). It cannot catch the
*disguised* forms: a process written in general-looking code that nonetheless
encodes knowledge of a specific test case, an overfit proposer prompt, or a
generalization laundered from a single sealed failure.

This tool prepares a review by a subagent with FRESH CONTEXT. Two properties make
it work, and both are enforced here:

  1. BLIND TO MOTIVATION. The judge receives the diff + the project's theory/thesis
     ONLY. It does NOT receive why the edit was made, the failing case that
     prompted it, the dev set, or the author's reasoning. The instant it sees the
     motivation it will rationalize the overfit the same way the author did — so
     this tool deliberately excludes all of that. That blindness is what lets it
     catch eyeball-laundering (#4): it cannot launder what it cannot see.

  2. ADVISORY, NEVER A GATE. It is an LLM, and an LLM must never be in the trust
     path (a judge that BLOCKS is a model deciding what is allowed — the exact
     thing redline forbids). So it FLAGS for a human / for the sealed set to
     confirm. Its value is speed: it narrows what the expensive sealed-set eval
     and the human look at. Trust its "flag"; never trust its "pass".

Modes:
  * default: print the exact judge prompt + the diff to stdout, for the user to
    run in whatever harness they trust (keeps the tool runtime-agnostic).
  * --run <cmd>: pipe the prompt to an LLM CLI (e.g. `--run 'claude -p'` or
    `--run 'llm'`); the tool captures the verdict. Still advisory.

The judge is asked for a STRUCTURED verdict (JSON) so downstream can route flags.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

JUDGE_SYSTEM = """\
You are an adversarial code reviewer with a single job: decide whether a code
change encodes INSTANCE-SPECIFIC KNOWLEDGE (overfitting) or a GENERAL PROCESS.

You are deliberately given NO context about why this change was made, what test
case prompted it, or what the author was trying to fix. This is intentional. Judge
ONLY the diff against the stated project theory. Do not speculate about intent;
if you find yourself constructing a justification for why a specific-looking edit
is "actually fine because they probably needed to handle X" — STOP. That
justification is exactly the rationalization that launders overfitting. Absence of
a visible motivation is not evidence the edit is general.

INSTANCE-FITTING you must flag (a change that bakes in knowledge of specific cases
rather than deriving behavior from general principles):
  - Branching on specific identifiers/names/shapes ("if it's the axi_ family...").
  - Constants or thresholds that appear tuned to hit specific cases.
  - Selection/routing logic that special-cases which rule/template/path fires for
    a particular input class.
  - Proposal/generation criteria (e.g. an LLM prompt inside the code) that encode
    what to look for in a way that mirrors known cases rather than the theory.
  - A "general" refactor whose only real effect is to make one specific case pass.
  - Anything that would improve behavior on a case the author could SEE while not
    following from the stated theory — i.e. fitting to the visible set.

GENERAL PROCESS you must pass (do not flag):
  - Logic derived from the stated theory / invariants, applied uniformly.
  - Structural constants (widths, 0/1/powers of two, protocol-defined values).
  - Changes whose behavior is independent of which specific instance is seen.

Output ONLY a JSON object, no prose:
{
  "verdict": "general" | "instance-fitting" | "uncertain",
  "confidence": 0.0-1.0,
  "flags": [ {"where": "<file:line or hunk>", "why": "<one sentence>"} ],
  "summary": "<one sentence>"
}
Default to "instance-fitting" or "uncertain" when unsure. A false flag costs a
human a glance; a missed overfit corrupts the system. Bias toward flagging.
"""

JUDGE_USER_TMPL = """\
PROJECT THEORY (the general principles this codebase must follow — the ONLY
context you get):
------------------------------------------------------------------------------
{theory}
------------------------------------------------------------------------------

THE DIFF TO JUDGE (does it encode instance knowledge, or a general process?):
------------------------------------------------------------------------------
{diff}
------------------------------------------------------------------------------

Return the JSON verdict now. Judge the diff against the theory only. Bias toward
flagging anything that branches on, or is tuned to, specific cases.
"""


def get_diff(base: Optional[str], head: str, repo_root: Path, diff_file: Optional[str]) -> str:
    if diff_file:
        return Path(diff_file).read_text()
    if base:
        try:
            return subprocess.run(
                ["git", "diff", f"{base}...{head}"],
                cwd=str(repo_root), check=True, capture_output=True, text=True,
            ).stdout
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"[overfit-judge] git diff failed: {e.stderr.strip()}")
    raise SystemExit("[overfit-judge] provide --base or --diff-file")


def build_prompt(theory: str, diff: str) -> str:
    return JUDGE_SYSTEM + "\n\n" + JUDGE_USER_TMPL.format(theory=theory.strip(), diff=diff.strip())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fresh-context LLM review for disguised instance-fitting (advisory).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--theory", help="Path to the project theory/thesis file (the ONLY context the judge gets).")
    ap.add_argument("--theory-text", help="Inline theory text (overrides --theory).")
    ap.add_argument("--base", help="Base git ref.")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--diff-file", help="A diff file to judge (overrides git).")
    ap.add_argument("--run", metavar="CMD",
                    help="LLM CLI to pipe the prompt into (e.g. 'claude -p', 'llm'). "
                         "Omit to print the prompt for your own harness.")
    ap.add_argument("--emit-prompt", metavar="FILE", help="Write the assembled prompt to FILE.")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()

    theory = args.theory_text
    if theory is None and args.theory:
        theory = Path(args.theory).read_text()
    if not theory:
        # A sane default so the tool is usable out of the box; users SHOULD supply
        # their own theory for a meaningful review.
        theory = ("This project values GENERALIZATION. Code must derive behavior from "
                  "stated principles/invariants, never from knowledge of specific test "
                  "cases. Fitting to cases you can see is the cardinal sin.")

    diff = get_diff(args.base, args.head, repo_root, args.diff_file)
    if not diff.strip():
        print("[overfit-judge] empty diff — nothing to judge.")
        return 0

    prompt = build_prompt(theory, diff)
    if args.emit_prompt:
        Path(args.emit_prompt).write_text(prompt)

    if not args.run:
        # runtime-agnostic: print the prompt for the user's trusted harness.
        print("# ---- FRESH-CONTEXT OVERFIT JUDGE PROMPT ----")
        print("# Run this in a NO-CONTEXT LLM session (a fresh subagent that has NOT")
        print("# seen why the edit was made). The verdict is ADVISORY — flags for a")
        print("# human; it is never a gate. Bias is toward flagging.\n")
        print(prompt)
        return 0

    # pipe to the given LLM CLI
    cmd = args.run.split()
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[overfit-judge] could not run '{args.run}': {e}", file=sys.stderr)
        return 2
    raw = out.stdout.strip()
    print(raw)
    # best-effort: if the model returned JSON with verdict != general, exit 1 as a
    # flag signal (still advisory — a human decides; this just routes CI).
    try:
        start = raw.find("{")
        verdict = json.loads(raw[start:]) if start >= 0 else {}
        if verdict.get("verdict") in ("instance-fitting", "uncertain"):
            print(f"\n[overfit-judge] FLAGGED ({verdict.get('verdict')}) — a human should "
                  f"review; this is advisory, not a block.", file=sys.stderr)
            return 1
    except (json.JSONDecodeError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
