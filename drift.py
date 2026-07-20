#!/usr/bin/env python3
"""
drift.py — surface components that need a human decision (see DESIGN.md
"The ⚠ unratified state").

Two jobs, both about keeping the graph honest as the codebase grows, WITHOUT an
LLM deciding anything:

  1. UNRATIFIED — list every component whose annotation has `ratified: false`
     (auto-labeled by the rule engine, or a stub a human hasn't confirmed). These
     are enforced at their level already, but a human should confirm or change
     them.

  2. UNCOVERED — list code paths (dirs/files under --scan roots) that no component
     owns, run the rule engine to propose a level for each, and emit a ready
     redline.meta.json stub. Nothing is written automatically; the human ratifies.

This automates *finding what is undecided* and *applying ratified rules*; it never
automates *deciding the rules* (that is onboarding) or *ratifying* (that is a
human flipping `ratified: true`).

Stdlib only. Reuses arch_gate (components) + rule_engine (classification).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arch_gate as ag        # noqa: E402
import rule_engine as re_      # noqa: E402


def load_components(repo_root: Path, policy_path: Path):
    policy = ag.load_policy(repo_root, policy_path)
    return policy, policy.components


def _is_covered(path: str, comps) -> bool:
    return ag.resolve_component(path, comps) is not None


def _read_ratified(repo_root: Path, annotation_glob: str) -> List[Dict[str, Any]]:
    """Return component records that carry ratified:false (raw, with meta_path)."""
    out = []
    for meta_path in sorted(repo_root.glob(annotation_glob)):
        try:
            raw = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        entries = raw["components"] if isinstance(raw, dict) and "components" in raw else [raw]
        for c in entries:
            if c.get("ratified") is False:
                out.append({**c, "_meta_path": str(meta_path.relative_to(repo_root))})
    return out


# candidate discovery: directories under scan roots that contain code but are
# not owned by any component. Deterministic, no model.
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c",
            ".cc", ".cpp", ".h", ".hpp", ".sv", ".v", ".scala", ".rb"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             ".pytest_cache", "dist", "build", ".mypy_cache"}


def _uncovered_dirs(repo_root: Path, scan_roots: List[str], comps) -> List[str]:
    """Top-most directories that contain code files but are uncovered."""
    uncovered: List[str] = []
    roots = [repo_root / r for r in scan_roots] if scan_roots else [repo_root]
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel = os.path.relpath(dirpath, repo_root).replace(os.sep, "/")
            if rel == ".":
                rel = ""
            has_code = any(Path(f).suffix in CODE_EXT for f in filenames)
            if not has_code:
                continue
            # is a representative file in this dir covered?
            sample = next((f for f in filenames if Path(f).suffix in CODE_EXT), None)
            sample_path = (rel + "/" + sample) if rel else sample
            if not _is_covered(sample_path, comps):
                # only report the top-most uncovered dir (skip children of an
                # already-reported uncovered dir)
                if not any(rel == u or rel.startswith(u + "/") for u in uncovered):
                    uncovered.append(rel)
    return sorted(set(uncovered))


def run_drift(repo_root: Path, policy_path: Path, rules_path: Path,
              scan_roots: List[str]) -> Dict[str, Any]:
    policy, comps = load_components(repo_root, policy_path)
    lib = re_.RuleLibrary.load(rules_path)
    annotation_glob = policy.config.get("annotation_glob", "**/redline.meta.json")

    unratified = _read_ratified(repo_root, annotation_glob)

    uncovered_dirs = _uncovered_dirs(repo_root, scan_roots, comps)
    proposals = []
    for d in uncovered_dirs:
        glob = (d + "/**") if d else "**"
        slug = (d.replace("/", "-") or "root") + "-component"
        cls = re_.classify(glob, lib, name=Path(d).name or "root")
        proposals.append({
            "path": d,
            "suggested_stub": re_.make_stub(slug, glob, cls),
            "source": cls.source,
            "level": cls.level,
        })
    return {
        "unratified_components": unratified,
        "uncovered_paths": proposals,
    }


def _fmt(result: Dict[str, Any]) -> str:
    lines = []
    unr = result["unratified_components"]
    lines.append(f"⚠ UNRATIFIED components ({len(unr)}) — auto-labeled or stubbed; "
                 f"enforced at their level but need a human to confirm:")
    if unr:
        for c in unr:
            lines.append(f"    [{c.get('editability','?'):<11}] {c['component']}  "
                         f"({c.get('_meta_path','')})  — {c.get('auto_reason', c.get('edit_rule',''))}")
            lines.append(f"        → to ratify: set \"ratified\": true (or change the level) in that file")
    else:
        lines.append("    (none — every component is human-ratified)")
    lines.append("")
    unc = result["uncovered_paths"]
    lines.append(f"UNCOVERED code paths ({len(unc)}) — no component owns these; "
                 f"rule engine proposes a level (ratify to adopt):")
    if unc:
        for p in unc:
            lines.append(f"    {p['path'] or '(root)'}  → suggest {p['level']}  [{p['source']}]")
        lines.append("")
        lines.append("    To adopt: add the suggested stub(s) below to a redline.meta.json,")
        lines.append("    review the level, then set \"ratified\": true.")
    else:
        lines.append("    (none — all scanned code is covered by a component)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Surface unratified + uncovered components (see DESIGN.md).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--policy", default="arch.policy.json")
    ap.add_argument("--rules", default="redline.rules.json")
    ap.add_argument("--scan", nargs="*", default=[],
                    help="Directories to scan for uncovered code (default: whole repo).")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--emit-stubs", metavar="FILE",
                    help="Write the proposed redline.meta.json stubs for uncovered paths to FILE.")
    ap.add_argument("--fail-on-unratified", action="store_true",
                    help="Exit 1 if any unratified components exist (for a 'ratify your graph' CI check).")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    result = run_drift(repo_root, Path(args.policy), Path(args.rules), args.scan)

    if args.emit_stubs and result["uncovered_paths"]:
        stubs = {"components": [p["suggested_stub"] for p in result["uncovered_paths"]]}
        Path(args.emit_stubs).write_text(json.dumps(stubs, indent=2) + "\n")

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_fmt(result))

    if args.fail_on_unratified and result["unratified_components"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
