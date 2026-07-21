#!/usr/bin/env python3
"""
redline — thesis-enforcement guardrails for AI coding agents (unified CLI).

One entrypoint over the whole stack. Every subcommand is a thin dispatch to a
focused module; run `redline <cmd> --help` for that module's full options.

    redline check     run the gate + overfit lint + (optional) fresh-context judge
    redline gate      the deterministic merge gate only (arch_gate)
    redline lint      the static instance-fitting lint (overfit_lint)
    redline judge     the fresh-context overfit judge (overfit_judge)
    redline drift     list unratified / uncovered components (drift)
    redline sealed    the sealed-ratchet promotion gate (sealed_gate)
    redline label     (re)generate the graph labeler HTML (build_labeler)
    redline docs      print the doc index — what to read, in order

FOR AN LLM ADOPTING REDLINE: run `redline docs` first. It lists, in order, the
docs that define the contracts (onboarding flow, editability format, the
anti-overfit stack, the sealed-set firewall). Everything you need is plain text
in this repo — read it before wiring redline into a project.

The trust discipline, everywhere: the LLM proposes; deterministic checks a human
ratified dispose. Only `gate` and `sealed` are hard gates (both deterministic).
`lint` and `judge` only flag (a heuristic and an LLM must never be the boundary).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# subcommand -> module file it dispatches to
SUBCOMMANDS = {
    "gate":   "arch_gate.py",
    "lint":   "overfit_lint.py",
    "judge":  "overfit_judge.py",
    "drift":  "drift.py",
    "sealed": "sealed_gate.py",
    "label":  "build_labeler.py",
}

DOC_INDEX = [
    ("README.md",         "Start here — the whole idea, the four levels, quick start."),
    ("ONBOARDING.md",     "The guided prompt to bootstrap a project's graph + rule library (feed to your agent)."),
    ("SPEC.md",           "The redline.meta.json format + gate semantics (the contract)."),
    ("ANTI_OVERFIT.md",   "The 4-layer anti-overfit stack (redline → lint → judge → sealed) and what each can't do."),
    ("SEALED_RATCHET.md", "The sealed held-out set discipline: blindness, ratchet, burn-on-inspection. Read before any autonomous loop."),
    ("HARNESS.md",        "How redline plugs into a project + coding loop: feedback surfacing + the firewall (what the loop must NEVER see)."),
    ("DESIGN.md",         "Design decisions + rationale (internal)."),
    ("PRIOR_ART.md",      "Cited proof of what does/doesn't exist elsewhere."),
]


def _print_docs() -> int:
    print("redline documentation — read in this order:\n")
    for name, blurb in DOC_INDEX:
        exists = "  " if (HERE / name).is_file() else "? "
        print(f"  {exists}{name:<20} {blurb}")
    print("\nAll are plain text in the redline repo. For an LLM: read README, ONBOARDING,")
    print("SPEC, then ANTI_OVERFIT + SEALED_RATCHET before wiring redline into a loop.")
    print("\nKey contract reminders:")
    print("  • Only `gate` and `sealed` are HARD gates, and both are deterministic.")
    print("  • `lint` and `judge` only FLAG — never treat their output as a block.")
    print("  • The sealed score is NEVER surfaced to the coding loop — only PROMOTE/REJECT.")
    return 0


def _usage() -> int:
    print(__doc__.strip())
    print("\nSubcommands:", ", ".join(list(SUBCOMMANDS) + ["docs"]))
    return 0


def _dispatch(cmd: str, rest: list) -> int:
    module = HERE / SUBCOMMANDS[cmd]
    if not module.is_file():
        print(f"[redline] internal error: {module} not found", file=sys.stderr)
        return 2
    # run the module as __main__ with argv set to its own args
    sys.argv = [str(module)] + rest
    try:
        runpy.run_path(str(module), run_name="__main__")
        return 0
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)


def _cmd_check(rest: list) -> int:
    """Composite: gate (hard) → lint (advisory) → judge (advisory, if --theory).

    Exit reflects the HARD gate only (the deterministic block). The lint/judge
    findings are surfaced but do not change the exit — they are advisory, for a
    human. This mirrors the trust discipline: only deterministic checks gate.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="redline check",
        description="Run the deterministic gate (hard) + overfit lint + optional judge (advisory).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--policy", default="arch.policy.json")
    ap.add_argument("--base", help="Base git ref (passed to gate + judge).")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--pr-body-file")
    ap.add_argument("--approved-owners", default="")
    ap.add_argument("--labels", default="")
    ap.add_argument("--theory", help="If set, also run the fresh-context judge against this theory file.")
    ap.add_argument("--judge-run", default="", help="LLM CLI for the judge, e.g. 'claude -p'. Omit to skip running it.")
    ap.add_argument("--lint-config", default="overfit_lint.json")
    args, unknown = ap.parse_known_args(rest)

    print("── redline check ─────────────────────────────────────────────\n")
    # 1. HARD gate
    print("[1/3] gate (deterministic — the block):")
    gate_args = ["--repo-root", args.repo_root, "--policy", args.policy]
    if args.base: gate_args += ["--base", args.base, "--head", args.head]
    if args.pr_body_file: gate_args += ["--pr-body-file", args.pr_body_file]
    if args.approved_owners: gate_args += ["--approved-owners", args.approved_owners]
    if args.labels: gate_args += ["--labels", args.labels]
    gate_code = _dispatch("gate", gate_args)

    # 2. advisory lint
    print("\n[2/3] overfit lint (advisory — flags for a human):")
    _dispatch("lint", ["--repo-root", args.repo_root, "--policy", args.policy,
                       "--config", args.lint_config])

    # 3. advisory judge (only if theory + a runner given)
    print("\n[3/3] fresh-context judge (advisory):")
    if args.theory and args.base:
        jargs = ["--repo-root", args.repo_root, "--theory", args.theory,
                 "--base", args.base, "--head", args.head]
        if args.judge_run:
            jargs += ["--run", args.judge_run]
        _dispatch("judge", jargs)
    else:
        print("  (skipped — pass --theory and --base to run it; add --judge-run 'claude -p' to execute)")

    print("\n──────────────────────────────────────────────────────────────")
    print(f"HARD gate verdict: {'PASS' if gate_code == 0 else 'BLOCKED'} "
          f"(lint/judge are advisory and do not change this).")
    return gate_code


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        return _usage()
    cmd, rest = args[0], args[1:]
    if cmd == "docs":
        return _print_docs()
    if cmd == "check":
        return _cmd_check(rest)
    if cmd in SUBCOMMANDS:
        return _dispatch(cmd, rest)
    print(f"[redline] unknown subcommand '{cmd}'. Run `redline --help`.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
