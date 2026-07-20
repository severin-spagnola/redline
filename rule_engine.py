#!/usr/bin/env python3
"""
rule_engine.py — deterministic auto-labeling of new components (see DESIGN.md
"Auto-labeling new components").

The human ratifies the classification RULES once (redline.rules.json, authored in
onboarding); this engine applies them deterministically. No LLM at classification
time — the rule library IS the ratified human judgment.

Precedence for a new/unlabeled component (first match wins):
  1. explicit rule    — a redline.rules.json rule matches (path_glob/name_regex/type)
  2. parent           — a subcomponent inherits its parent component's level
  3. sibling-majority — a new sibling inherits the majority level of same-rank peers
  4. default + flag    — none of the above → default level (green), tagged
                         ⚠ auto-labeled + ratified:false

Anything auto-labeled is ENFORCED at its level immediately but carries
`ratified: false` so `redline drift` surfaces it until a human confirms.

Stdlib only. Reuses arch_gate's Component model + LEVELS.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# import arch_gate (single-file module in the same dir) for Component + loaders
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arch_gate as ag  # noqa: E402

LEVELS = ag.LEVELS
LEVEL_RANK = {"editable": 0, "conditional": 1, "never": 2, "frozen": 3}


# ---------------------------------------------------------------------------
# Rule library
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    match: Dict[str, Any]        # {path_glob?, name_regex?, type?}
    level: str
    reason: str = ""

    def matches(self, *, path: str, name: str, ctype: str = "") -> bool:
        m = self.match
        if "path_glob" in m and not fnmatch.fnmatch(path, m["path_glob"]):
            return False
        if "name_regex" in m and not re.search(m["name_regex"], name):
            return False
        if "type" in m and m["type"] != ctype:
            return False
        # a rule with no match keys matches nothing (avoid accidental catch-all)
        return any(k in m for k in ("path_glob", "name_regex", "type"))


@dataclass
class RuleLibrary:
    rules: List[Rule] = field(default_factory=list)
    inherit_subcomponent: bool = True
    inherit_sibling_majority: bool = True
    default: str = "editable"

    @staticmethod
    def load(path: Path) -> "RuleLibrary":
        if not path.is_file():
            # No rule library → default-green with inheritance off. Everything
            # unlabeled just flags for review; nothing auto-classifies by rule.
            return RuleLibrary(rules=[], inherit_subcomponent=True,
                               inherit_sibling_majority=True, default="editable")
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"[rule_engine] ERROR: {path}: invalid JSON: {e}")
        rules = []
        for r in raw.get("rules", []):
            lvl = r.get("level")
            if lvl not in LEVELS:
                raise SystemExit(f"[rule_engine] ERROR: rule level {lvl!r} invalid "
                                 f"(must be one of {LEVELS})")
            if "match" not in r or not isinstance(r["match"], dict):
                raise SystemExit(f"[rule_engine] ERROR: rule missing a 'match' object: {r}")
            rules.append(Rule(match=r["match"], level=lvl, reason=r.get("reason", "")))
        inh = raw.get("inherit", {})
        default = raw.get("default", "editable")
        if default not in LEVELS:
            raise SystemExit(f"[rule_engine] ERROR: default {default!r} invalid")
        return RuleLibrary(
            rules=rules,
            inherit_subcomponent=bool(inh.get("subcomponent", True)),
            inherit_sibling_majority=bool(inh.get("sibling_majority", True)),
            default=default,
        )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass
class Classification:
    level: str
    reason: str
    source: str          # "rule" | "parent" | "sibling-majority" | "default"
    ratified: bool       # rule/parent/sibling → False (auto); only humans ratify


def classify(
    path: str,
    lib: RuleLibrary,
    *,
    name: Optional[str] = None,
    ctype: str = "",
    parent_level: Optional[str] = None,
    sibling_levels: Optional[List[str]] = None,
) -> Classification:
    """Deterministically classify one candidate component. First match wins."""
    path = path.replace(os.sep, "/")
    name = name if name is not None else Path(path).name

    # 1. explicit rule
    for r in lib.rules:
        if r.matches(path=path, name=name, ctype=ctype):
            return Classification(r.level, r.reason or "matched a ratified rule",
                                  "rule", ratified=False)

    # 2. parent inheritance
    if lib.inherit_subcomponent and parent_level in LEVELS:
        return Classification(parent_level,
                              f"inherited from parent component ({parent_level})",
                              "parent", ratified=False)

    # 3. sibling-majority
    if lib.inherit_sibling_majority and sibling_levels:
        counts = Counter(l for l in sibling_levels if l in LEVELS)
        if counts:
            # majority; ties broken toward the STRICTER level (safer default)
            top = max(counts.items(),
                      key=lambda kv: (kv[1], LEVEL_RANK.get(kv[0], 0)))[0]
            return Classification(top,
                                  f"inherited majority level of sibling components ({top})",
                                  "sibling-majority", ratified=False)

    # 4. default + flag
    return Classification(lib.default,
                          "no rule/parent/sibling matched — auto-labeled default, needs review",
                          "default", ratified=False)


def make_stub(slug: str, path_glob: str, cls: Classification,
              display_name: Optional[str] = None) -> Dict[str, Any]:
    """A redline.meta.json component object for an auto-labeled component."""
    stub: Dict[str, Any] = {
        "component": slug,
        "display_name": display_name or slug.replace("-", " ").title(),
        "editability": cls.level,
        "edit_rule": cls.reason or "auto-labeled; ratify to confirm.",
        "description": "(auto-generated stub — edit + ratify)",
        "paths": [path_glob],
        "ratified": False,
        "auto_reason": f"{cls.source}: {cls.reason}",
    }
    return stub


# ---------------------------------------------------------------------------
# CLI — classify a single path (mainly for scripting / the drift tool)
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministically auto-label a component (see DESIGN.md).")
    ap.add_argument("--rules", default="redline.rules.json", help="Rule library path.")
    ap.add_argument("--path", required=True, help="Repo-relative path/glob of the candidate component.")
    ap.add_argument("--name", help="Component name (defaults to basename).")
    ap.add_argument("--type", default="", help="Component type (for type rules).")
    ap.add_argument("--parent-level", help="Parent component level (for inheritance).")
    ap.add_argument("--sibling-levels", default="", help="Comma-separated sibling levels.")
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args(argv)

    lib = RuleLibrary.load(Path(args.rules))
    cls = classify(
        args.path, lib,
        name=args.name, ctype=args.type,
        parent_level=args.parent_level,
        sibling_levels=[s.strip() for s in args.sibling_levels.split(",") if s.strip()],
    )
    if args.json:
        print(json.dumps({"level": cls.level, "reason": cls.reason,
                          "source": cls.source, "ratified": cls.ratified}, indent=2))
    else:
        flag = "  ⚠ unratified" if not cls.ratified else ""
        print(f"{args.path} → {cls.level}  [{cls.source}]{flag}")
        print(f"  reason: {cls.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
