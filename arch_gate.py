#!/usr/bin/env python3
"""
arch_gate.py — reference implementation of the Editability Policy gate (SPEC.md).

Deterministic, offline, stdlib-only. Maps a diff's changed paths to components,
looks up each component's editability level, and blocks the merge when a
protected component is changed without the required override signal.

This is the LOAD-BEARING enforcement layer: it runs in CI on a PR to a protected
branch, and a non-zero exit fails the required status check, which blocks the
merge. The agent controls neither the CI runner nor branch protection, so it
cannot route around this.

Usage:
    # In CI (paths come from the PR diff):
    python arch_gate.py --base origin/main --head HEAD \
        --policy arch.policy.json --pr-body-file pr_body.txt \
        --approved-owners "@alice,@bob" --labels "arch-reviewed"

    # Or feed an explicit file list (bypasses git):
    python arch_gate.py --changed-file changed.txt --policy arch.policy.json ...

Exit codes:
    0  no violation (merge allowed)
    1  one or more violations (merge blocked)
    2  configuration / usage error
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LEVELS = ("editable", "conditional", "never", "frozen")
UNANNOTATED = "__unannotated__"


# ---------------------------------------------------------------------------
# Policy + annotation loading
# ---------------------------------------------------------------------------


@dataclass
class Component:
    slug: str
    editability: str
    paths: List[str]
    dir_scope: str          # annotation-file directory (implicit path scope)
    edit_rule: str = ""
    display_name: str = ""
    connects_to: List[str] = field(default_factory=list)
    ratified: bool = True   # False = auto-labeled by the rule engine, awaiting human confirm


@dataclass
class Policy:
    config: Dict[str, Any]
    components: List[Component]
    # redline's OWN governance files (set by load_policy) — self-protected so an
    # agent can't edit the policy/annotations/rules to unblock itself.
    policy_rel: str = ""            # arch.policy.json, repo-relative
    meta_rels: List[str] = field(default_factory=list)   # the *.meta.json files
    rules_rel: str = ""            # redline.rules.json if present, repo-relative

    def levels_cfg(self) -> Dict[str, Any]:
        return self.config.get("levels", {})

    def overrides_cfg(self) -> Dict[str, Any]:
        return self.config.get("overrides", {})

    def unannotated_policy(self) -> str:
        return self.config.get("unannotated_policy", "pass")


def _load_components(repo_root: Path, annotation_glob: str) -> List[Component]:
    comps: List[Component] = []
    seen_slugs: Dict[str, str] = {}
    for meta_path in sorted(repo_root.glob(annotation_glob)):
        try:
            raw = json.loads(meta_path.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"[arch_gate] ERROR: {meta_path}: invalid JSON: {e}")
        entries = raw["components"] if isinstance(raw, dict) and "components" in raw else [raw]
        dir_scope = str(meta_path.parent.relative_to(repo_root)).replace(os.sep, "/")
        if dir_scope == ".":
            dir_scope = ""
        for c in entries:
            slug = c.get("component")
            lvl = c.get("editability")
            if not slug or lvl not in LEVELS:
                raise SystemExit(
                    f"[arch_gate] ERROR: {meta_path}: component '{slug}' has invalid/missing "
                    f"editability {lvl!r} (must be one of {LEVELS})"
                )
            if slug in seen_slugs:
                raise SystemExit(
                    f"[arch_gate] ERROR: duplicate component slug '{slug}' "
                    f"(in {seen_slugs[slug]} and {meta_path})"
                )
            seen_slugs[slug] = str(meta_path)
            comps.append(Component(
                slug=slug,
                editability=lvl,
                paths=list(c.get("paths", [])),
                dir_scope=dir_scope,
                edit_rule=c.get("edit_rule", ""),
                display_name=c.get("display_name", slug),
                connects_to=list(c.get("connects_to", [])),
                ratified=c.get("ratified", True),
            ))
    return comps


def load_policy(repo_root: Path, policy_path: Path) -> Policy:
    try:
        cfg = json.loads(policy_path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"[arch_gate] ERROR: policy config not found: {policy_path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"[arch_gate] ERROR: {policy_path}: invalid JSON: {e}")
    annotation_glob = cfg.get("annotation_glob", "**/redline.meta.json")
    comps = _load_components(repo_root, annotation_glob)
    _validate(cfg, comps, policy_path)

    # Record redline's own governance files (repo-relative) for self-protection.
    def _rel(p: Path) -> str:
        try:
            return str(p.resolve().relative_to(repo_root.resolve())).replace(os.sep, "/")
        except ValueError:
            return ""  # outside the repo; not diff-protectable here
    meta_rels = [str(m.relative_to(repo_root)).replace(os.sep, "/")
                 for m in sorted(repo_root.glob(annotation_glob))]
    rules_name = cfg.get("rules_file", "redline.rules.json")
    rules_path = repo_root / rules_name
    return Policy(config=cfg, components=comps,
                  policy_rel=_rel(policy_path),
                  meta_rels=meta_rels,
                  rules_rel=(rules_name if rules_path.is_file() else ""))


def _validate(cfg: Dict[str, Any], comps: List[Component], policy_path: Path) -> None:
    # every level referenced has a config entry; every override referenced is defined
    levels_cfg = cfg.get("levels", {})
    overrides_cfg = cfg.get("overrides", {})
    for lvl in LEVELS:
        if lvl not in levels_cfg:
            raise SystemExit(f"[arch_gate] ERROR: {policy_path}: levels.{lvl} is not configured")
        rule = levels_cfg[lvl]
        on_change = rule.get("on_change")
        if on_change not in ("pass", "require", "block"):
            raise SystemExit(f"[arch_gate] ERROR: levels.{lvl}.on_change must be pass|require|block")
        for sig in rule.get("override", []):
            if sig not in overrides_cfg:
                raise SystemExit(
                    f"[arch_gate] ERROR: levels.{lvl}.override references undefined signal '{sig}'"
                )
    # path overlap check (§6.3): warn, don't fail — ambiguity resolved by specificity
    claimed: Dict[str, str] = {}
    for c in comps:
        for g in c.paths:
            # exact-glob collisions only (cheap heuristic)
            if g in claimed and claimed[g] != c.slug:
                print(f"[arch_gate] WARNING: path glob '{g}' claimed by both "
                      f"{claimed[g]} and {c.slug}", file=sys.stderr)
            claimed[g] = c.slug


# ---------------------------------------------------------------------------
# Path -> component resolution (§2.3)
# ---------------------------------------------------------------------------


def resolve_component(path: str, comps: List[Component]) -> Optional[Component]:
    path = path.replace(os.sep, "/")
    # 1. most specific `paths` glob match (longest matching glob wins)
    best: Optional[Component] = None
    best_len = -1
    for c in comps:
        for g in c.paths:
            if fnmatch.fnmatch(path, g) and len(g) > best_len:
                best, best_len = c, len(g)
    if best is not None:
        return best
    # 2. deepest annotation-file directory that is an ancestor (nearest-file-wins).
    #    Only components WITHOUT explicit `paths` claim files by directory scope;
    #    a component that declared `paths` is precisely scoped to those globs and
    #    must not absorb unrelated sibling files sharing its annotation directory.
    best = None
    best_depth = -1
    for c in comps:
        if c.paths:
            continue
        scope = c.dir_scope
        if scope == "":
            # root-level annotation is a catch-all ancestor of everything
            depth = 0
        elif path == scope or path.startswith(scope + "/"):
            depth = scope.count("/") + 1
        else:
            continue
        if depth > best_depth:
            best, best_depth = c, depth
    return best  # may be None -> unannotated


# ---------------------------------------------------------------------------
# Marker-comment anchoring (SPEC §, DESIGN "intra-file anchoring")
#
#   # arch:begin <slug> <level>  [reason="..."]
#   ... guarded region ...
#   # arch:end <slug>
#
# A change to any line BETWEEN a region's markers is treated as a change to that
# slug at that level. Deleting a begin-marker that existed in the base ref counts
# as a violation (else an agent escapes the lock by removing the markers).
# ---------------------------------------------------------------------------

_MARKER_BEGIN = re.compile(
    r"arch:begin\s+(?P<slug>[a-z0-9][a-z0-9-]*)\s+(?P<level>editable|conditional|never|frozen)"
    r"(?:\s+reason\s*=\s*\"(?P<reason>[^\"]*)\")?",
    re.IGNORECASE,
)
_MARKER_END = re.compile(r"arch:end\s+(?P<slug>[a-z0-9][a-z0-9-]*)", re.IGNORECASE)


@dataclass
class MarkerRegion:
    slug: str
    level: str
    reason: str
    begin_line: int          # 1-indexed line of the begin marker
    end_line: int            # 1-indexed line of the end marker (or EOF)
    path: str


def scan_markers(text: str, path: str) -> List[MarkerRegion]:
    """Parse arch:begin/arch:end marker regions from a file's text."""
    regions: List[MarkerRegion] = []
    open_stack: List[Dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        mb = _MARKER_BEGIN.search(line)
        if mb:
            open_stack.append({
                "slug": mb.group("slug"), "level": mb.group("level").lower(),
                "reason": mb.group("reason") or "", "begin": i,
            })
            continue
        me = _MARKER_END.search(line)
        if me and open_stack:
            # close the most recent matching-slug open marker (nearest wins)
            for j in range(len(open_stack) - 1, -1, -1):
                if open_stack[j]["slug"] == me.group("slug"):
                    o = open_stack.pop(j)
                    regions.append(MarkerRegion(o["slug"], o["level"], o["reason"],
                                                o["begin"], i, path))
                    break
    # unclosed begin markers guard to EOF
    n = len(text.splitlines())
    for o in open_stack:
        regions.append(MarkerRegion(o["slug"], o["level"], o["reason"], o["begin"], n, path))
    return regions


def git_show(ref: str, path: str, repo_root: Path) -> Optional[str]:
    """Return file contents at a ref, or None if the file didn't exist there."""
    try:
        return subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=str(repo_root), check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None


def changed_line_ranges(base: str, head: str, path: str, repo_root: Path) -> List[Tuple[int, int]]:
    """Return (start,end) 1-indexed line ranges changed in `path` on the HEAD side."""
    try:
        out = subprocess.run(
            ["git", "diff", "--unified=0", f"{base}...{head}", "--", path],
            cwd=str(repo_root), check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return []
    ranges: List[Tuple[int, int]] = []
    for line in out.splitlines():
        # hunk header: @@ -a,b +c,d @@
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                # pure deletion; attribute to the line at `start`
                ranges.append((start, start))
            else:
                ranges.append((start, start + count - 1))
    return ranges


# ---------------------------------------------------------------------------
# Override evaluation (§5)
# ---------------------------------------------------------------------------


@dataclass
class PRContext:
    pr_body: str = ""
    approved_owners: List[str] = field(default_factory=list)  # owners who approved
    labels: List[str] = field(default_factory=list)
    env_ack: bool = False
    # For codeowners_review we need to know owners of the touched path. In this
    # reference impl we accept an "approved_owners" list and treat any approval
    # as satisfying codeowners_review; a fuller impl would parse CODEOWNERS and
    # match owners to the component's paths.


def _pr_body_block_present(pr_body: str, heading: str) -> Optional[str]:
    """Return the block's text if a `## <heading>` section exists, else None."""
    # Match a markdown heading line for `heading` and capture until the next heading/EOF.
    pat = re.compile(
        r"^#{1,6}\s*" + re.escape(heading) + r"\s*$(.*?)(?=^#{1,6}\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pat.search(pr_body or "")
    if not m:
        return None
    body = m.group(1).strip()
    if not body:
        return None
    # A `reason:` line must carry an actual value to count as a justification.
    reason_line = re.search(r"^\s*reason\s*:(.*)$", body, re.MULTILINE | re.IGNORECASE)
    if reason_line is not None:
        return body if reason_line.group(1).strip() else None
    # No reason: line at all, but the block has other content — accept it (the
    # heading itself is a deliberate override marker); reason: is just preferred.
    return body


def evaluate_signal(sig_name: str, sig_cfg: Dict[str, Any], ctx: PRContext) -> bool:
    t = sig_cfg.get("type")
    if t == "pr_body_block":
        return _pr_body_block_present(ctx.pr_body, sig_cfg.get("heading", "Arch-Override")) is not None
    if t == "codeowners_review":
        return len(ctx.approved_owners) > 0
    if t == "label":
        return sig_cfg.get("name") in ctx.labels
    if t == "env_ack":
        return ctx.env_ack
    # unknown signal type -> not satisfied (fail closed)
    print(f"[arch_gate] WARNING: unknown override signal type '{t}'", file=sys.stderr)
    return False


def overrides_satisfied(sig_names: List[str], mode: str, overrides_cfg: Dict[str, Any],
                        ctx: PRContext) -> Tuple[bool, List[str]]:
    got = [s for s in sig_names if evaluate_signal(s, overrides_cfg.get(s, {}), ctx)]
    if mode == "all":
        return (len(got) == len(sig_names) and len(sig_names) > 0), got
    return (len(got) > 0), got  # default "any"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


LEVEL_RANK = {"editable": 0, "conditional": 1, "never": 2, "frozen": 3}


@dataclass
class Verdict:
    path: str
    slug: str
    level: str
    status: str          # "pass" | "allowed_with_override" | "violation"
    needed: List[str] = field(default_factory=list)
    got: List[str] = field(default_factory=list)
    reason: str = ""            # the edit_rule / marker reason (the "why")
    source: str = "component"   # "component" | "marker" | "guard-deletion" | "unannotated"
    anchor: str = ""            # e.g. "L40-47" for a marker region
    nudge: bool = False         # non-blocking "label this?" nudge
    unratified: bool = False    # component is auto-labeled, awaiting human confirm (⚠)


def _prescriptive(v: Verdict, policy: Policy) -> str:
    """The human/agent-facing 'what to do next' message for a verdict."""
    ovcfg = policy.overrides_cfg()
    if v.status == "pass":
        return ""
    why = f" — {v.reason}" if v.reason else ""
    if v.source == "guard-deletion":
        return (f"You DELETED the arch guard for `{v.slug}` ({v.level}){why}. "
                f"Removing a guard marker is treated as editing the guarded code. "
                f"Restore the `# arch:begin {v.slug} {v.level}` / `# arch:end {v.slug}` "
                f"markers, or justify via an override. Do NOT strip guards to pass.")
    where = f" at {v.path}:{v.anchor}" if v.anchor else f" in {v.path}"
    # spell the concrete override actions
    actions = []
    for sig in v.needed:
        t = ovcfg.get(sig, {}).get("type")
        if t == "pr_body_block":
            h = ovcfg[sig].get("heading", "Arch-Override")
            actions.append(f'add a "## {h}" block to the PR body with a `reason:` line')
        elif t == "codeowners_review":
            actions.append("get an approving review from a code owner of this path")
        elif t == "label":
            actions.append(f'apply the `{ovcfg[sig].get("name","")}` label (authorized reviewer only)')
        elif t == "env_ack":
            actions.append("acknowledge locally (local runs only)")
    joiner = " AND " if v.needed and policy.config.get("override_mode") == "all" else " OR "
    fix = joiner.join(actions) if actions else "obtain the required override"
    verb = "must not be edited" if v.level in ("never", "frozen") else "requires justification to edit"
    return (f"You edited `{v.slug}` ({v.level}){where}{why}. This component {verb}. "
            f"To pass: revert those lines (e.g. `git checkout <base> -- {v.path}`), OR {fix}. "
            f"Do NOT keep editing to try to make the check pass.")


def _marker_verdicts(changed: List[str], policy: Policy, ctx: PRContext,
                     base: Optional[str], head: Optional[str], repo_root: Path) -> List[Verdict]:
    """Verdicts from intra-file marker regions + guard-deletion detection."""
    if not base or not head:
        return []
    levels_cfg = policy.levels_cfg()
    overrides_cfg = policy.overrides_cfg()
    out: List[Verdict] = []
    for p in changed:
        head_text = git_show(head, p, repo_root)
        base_text = git_show(base, p, repo_root)
        head_regions = scan_markers(head_text, p) if head_text else []
        base_regions = scan_markers(base_text, p) if base_text else []
        # (a) guard-deletion: a begin marker slug present in base but gone in head
        head_slugs = {(r.slug, r.level) for r in head_regions}
        for br in base_regions:
            if (br.slug, br.level) not in head_slugs:
                rule = levels_cfg.get(br.level, {"on_change": "block", "override": []})
                sig_names = rule.get("override", [])
                mode = rule.get("override_mode", policy.config.get("override_mode", "any"))
                ok, got = overrides_satisfied(sig_names, mode, overrides_cfg, ctx)
                out.append(Verdict(
                    p, br.slug, br.level,
                    "allowed_with_override" if ok else "violation",
                    sig_names, got, reason=br.reason, source="guard-deletion"))
        # (b) edits inside a marker region on the head side
        if not head_regions:
            continue
        edited = changed_line_ranges(base, head, p, repo_root)
        for reg in head_regions:
            rule = levels_cfg.get(reg.level, {"on_change": "pass", "override": []})
            if rule.get("on_change", "pass") == "pass":
                continue
            hit = any(not (e_end < reg.begin_line or e_start > reg.end_line)
                      for (e_start, e_end) in edited)
            if not hit:
                continue
            sig_names = rule.get("override", [])
            mode = rule.get("override_mode", policy.config.get("override_mode", "any"))
            ok, got = overrides_satisfied(sig_names, mode, overrides_cfg, ctx)
            out.append(Verdict(
                p, reg.slug, reg.level,
                "allowed_with_override" if ok else "violation",
                sig_names, got, reason=reg.reason, source="marker",
                anchor=f"L{reg.begin_line}-{reg.end_line}"))
    return out


def _red_slugs(policy: Policy) -> set:
    return {c.slug for c in policy.components if c.editability in ("never", "frozen")}


def _nudge_verdicts(changed: List[str], policy: Policy) -> List[Verdict]:
    """Non-blocking 'label this?' nudges for new/green code adjacent to red nodes."""
    reds = _red_slugs(policy)
    if not reds:
        return []
    # a green/unannotated changed file whose path sits under, or whose resolved
    # component connects_to, a red node -> nudge to consider labeling it.
    red_dirs = [c.dir_scope for c in policy.components
                if c.editability in ("never", "frozen") and c.dir_scope]
    out: List[Verdict] = []
    for p in changed:
        comp = resolve_component(p, policy.components)
        lvl = comp.editability if comp else "editable"
        if lvl in ("never", "frozen"):
            continue  # already guarded
        near = any(p.startswith(d + "/") for d in red_dirs)
        conn = bool(comp and (set(getattr(comp, "connects_to", []) or []) & reds))
        if near or conn:
            out.append(Verdict(p, comp.slug if comp else "(unannotated)", lvl,
                               "pass", nudge=True,
                               reason="new/green code adjacent to a thesis-critical (red) component"))
    return out


def _is_governance_file(path: str, policy: Policy) -> Optional[str]:
    """If `path` is one of redline's OWN governance files, return a label; else None.

    Editing these is how an agent would unblock itself (relax the policy, relabel a
    component, delete a rule). So they are self-protected: treated as needing the
    override, hardcoded — NOT configurable, because the config is itself protected.
    """
    p = path.replace(os.sep, "/")
    if policy.policy_rel and p == policy.policy_rel:
        return "the gate config (arch.policy.json)"
    if p in policy.meta_rels:
        return "an editability annotation (redline.meta.json)"
    if policy.rules_rel and p == policy.rules_rel:
        return "the auto-labeling rule library (redline.rules.json)"
    # also protect a redline hook/gate that lives in the repo, if declared
    return None


# Editing redline's own governance always requires an override, regardless of
# config. Built-in signal definitions so this works even if the config's
# `overrides` block is minimal or was deleted (an agent can't disable
# self-protection by stripping the config — the config is itself protected).
_SELF_PROTECT_SIGNALS = ["justification", "code_owner"]
_SELF_PROTECT_DEFS = {
    "justification": {"type": "pr_body_block", "heading": "Arch-Override"},
    "code_owner": {"type": "codeowners_review"},
}


def run_gate(changed: List[str], policy: Policy, ctx: PRContext,
             base: Optional[str] = None, head: Optional[str] = None,
             repo_root: Optional[Path] = None) -> List[Verdict]:
    verdicts: List[Verdict] = []
    levels_cfg = policy.levels_cfg()
    overrides_cfg = policy.overrides_cfg()
    for p in changed:
        # --- self-protection: redline's own governance files (runs FIRST, hardcoded) ---
        gov = _is_governance_file(p, policy)
        if gov is not None:
            # use built-in signal defs (config can't weaken self-protection).
            self_ov = {**_SELF_PROTECT_DEFS, **{k: v for k, v in overrides_cfg.items()
                                                if k in _SELF_PROTECT_SIGNALS}}
            ok, got = overrides_satisfied(_SELF_PROTECT_SIGNALS, "any", self_ov, ctx)
            verdicts.append(Verdict(
                p, "redline-self", "never",
                "allowed_with_override" if ok else "violation",
                _SELF_PROTECT_SIGNALS, got,
                reason=f"changing {gov} weakens the guard itself — this is a governed "
                       f"action requiring the override, so an agent cannot edit the "
                       f"policy to unblock itself",
                source="self-protect"))
            continue
        comp = resolve_component(p, policy.components)
        level = comp.editability if comp else UNANNOTATED
        slug = comp.slug if comp else "(unannotated)"
        reason = comp.edit_rule if comp else ""
        unratified = bool(comp and not comp.ratified)
        if level == UNANNOTATED:
            rule = {"on_change": policy.unannotated_policy(), "override": []}
            src = "unannotated"
        else:
            rule = levels_cfg[level]
            src = "component"
        on_change = rule.get("on_change", "pass")
        if on_change == "pass":
            verdicts.append(Verdict(p, slug, level, "pass", reason=reason, source=src,
                                    unratified=unratified))
            continue
        sig_names = rule.get("override", [])
        mode = rule.get("override_mode", policy.config.get("override_mode", "any"))
        ok, got = overrides_satisfied(sig_names, mode, overrides_cfg, ctx)
        verdicts.append(Verdict(
            p, slug, level,
            "allowed_with_override" if ok else "violation",
            sig_names, got, reason=reason, source=src, unratified=unratified))
    # intra-file marker verdicts + guard-deletion (needs git refs)
    if repo_root is not None:
        verdicts.extend(_marker_verdicts(changed, policy, ctx, base, head, repo_root))
    # non-blocking nudges
    verdicts.extend(_nudge_verdicts(changed, policy))
    return verdicts


# ---------------------------------------------------------------------------
# Diff acquisition
# ---------------------------------------------------------------------------


def git_changed(base: str, head: str, repo_root: Path) -> List[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...{head}"],
            cwd=str(repo_root), check=True, capture_output=True, text=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[arch_gate] ERROR: git diff failed: {e.stderr.strip()}")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(verdicts: List[Verdict], policy: Policy) -> str:
    icon = {"pass": "  ok ", "allowed_with_override": " ovr ", "violation": "BLOCK"}
    lines = []
    for v in verdicts:
        if v.nudge:
            lines.append(f"[NUDGE] {v.path}  → near red `{v.slug}`: consider labeling ({v.reason})")
            continue
        src = f" {{{v.source}}}" if v.source not in ("component", "unannotated") else ""
        anchor = f":{v.anchor}" if v.anchor else ""
        warn = "  ⚠ unratified" if v.unratified else ""
        base = f"[{icon[v.status]}] {v.level:<11} {v.path}{anchor}{src}  → {v.slug}{warn}"
        if v.status == "allowed_with_override":
            base += f"   (override: {','.join(v.got)})"
        lines.append(base)
        if v.status == "violation":
            for ml in _prescriptive(v, policy).split(". "):
                if ml.strip():
                    lines.append(f"          ↳ {ml.strip().rstrip('.')}.")
    return "\n".join(lines)


def _fmt_pr_comment(verdicts: List[Verdict], policy: Policy) -> str:
    """A Markdown comment body for posting to the PR (agent reads this next loop)."""
    violations = [v for v in verdicts if v.status == "violation"]
    overridden = [v for v in verdicts if v.status == "allowed_with_override"]
    nudges = [v for v in verdicts if v.nudge]
    out = ["<!-- arch-gate -->", "## 🛡️ Editability Gate", ""]
    if violations:
        out.append(f"**❌ Blocked — {len(violations)} change(s) touch protected "
                   f"components without the required override.**")
        out.append("")
        out.append("These changes edit code marked as *not freely editable*. Under the "
                   "project's thesis, editing here is the easy-but-wrong path. **Do not keep "
                   "editing to make this check pass** — take one of the actions below.")
        out.append("")
        for v in violations:
            where = f"`{v.path}`" + (f" ({v.anchor})" if v.anchor else "")
            out.append(f"### {where} → `{v.slug}` · **{v.level}**")
            out.append("")
            out.append(_prescriptive(v, policy))
            out.append("")
    else:
        out.append("**✅ Passed — no editability violations.**")
        out.append("")
    if overridden:
        out.append("<details><summary>Allowed with override "
                   f"({len(overridden)})</summary>\n")
        for v in overridden:
            out.append(f"- `{v.path}` → `{v.slug}` ({v.level}) — override: {', '.join(v.got)}")
        out.append("\n</details>")
        out.append("")
    if nudges:
        out.append("<details><summary>Suggestions "
                   f"({len(nudges)}) — new/green code near thesis-critical components</summary>\n")
        for v in nudges:
            out.append(f"- `{v.path}` sits near red `{v.slug}` — consider labeling it.")
        out.append("\n</details>")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Editability Policy gate (see SPEC.md).")
    ap.add_argument("--repo-root", default=".", help="Repository root.")
    ap.add_argument("--policy", default="arch.policy.json", help="Gate config path.")
    ap.add_argument("--base", help="Base git ref (e.g. origin/main).")
    ap.add_argument("--head", default="HEAD", help="Head git ref.")
    ap.add_argument("--changed-file", help="File with newline-separated changed paths "
                                           "(overrides git diff; disables marker/guard checks).")
    ap.add_argument("--pr-body-file", help="File containing the PR description text.")
    ap.add_argument("--pr-body", help="PR description text (inline).")
    ap.add_argument("--approved-owners", default="", help="Comma-separated owners who approved.")
    ap.add_argument("--labels", default="", help="Comma-separated PR labels.")
    ap.add_argument("--env-ack", action="store_true", help="Local override acknowledgement.")
    ap.add_argument("--json", action="store_true", help="Emit JSON verdicts.")
    ap.add_argument("--comment-out", metavar="FILE",
                    help="Write a Markdown PR-comment body to FILE (for posting to the PR).")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    policy = load_policy(repo_root, Path(args.policy))

    base, head = args.base, args.head
    marker_aware = True
    if args.changed_file:
        changed = [ln.strip() for ln in Path(args.changed_file).read_text().splitlines() if ln.strip()]
        marker_aware = bool(args.base)  # markers need git refs
    elif args.base:
        changed = git_changed(args.base, args.head, repo_root)
    else:
        print("[arch_gate] ERROR: provide --base or --changed-file", file=sys.stderr)
        return 2

    pr_body = args.pr_body or (Path(args.pr_body_file).read_text() if args.pr_body_file else "")
    ctx = PRContext(
        pr_body=pr_body,
        approved_owners=[s.strip() for s in args.approved_owners.split(",") if s.strip()],
        labels=[s.strip() for s in args.labels.split(",") if s.strip()],
        env_ack=args.env_ack,
    )

    verdicts = run_gate(changed, policy, ctx,
                        base=base if marker_aware else None,
                        head=head if marker_aware else None,
                        repo_root=repo_root if marker_aware else None)
    violations = [v for v in verdicts if v.status == "violation"]

    if args.comment_out:
        Path(args.comment_out).write_text(_fmt_pr_comment(verdicts, policy) + "\n")

    if args.json:
        print(json.dumps([v.__dict__ for v in verdicts], indent=2))
    else:
        if verdicts:
            print(_fmt(verdicts, policy))
        else:
            print("[arch_gate] no changed files to check.")
        print()
        if violations:
            print(f"[arch_gate] BLOCKED: {len(violations)} change(s) to protected "
                  f"components lack a required override signal. See ↳ lines above for the fix.")
        else:
            print("[arch_gate] OK: no editability violations.")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
