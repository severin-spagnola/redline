#!/usr/bin/env python3
"""
pretooluse_arch_guard.py — Claude Code PreToolUse hook enforcing the Editability
Policy (SPEC.md) BEFORE a file edit happens, as an in-session guardrail.

WHAT THIS IS (and is not)
-------------------------
This is the OPTIONAL *interceptor* layer from SPEC.md §7.3 / DESIGN.md
("Enforcement + the anti-thrash feedback loop"). Claude Code runs it before it
executes a Write / Edit / NotebookEdit. If the target file belongs to a
protected component (`conditional` / `never` / `frozen`), the hook refuses the
edit at attempt time with a PRESCRIPTIVE message, so the doomed edit never
happens and no agent round-trip is wasted. That is the "stop the thrash
in-session" ergonomic: the constraint is re-presented, with its reason, at the
moment the agent acts — the agent does not have to *remember* "don't touch X".

  THIS HOOK IS ERGONOMICS, NOT THE GUARANTEE.

The real, un-bypassable enforcement is the CI gate (arch_gate.py) running on a PR
to a protected branch: a violation fails a required status check and the merge
cannot happen. A hook is per-agent and, as the research notes, an agent can
sometimes edit its own hooks — so this layer MUST NOT be relied on for
enforcement. It reduces wasted work; arch_gate.py is what actually holds.

FAIL-OPEN GUARANTEE (critical)
------------------------------
This hook NEVER fails closed. On ANY internal error — no policy file found, a
malformed tool-call payload, a JSON/parse error, an import failure, anything
unexpected — it prints a one-line warning to stderr and exits 0 (allow). A
broken guardrail must never brick the user's ability to edit their own code. The
only non-zero exit is a *deliberate* block of a genuinely-protected path in
"block" mode. Everything else is exit 0.

PRE-EDIT LIMITATIONS (documented on purpose)
--------------------------------------------
Unlike arch_gate.py (which sees the exact diff), a PreToolUse hook fires BEFORE
the edit, so it cannot know which lines will change. Two consequences:

  1. Component level: the target path is resolved to its component with
     arch_gate.resolve_component and judged on that component's editability
     level. If the whole file/dir is protected, the edit is blocked.

  2. Marker regions (`# arch:begin <slug> never|frozen ...`): the hook cannot
     tell whether the pending edit lands inside a guarded region. It therefore
     acts CONSERVATIVELY: if the target file contains ANY never/frozen marker
     region, the edit is treated as a would-be edit to that guarded region and
     blocked (in block mode). This can over-block edits to unguarded lines of a
     file that merely *contains* a guarded region — that is intentional
     caution at the interceptor layer; the CI gate does the precise,
     line-accurate check on the actual diff (and treats deleting a guard as
     editing the guarded thing, which a pre-edit hook cannot see).

Because there is no PR yet at edit time, there is also no override to evaluate
(no `## Arch-Override` block, no code-owner approval, no label exist yet). So the
prescriptive message tells the agent to pick a DIFFERENT approach / target rather
than "add an override" — the override path lives at the PR/gate stage, not here.

CONFIGURATION (env)
-------------------
  ARCH_POLICY       Absolute path to arch.policy.json. If unset, the hook walks
                    up from the target file's directory (then cwd) to find one.
  ARCH_GUARD_MODE   "block" (default): exit 2 on a protected target (Claude Code
                    blocks the tool call and shows stderr to the agent).
                    "warn": print the same message to stderr but exit 0 (advisory
                    only — the edit proceeds). Use this to run informational-only.
  ARCH_GUARD_DEBUG  If set to a truthy value, extra diagnostics go to stderr.

Stdlib-only, Python 3.11. See SPEC.md, DESIGN.md, and arch_gate.py (whose
load_policy / resolve_component / scan_markers / _prescriptive this reuses —
it does NOT reimplement policy logic).

Exit codes (Claude Code PreToolUse convention):
    0  allow the tool call (also every fail-open path)
    2  block the tool call; stderr is shown to the agent (block mode, protected)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

# ---------------------------------------------------------------------------
# Import the reference gate's logic. We REUSE it (SPEC/DESIGN mandate: one
# policy source, no reimplementation). The hook lives at
# tools/arch/spec/hooks/, so arch_gate.py is one directory up.
# ---------------------------------------------------------------------------
_THIS = Path(__file__).resolve()
_SPEC_DIR = _THIS.parent.parent          # .../tools/arch/spec
if str(_SPEC_DIR) not in sys.path:
    sys.path.insert(0, str(_SPEC_DIR))

MODE = (os.environ.get("ARCH_GUARD_MODE") or "block").strip().lower()
_DEBUG = bool((os.environ.get("ARCH_GUARD_DEBUG") or "").strip())

# arch_gate itself is imported inside main() so an import failure fails OPEN
# rather than crashing at module load. Names are resolved there.


def _warn(msg: str) -> None:
    """Advisory line to stderr (does not, by itself, block anything)."""
    sys.stderr.write(f"[arch-guard] {msg}\n")


def _debug(msg: str) -> None:
    if _DEBUG:
        sys.stderr.write(f"[arch-guard][debug] {msg}\n")


def _allow(reason: str = "") -> int:
    """Fail-open / permit path. Always exit 0."""
    if reason:
        _debug(f"allow: {reason}")
    return 0


# ---------------------------------------------------------------------------
# Extract the target file path from the PreToolUse stdin payload.
#
# Claude Code passes { tool_name, tool_input: {...}, cwd, session_id, ... }.
# The field holding the path varies by tool and CC version, so we look in the
# documented spots first, then defensively search the JSON for any plausible
# path-like key. This is best-effort by design: if we cannot find a path, we
# fail OPEN (return None -> allow).
# ---------------------------------------------------------------------------
_PATH_KEYS = ("file_path", "path", "notebook_path", "filePath", "notebookPath")


def _first_path_in(obj: Any) -> Optional[str]:
    """Best-effort recursive search for a value under a plausible *path* key."""
    if isinstance(obj, dict):
        # Prefer the known key names at this level.
        for k in _PATH_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v
        # Then any key whose name contains 'path' with a string value.
        for k, v in obj.items():
            if isinstance(k, str) and "path" in k.lower() and isinstance(v, str) and v.strip():
                # skip obviously-non-target paths from the envelope
                if k in ("transcript_path", "cwd", "hook_event_name"):
                    continue
                return v
        # Recurse into nested dicts/lists.
        for v in obj.values():
            found = _first_path_in(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _first_path_in(v)
            if found:
                return found
    return None


def extract_target_path(payload: dict) -> Optional[str]:
    """Return the edit target path from a PreToolUse payload, or None."""
    tin = payload.get("tool_input")
    if isinstance(tin, dict):
        for k in _PATH_KEYS:
            v = tin.get(k)
            if isinstance(v, str) and v.strip():
                return v
    # Other common shapes: params.*, top-level file_path, or anywhere in the JSON.
    params = payload.get("params")
    if isinstance(params, dict):
        for k in _PATH_KEYS:
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                return v
    for k in _PATH_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # Last resort: search the whole payload (minus the known envelope keys).
    return _first_path_in({k: v for k, v in payload.items()
                           if k not in ("transcript_path", "cwd", "hook_event_name",
                                        "session_id", "prompt_id", "permission_mode",
                                        "tool_name")})


# ---------------------------------------------------------------------------
# Locate arch.policy.json: ARCH_POLICY override, else walk up from the target's
# directory, else walk up from cwd.
# ---------------------------------------------------------------------------
def find_policy(target: Path, cwd: Path) -> Optional[Path]:
    env = os.environ.get("ARCH_POLICY")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        _warn(f"ARCH_POLICY={env!r} is not a file; falling back to walking up.")

    def _walk_up(start: Path) -> Optional[Path]:
        start = start if start.is_dir() else start.parent
        for d in [start, *start.parents]:
            cand = d / "arch.policy.json"
            if cand.is_file():
                return cand
        return None

    return _walk_up(target) or _walk_up(cwd)


# ---------------------------------------------------------------------------
# Repo root for the policy = the directory containing arch.policy.json. The
# annotation glob in the config is resolved relative to that root (same as
# arch_gate's CLI --repo-root default sitting beside the policy).
# ---------------------------------------------------------------------------
def _rel_to_root(target: Path, root: Path) -> Optional[str]:
    """Repo-relative POSIX path of target under root, or None if outside root."""
    try:
        rel = target.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _protected_markers(target: Path, ag) -> List[Any]:
    """Never/frozen marker regions physically present in the target file.

    Pre-edit we cannot know which lines change, so ANY never/frozen region in
    the file makes the edit a would-be edit to a guarded region (conservative;
    documented limitation). Returns arch_gate MarkerRegion objects.
    """
    try:
        if not target.is_file():
            return []
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        regions = ag.scan_markers(text, target.name)
    except Exception as e:  # never let a marker-scan bug block an edit
        _debug(f"scan_markers failed: {e}")
        return []
    return [r for r in regions if getattr(r, "level", "") in ("never", "frozen")]


def _pre_edit_message(slug: str, level: str, reason: str, where: str,
                      base_prescriptive: str) -> str:
    """Prescriptive, pre-edit-appropriate block message.

    Reuses arch_gate._prescriptive's descriptive core (component, level meaning,
    the 'why'/reason) but frames the CALL TO ACTION for edit-time: there is no PR
    or override to satisfy yet (SPEC §7.3), so the right move is to choose a
    different target, not to 'add an override'. The gate stage is where an
    override would apply — we surface that so the agent knows the downstream
    consequence, without inviting it to route around this component now.
    """
    meaning = {
        "frozen": ("FROZEN — the hardest lock (e.g. calibration ground truth). "
                   "It is not to be modified in place at all; if it is genuinely "
                   "wrong it is retired and re-admitted through the full gate, "
                   "never patched here."),
        "never":  ("NEVER — a do-not-touch component. The norm is 'don't'; editing "
                   "it is the easy-but-wrong path under a vague prompt."),
        "conditional": ("CONDITIONAL — editable only under its stated edit_rule, "
                        "which a human/agent must affirm holds."),
    }.get(level, f"{level} — protected.")
    why = f"\n  Why it is protected: {reason}" if reason else ""
    lines = [
        f"BLOCKED (pre-edit): this edit targets `{slug}` ({level}){where}.",
        f"  What `{level}` means: {meaning}{why}",
        "",
        "  This is an in-session guardrail. Do NOT edit here and do NOT keep "
        "retrying to make the edit land. Instead:",
        f"    - Find the CORRECT place for this change — the symptom you are "
        f"chasing is almost certainly downstream of `{slug}`, not inside it. Edit "
        f"that instead.",
        "    - If you believe this component genuinely must change, that decision "
        "does not happen at edit time: it requires a deliberate, human-visible "
        "override at the PR / CI-gate stage (an `## Arch-Override` block stating "
        "the reason, and/or a code-owner approval, per the policy). The CI gate "
        "(arch_gate.py) is what actually enforces this on merge; this hook only "
        "stops the wasted attempt now.",
    ]
    # Append the gate's own descriptive line so the message stays consistent
    # with what the agent will see at the gate (single policy source).
    if base_prescriptive:
        # Keep just the descriptive lead-in (strip the PR-diff-centric tail that
        # assumes a diff already exists).
        lead = base_prescriptive.split(". To pass:")[0].strip()
        if lead:
            lines += ["", f"  At the gate this reads as: {lead}."]
    return "\n".join(lines)


def evaluate(target: Path, policy_path: Path, ag) -> Optional[str]:
    """Return a prescriptive block-message if the target is protected, else None.

    None  -> allow (editable / unannotated-pass / outside policy scope / any
             internal issue -> fail open).
    str   -> the message to show; caller decides block(exit 2) vs warn(exit 0)
             based on ARCH_GUARD_MODE.
    """
    root = policy_path.parent
    try:
        policy = ag.load_policy(root, policy_path)
    except SystemExit as e:
        # arch_gate raises SystemExit on bad policy/config. Fail OPEN.
        _warn(f"could not load policy {policy_path} ({e}); allowing edit (fail-open).")
        return None
    except Exception as e:
        _warn(f"unexpected error loading policy ({e}); allowing edit (fail-open).")
        return None

    rel = _rel_to_root(target, root)
    if rel is None:
        # Target lives outside the policy's repo root -> not in scope -> allow.
        _debug(f"target {target} is outside policy root {root}; allowing.")
        return None

    levels_cfg = policy.levels_cfg()

    # (1) Component-level decision via the reused resolver.
    comp = ag.resolve_component(rel, policy.components)
    if comp is not None:
        level = comp.editability
        on_change = (levels_cfg.get(level, {}) or {}).get("on_change", "pass")
        # Respect the repo's config: only block levels whose on_change needs an
        # override (require/block). A repo that sets a level to "pass" is honored
        # (DESIGN non-negotiable: no hardcoded level policy). Default protected
        # levels are conditional/never/frozen.
        if on_change in ("require", "block"):
            v = ag.Verdict(
                path=rel, slug=comp.slug, level=level, status="violation",
                needed=(levels_cfg.get(level, {}) or {}).get("override", []),
                reason=comp.edit_rule, source="component",
            )
            try:
                base = ag._prescriptive(v, policy)
            except Exception as e:
                _debug(f"_prescriptive failed: {e}")
                base = ""
            where = f" (component owns {rel})"
            return _pre_edit_message(comp.slug, level, comp.edit_rule, where, base)
        _debug(f"{rel} -> component {comp.slug} ({level}, on_change={on_change}) -> allow")

    # (2) Marker regions: conservative pre-edit block if the file contains any
    #     never/frozen guarded region (we cannot see the pending line numbers).
    regions = _protected_markers(target, ag)
    if regions:
        reg = regions[0]
        lvl = getattr(reg, "level", "never")
        on_change = (levels_cfg.get(lvl, {}) or {}).get("on_change", "block")
        if on_change in ("require", "block"):
            v = ag.Verdict(
                path=rel, slug=getattr(reg, "slug", "(marker)"), level=lvl,
                status="violation",
                needed=(levels_cfg.get(lvl, {}) or {}).get("override", []),
                reason=getattr(reg, "reason", ""), source="marker",
                anchor=f"L{getattr(reg,'begin_line','?')}-{getattr(reg,'end_line','?')}",
            )
            try:
                base = ag._prescriptive(v, policy)
            except Exception as e:
                _debug(f"_prescriptive failed: {e}")
                base = ""
            others = "" if len(regions) == 1 else f" (+{len(regions)-1} more guarded region(s))"
            where = (f" — file contains a guarded region "
                     f"{v.anchor}{others}; pre-edit the hook cannot tell if your "
                     f"edit lands inside it, so it blocks conservatively")
            return _pre_edit_message(v.slug, lvl, v.reason, where, base)

    return None  # editable / unannotated-pass / no protected marker -> allow


def main() -> int:
    # --- read + parse stdin (fail OPEN on anything wrong) ---------------------
    try:
        raw = sys.stdin.read()
    except Exception as e:
        _warn(f"could not read stdin ({e}); allowing edit (fail-open).")
        return _allow()
    if not raw or not raw.strip():
        return _allow("empty stdin")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        _warn(f"stdin is not valid JSON ({e}); allowing edit (fail-open).")
        return _allow()
    if not isinstance(payload, dict):
        return _allow("stdin JSON is not an object")

    # --- import the gate logic (fail OPEN if it can't be imported) ------------
    try:
        import arch_gate as ag  # noqa: N813  (module is the reused policy engine)
    except Exception as e:
        _warn(f"could not import arch_gate ({e}); allowing edit (fail-open).")
        return _allow()

    # --- find the edit target ------------------------------------------------
    target_str = extract_target_path(payload)
    if not target_str:
        return _allow("no target file path found in payload")
    cwd = Path(payload.get("cwd") or os.getcwd())
    target = Path(target_str)
    if not target.is_absolute():
        target = (cwd / target)

    # --- locate the policy (fail OPEN if none) -------------------------------
    policy_path = find_policy(target, cwd)
    if policy_path is None:
        return _allow(f"no arch.policy.json found from {target} or {cwd}")

    # --- evaluate; wrap EVERYTHING so no bug can fail closed ------------------
    try:
        message = evaluate(target, policy_path, ag)
    except Exception as e:  # absolute backstop
        _warn(f"unexpected error during evaluation ({e}); allowing edit (fail-open).")
        return _allow()

    if not message:
        return _allow()  # protected? no -> allow, print nothing

    # Protected target. block (exit 2) vs warn (exit 0).
    if MODE == "warn":
        sys.stderr.write(message + "\n")
        sys.stderr.write("[arch-guard] mode=warn -> advisory only; the edit is "
                         "allowed to proceed. The CI gate still enforces this on merge.\n")
        return 0
    # default: block
    sys.stderr.write(message + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
