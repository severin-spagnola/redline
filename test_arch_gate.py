"""
Pytest suite for arch_gate.py — the Editability Policy gate (SPEC.md / DESIGN.md).

Behavior contract under test lives in:
    - SPEC.md   (rules, levels, override signals, resolution, validation)
    - DESIGN.md (gate semantics, marker anchoring, guard-deletion, nudges)

The module under test is a single stdlib-only file (Python 3.11), NOT a package.
We load it by absolute path (relative to THIS test file) so the suite runs from
any cwd. The gate is treated as read-only; where a real bug is found it is
reported and marked xfail rather than worked around.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load arch_gate.py by absolute path (robust to any cwd).
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_GATE_PATH = _HERE / "arch_gate.py"

_spec = importlib.util.spec_from_file_location("arch_gate", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
ag = importlib.util.module_from_spec(_spec)
# register so dataclasses / __module__ resolve cleanly
sys.modules["arch_gate"] = ag
_spec.loader.exec_module(ag)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# A canonical, spec-conformant policy config (mirrors examples/arch.policy.json):
#   editable    -> pass
#   conditional -> require (any of justification|code_owner)
#   never       -> block   (any of justification|code_owner)
#   frozen      -> block   (ALL of justification|code_owner|strong_label)
BASE_POLICY_CFG = {
    "spec_version": "0.1",
    "annotation_glob": "**/redline.meta.json",
    "protected_branches": ["main"],
    "unannotated_policy": "pass",
    "override_mode": "any",
    "levels": {
        "editable": {"on_change": "pass"},
        "conditional": {
            "on_change": "require",
            "override": ["justification", "code_owner"],
        },
        "never": {
            "on_change": "block",
            "override": ["justification", "code_owner"],
        },
        "frozen": {
            "on_change": "block",
            "override": ["justification", "code_owner", "strong_label"],
            "override_mode": "all",
        },
    },
    "overrides": {
        "justification": {"type": "pr_body_block", "heading": "Arch-Override"},
        "code_owner": {"type": "codeowners_review"},
        "strong_label": {"type": "label", "name": "arch-frozen-approved"},
    },
}

# A PR body that satisfies the justification (pr_body_block) signal.
GOOD_PR_BODY = "## Arch-Override\ncomponent: some-thing\nreason: this edit is necessary because X\n"


def write_policy(tmp_path: Path, cfg: dict, name: str = "arch.policy.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(cfg))
    return p


def write_annotation(tmp_path: Path, rel_dir: str, components, name: str = "redline.meta.json") -> Path:
    """Write a redline.meta.json annotation file under rel_dir with the given component(s)."""
    d = tmp_path / rel_dir if rel_dir else tmp_path
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if isinstance(components, list):
        payload = {"components": components}
    else:
        payload = components
    p.write_text(json.dumps(payload))
    return p


def load_base_policy(tmp_path: Path, cfg: dict | None = None) -> "ag.Policy":
    """Load a policy from tmp_path, defaulting to a copy of BASE_POLICY_CFG."""
    cfg = json.loads(json.dumps(BASE_POLICY_CFG)) if cfg is None else cfg
    policy_path = write_policy(tmp_path, cfg)
    return ag.load_policy(tmp_path, policy_path)


def ctx(**kw) -> "ag.PRContext":
    return ag.PRContext(**kw)


_GIT = shutil.which("git")
requires_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def git_repo(tmp_path: Path) -> Path:
    """Init a git repo at tmp_path with a configured identity; return the root."""
    if _GIT is None:  # pragma: no cover - guarded by requires_git
        pytest.skip("git not available")
    root = tmp_path

    def g(*args):
        return subprocess.run(
            [_GIT, *args], cwd=str(root), check=True, capture_output=True, text=True
        )

    g("init", "-q")
    g("config", "user.email", "test@example.com")
    g("config", "user.name", "Arch Gate Test")
    g("config", "commit.gpgsign", "false")
    return root


def git_commit(root: Path, msg: str) -> str:
    subprocess.run([_GIT, "add", "-A"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run([_GIT, "commit", "-qm", msg], cwd=str(root), check=True, capture_output=True, text=True)
    out = subprocess.run(
        [_GIT, "rev-parse", "HEAD"], cwd=str(root), check=True, capture_output=True, text=True
    ).stdout.strip()
    return out


# ===========================================================================
# 0. Module smoke — public surface exists
# ===========================================================================


def test_public_surface_present():
    for name in (
        "load_policy", "resolve_component", "scan_markers", "changed_line_ranges",
        "run_gate", "evaluate_signal", "overrides_satisfied", "_prescriptive",
        "_fmt_pr_comment", "Component", "Policy", "Verdict", "PRContext", "MarkerRegion",
    ):
        assert hasattr(ag, name), f"arch_gate is missing public symbol {name!r}"


# ===========================================================================
# 1. resolve_component (SPEC §2.3)
# ===========================================================================


def _comp(slug, level="never", paths=None, dir_scope="", connects_to=None):
    return ag.Component(
        slug=slug,
        editability=level,
        paths=list(paths or []),
        dir_scope=dir_scope,
        connects_to=list(connects_to or []),
    )


def test_resolve_component_most_specific_glob_wins():
    # Two components both match; longest matching glob (more specific) wins.
    broad = _comp("broad", paths=["engine/**"])
    narrow = _comp("narrow", paths=["engine/sable/spec_ingest/*.py"])
    comps = [broad, narrow]
    got = ag.resolve_component("engine/sable/spec_ingest/semantic_templates.py", comps)
    assert got is not None and got.slug == "narrow"


def test_resolve_component_nearest_ancestor_dir_fallback():
    # No paths glob matches -> deepest annotation-file directory that is an
    # ancestor wins (nearest-file-wins, like .gitignore nesting).
    shallow = _comp("shallow", paths=[], dir_scope="engine")
    deep = _comp("deep", paths=[], dir_scope="engine/sable/cegis")
    comps = [shallow, deep]
    got = ag.resolve_component("engine/sable/cegis/enumerate.py", comps)
    assert got is not None and got.slug == "deep"
    # A file under only the shallow scope falls back to shallow.
    got2 = ag.resolve_component("engine/other/thing.py", comps)
    assert got2 is not None and got2.slug == "shallow"


def test_resolve_component_unannotated_returns_none():
    comps = [_comp("c", paths=["engine/**"], dir_scope="engine")]
    assert ag.resolve_component("docs/readme.md", comps) is None


def test_resolve_component_glob_beats_dir_scope():
    # A paths-glob match (rule 1) takes precedence over a dir_scope ancestor (rule 2).
    by_dir = _comp("by-dir", paths=[], dir_scope="src")
    by_glob = _comp("by-glob", paths=["src/special.py"], dir_scope="")
    comps = [by_dir, by_glob]
    got = ag.resolve_component("src/special.py", comps)
    assert got is not None and got.slug == "by-glob"


def test_resolve_component_root_dir_scope_is_catchall():
    root = _comp("root", paths=[], dir_scope="")  # root annotation = ancestor of everything
    comps = [root]
    got = ag.resolve_component("any/deep/path/x.py", comps)
    assert got is not None and got.slug == "root"


# ===========================================================================
# 2. Level behaviors from config (SPEC §4.2 / DESIGN gate semantics)
# ===========================================================================


def _run_one(policy, path, level_paths_slug=("thing",), **ctx_kw):
    return ag.run_gate([path], policy, ctx(**ctx_kw))


def test_level_editable_passes(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "editz", "editability": "editable",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/free.py"]}])
    policy = load_base_policy(tmp_path)
    v = ag.run_gate(["src/free.py"], policy, ctx())
    assert len(v) == 1 and v[0].status == "pass" and v[0].level == "editable"


def test_level_conditional_blocked_without_override(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "condz", "editability": "conditional",
                                        "edit_rule": "only under X", "description": "-",
                                        "paths": ["src/cond.py"]}])
    policy = load_base_policy(tmp_path)
    v = ag.run_gate(["src/cond.py"], policy, ctx())
    assert [x for x in v if not x.nudge][0].status == "violation"


def test_level_conditional_passes_with_override(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "condz", "editability": "conditional",
                                        "edit_rule": "only under X", "description": "-",
                                        "paths": ["src/cond.py"]}])
    policy = load_base_policy(tmp_path)
    v = ag.run_gate(["src/cond.py"], policy, ctx(pr_body=GOOD_PR_BODY))
    assert [x for x in v if not x.nudge][0].status == "allowed_with_override"


def test_level_never_blocked_without_override(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "nevz", "editability": "never",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/nev.py"]}])
    policy = load_base_policy(tmp_path)
    v = [x for x in ag.run_gate(["src/nev.py"], policy, ctx()) if not x.nudge]
    assert v[0].status == "violation" and v[0].level == "never"


def test_level_never_passes_with_single_override(tmp_path):
    # never uses override_mode "any" (default) -> one signal suffices.
    write_annotation(tmp_path, "src", [{"component": "nevz", "editability": "never",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/nev.py"]}])
    policy = load_base_policy(tmp_path)
    v = [x for x in ag.run_gate(["src/nev.py"], policy, ctx(approved_owners=["@alice"])) if not x.nudge]
    assert v[0].status == "allowed_with_override"


def test_level_frozen_all_mode_blocked_with_only_one_signal(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "froz", "editability": "frozen",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/frz.py"]}])
    policy = load_base_policy(tmp_path)
    # Only justification present; frozen needs ALL three (justification, code_owner, strong_label).
    v = [x for x in ag.run_gate(["src/frz.py"], policy, ctx(pr_body=GOOD_PR_BODY)) if not x.nudge]
    assert v[0].status == "violation" and v[0].level == "frozen"


def test_level_frozen_all_mode_passes_with_all_signals(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "froz", "editability": "frozen",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/frz.py"]}])
    policy = load_base_policy(tmp_path)
    v = [x for x in ag.run_gate(
        ["src/frz.py"], policy,
        ctx(pr_body=GOOD_PR_BODY, approved_owners=["@alice"],
            labels=["arch-frozen-approved"])) if not x.nudge]
    assert v[0].status == "allowed_with_override"
    assert set(v[0].got) == {"justification", "code_owner", "strong_label"}


# ===========================================================================
# 3. Override signals + override_mode any/all (SPEC §5)
# ===========================================================================


def test_signal_pr_body_block_with_reason_satisfies():
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    assert ag.evaluate_signal("j", cfg, ctx(pr_body=GOOD_PR_BODY)) is True


def test_signal_pr_body_block_absent_does_not_satisfy():
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    assert ag.evaluate_signal("j", cfg, ctx(pr_body="just some description, no block")) is False


def test_signal_pr_body_block_empty_body_does_not_satisfy():
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    # Heading present but the block is empty (immediately followed by another heading).
    body = "## Arch-Override\n## Something Else\nblah\n"
    assert ag.evaluate_signal("j", cfg, ctx(pr_body=body)) is False


def test_signal_codeowners_review_satisfied_by_nonempty_owners():
    cfg = {"type": "codeowners_review"}
    assert ag.evaluate_signal("c", cfg, ctx(approved_owners=["@alice"])) is True
    assert ag.evaluate_signal("c", cfg, ctx(approved_owners=[])) is False


def test_signal_label_matched():
    cfg = {"type": "label", "name": "arch-frozen-approved"}
    assert ag.evaluate_signal("l", cfg, ctx(labels=["arch-frozen-approved"])) is True
    assert ag.evaluate_signal("l", cfg, ctx(labels=["other"])) is False


def test_signal_env_ack():
    cfg = {"type": "env_ack"}
    assert ag.evaluate_signal("e", cfg, ctx(env_ack=True)) is True
    assert ag.evaluate_signal("e", cfg, ctx(env_ack=False)) is False


def test_signal_unknown_type_fails_closed():
    cfg = {"type": "made_up_type"}
    assert ag.evaluate_signal("x", cfg, ctx(env_ack=True)) is False


def test_overrides_satisfied_mode_any():
    ov = BASE_POLICY_CFG["overrides"]
    ok, got = ag.overrides_satisfied(
        ["justification", "code_owner"], "any", ov, ctx(approved_owners=["@a"]))
    assert ok is True and got == ["code_owner"]


def test_overrides_satisfied_mode_all_needs_every_signal():
    ov = BASE_POLICY_CFG["overrides"]
    # only one of two present
    ok, got = ag.overrides_satisfied(
        ["justification", "code_owner"], "all", ov, ctx(approved_owners=["@a"]))
    assert ok is False and got == ["code_owner"]
    # both present
    ok2, got2 = ag.overrides_satisfied(
        ["justification", "code_owner"], "all", ov,
        ctx(pr_body=GOOD_PR_BODY, approved_owners=["@a"]))
    assert ok2 is True and set(got2) == {"justification", "code_owner"}


def test_overrides_satisfied_all_mode_empty_list_is_false():
    # "all" with an empty signal list must not vacuously pass.
    ok, got = ag.overrides_satisfied([], "all", BASE_POLICY_CFG["overrides"], ctx())
    assert ok is False and got == []


# ===========================================================================
# 4. pr_body_block parsing detail (via evaluate_signal / _pr_body_block_present)
# ===========================================================================


def test_pr_body_block_case_insensitive_heading():
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    body = "## arch-OVERRIDE\nreason: because\n"
    assert ag.evaluate_signal("j", cfg, ctx(pr_body=body)) is True


def test_pr_body_block_truly_empty_block_is_none():
    # Heading immediately followed by the next heading => empty block => None.
    body = "## Arch-Override\n## Next\n"
    assert ag._pr_body_block_present(body, "Arch-Override") is None


def test_pr_body_block_present_but_empty_reason_value_is_none():
    # A `reason:` line with an EMPTY value does NOT count as a justification:
    # arch_gate.py returns None ("a reason: line must carry an actual value").
    body = "## Arch-Override\nreason:\n"  # empty reason VALUE
    assert ag._pr_body_block_present(body, "Arch-Override") is None
    # ... and therefore the signal is not satisfied.
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    assert ag.evaluate_signal("j", cfg, ctx(pr_body=body)) is False


def test_pr_body_block_ignores_other_headings():
    cfg = {"type": "pr_body_block", "heading": "Arch-Override"}
    body = "## Some Other Heading\nreason: this is not an override block\n"
    assert ag.evaluate_signal("j", cfg, ctx(pr_body=body)) is False


def test_pr_body_block_stops_at_next_heading():
    # The captured block ends at the next heading; a reason after that heading
    # does not belong to the override block.
    body = "## Arch-Override\n## Next\nreason: belongs to next\n"
    assert ag._pr_body_block_present(body, "Arch-Override") is None


def test_pr_body_block_nonempty_freetext_without_reason_still_counts():
    # DESIGN/impl: a non-empty block counts even without an explicit reason: line.
    body = "## Arch-Override\nthis change is deliberate and reviewed\n"
    got = ag._pr_body_block_present(body, "Arch-Override")
    assert got is not None and "deliberate" in got


# ===========================================================================
# 5. scan_markers (DESIGN intra-file anchoring)
# ===========================================================================


def test_scan_markers_basic_region_with_reason():
    text = (
        "line1\n"
        '# arch:begin signal-clause never reason="no lookahead"\n'
        "signal = fast > slow\n"
        "# arch:end signal-clause\n"
        "tail\n"
    )
    regions = ag.scan_markers(text, "f.py")
    assert len(regions) == 1
    r = regions[0]
    assert r.slug == "signal-clause"
    assert r.level == "never"
    assert r.reason == "no lookahead"
    assert r.begin_line == 2
    assert r.end_line == 4
    assert r.path == "f.py"


def test_scan_markers_multiple_regions():
    text = (
        "# arch:begin a never\n"
        "x\n"
        "# arch:end a\n"
        "# arch:begin b conditional\n"
        "y\n"
        "# arch:end b\n"
    )
    regions = ag.scan_markers(text, "f.py")
    slugs = sorted(r.slug for r in regions)
    assert slugs == ["a", "b"]
    by = {r.slug: r for r in regions}
    assert by["a"].begin_line == 1 and by["a"].end_line == 3
    assert by["b"].begin_line == 4 and by["b"].end_line == 6
    assert by["b"].level == "conditional"


def test_scan_markers_nested_regions_nearest_end_wins():
    # end for the same slug closes the nearest matching open marker.
    text = (
        "# arch:begin outer never\n"
        "a\n"
        "# arch:begin inner frozen\n"
        "b\n"
        "# arch:end inner\n"
        "c\n"
        "# arch:end outer\n"
    )
    regions = ag.scan_markers(text, "f.py")
    by = {r.slug: r for r in regions}
    assert by["inner"].begin_line == 3 and by["inner"].end_line == 5
    assert by["outer"].begin_line == 1 and by["outer"].end_line == 7
    assert by["inner"].level == "frozen"


def test_scan_markers_unclosed_guards_to_eof():
    text = (
        "head\n"
        "# arch:begin lonely never reason=\"unterminated\"\n"
        "body1\n"
        "body2\n"
    )
    regions = ag.scan_markers(text, "f.py")
    assert len(regions) == 1
    r = regions[0]
    assert r.slug == "lonely"
    assert r.begin_line == 2
    assert r.end_line == 4  # EOF (total line count)
    assert r.reason == "unterminated"


def test_scan_markers_no_markers_returns_empty():
    assert ag.scan_markers("just\nplain\ncode\n", "f.py") == []


# ===========================================================================
# 6. changed_line_ranges + marker hit detection on a REAL git repo
# ===========================================================================


@requires_git
def test_changed_line_ranges_reports_edited_hunk(tmp_path):
    root = git_repo(tmp_path)
    (root / "f.py").write_text("a\nb\nc\nd\n")
    base = git_commit(root, "base")
    (root / "f.py").write_text("a\nCHANGED\nc\nd\n")
    head = git_commit(root, "edit line 2")
    ranges = ag.changed_line_ranges(base, head, "f.py", root)
    assert (2, 2) in ranges


@requires_git
def test_run_gate_marker_edit_inside_region_is_violation(tmp_path):
    root = git_repo(tmp_path)
    # Policy lives in the repo; annotation not strictly needed for marker path,
    # but the level config for `never` must exist (it does in BASE_POLICY_CFG).
    policy = load_base_policy(tmp_path)
    src = (
        "line1\n"
        "line2\n"
        '# arch:begin sig never reason="no lookahead"\n'
        "signal = fast > slow\n"
        "more\n"
        "# arch:end sig\n"
        "tail\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base with marker")
    # edit line 4 — inside the region (begin=3, end=6)
    (root / "f.py").write_text(src.replace("fast > slow", "fast >= slow"))
    head = git_commit(root, "edit inside region")

    verdicts = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    marker_v = [v for v in verdicts if v.source == "marker"]
    assert len(marker_v) == 1
    v = marker_v[0]
    assert v.status == "violation"
    assert v.slug == "sig"
    assert v.level == "never"
    assert v.anchor == "L3-6"
    assert v.reason == "no lookahead"


@requires_git
def test_run_gate_marker_edit_outside_region_no_violation(tmp_path):
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = (
        "line1\n"
        "line2\n"
        '# arch:begin sig never reason="x"\n'
        "guarded\n"
        "# arch:end sig\n"
        "tail\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base")
    # edit line 1 — OUTSIDE the region (3-5)
    (root / "f.py").write_text(src.replace("line1", "LINE1_EDITED"))
    head = git_commit(root, "edit outside region")

    verdicts = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    assert [v for v in verdicts if v.source == "marker"] == []
    # and no violation from any source
    assert [v for v in verdicts if v.status == "violation"] == []


@requires_git
def test_run_gate_marker_edit_inside_passes_with_override(tmp_path):
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = (
        "x\n"
        "# arch:begin sig never\n"
        "guarded = 1\n"
        "# arch:end sig\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base")
    (root / "f.py").write_text(src.replace("guarded = 1", "guarded = 2"))
    head = git_commit(root, "edit guarded")
    verdicts = ag.run_gate(["f.py"], policy, ctx(approved_owners=["@a"]),
                           base=base, head=head, repo_root=root)
    marker_v = [v for v in verdicts if v.source == "marker"]
    assert len(marker_v) == 1 and marker_v[0].status == "allowed_with_override"


# ===========================================================================
# 7. Guard-deletion (DESIGN: deleting a guard = violating it)
# ===========================================================================


@requires_git
def test_run_gate_guard_deletion_is_violation(tmp_path):
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = (
        "a\n"
        "b\n"
        '# arch:begin sig never reason="keep me"\n'
        "c\n"
        "d\n"
        "# arch:end sig\n"
        "e\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base with guard")
    # head removes the guard markers entirely (and the guarded lines)
    (root / "f.py").write_text("a\nb\nc\nd\ne\n")
    head = git_commit(root, "strip the guard")

    verdicts = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    gd = [v for v in verdicts if v.source == "guard-deletion"]
    assert len(gd) == 1
    v = gd[0]
    assert v.status == "violation"
    assert v.slug == "sig"
    assert v.level == "never"


@requires_git
def test_run_gate_guard_deletion_can_be_overridden(tmp_path):
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = (
        '# arch:begin sig never\n'
        "c\n"
        "# arch:end sig\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base")
    (root / "f.py").write_text("c\n")
    head = git_commit(root, "strip guard")
    verdicts = ag.run_gate(["f.py"], policy, ctx(pr_body=GOOD_PR_BODY),
                           base=base, head=head, repo_root=root)
    gd = [v for v in verdicts if v.source == "guard-deletion"]
    assert len(gd) == 1 and gd[0].status == "allowed_with_override"


@requires_git
def test_run_gate_guard_preserved_no_guard_deletion(tmp_path):
    # Marker unchanged base->head, no edit inside -> no guard-deletion, no violation.
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = (
        "top\n"
        "# arch:begin sig never\n"
        "guarded\n"
        "# arch:end sig\n"
        "bottom\n"
    )
    (root / "f.py").write_text(src)
    base = git_commit(root, "base")
    # edit a line OUTSIDE region, keep markers intact
    (root / "f.py").write_text(src.replace("bottom", "BOTTOM2"))
    head = git_commit(root, "edit outside, keep guard")
    verdicts = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    assert [v for v in verdicts if v.source == "guard-deletion"] == []
    assert [v for v in verdicts if v.status == "violation"] == []


# ===========================================================================
# 8. Nudges (DESIGN: non-blocking "label this?" for green code near red nodes)
# ===========================================================================


def test_nudge_for_green_file_under_red_component_dir(tmp_path):
    # A green file that sits UNDER a red component's dir_scope but resolves to a
    # *different* (green) component (via a deeper annotation dir) triggers a nudge.
    # NOTE: the red annotation's own directory is that component's implicit path
    # scope, so a file in that exact dir would resolve to the red comp itself (a
    # violation, not a nudge). To exercise the "near a red dir" nudge branch, the
    # green file must resolve to a deeper green component while still living under
    # the red dir_scope.
    write_annotation(tmp_path, "engine",
                     [{"component": "formal-templates", "editability": "never",
                       "edit_rule": "-", "description": "-",
                       "paths": ["engine/semantic_templates.py"]}])
    write_annotation(tmp_path, "engine/helpers",
                     [{"component": "helperz", "editability": "editable",
                       "edit_rule": "-", "description": "-",
                       "paths": ["engine/helpers/new_helper.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["engine/helpers/new_helper.py"], policy, ctx())
    nudges = [v for v in verdicts if v.nudge]
    assert len(nudges) == 1
    n = nudges[0]
    assert n.nudge is True
    assert n.status == "pass"
    assert n.slug == "helperz"  # resolves to the green comp, but is nudged (near red dir)


def test_nudge_for_connects_to_red_slug(tmp_path):
    # Green component that connects_to a red slug -> nudge.
    write_annotation(tmp_path, "a",
                     [{"component": "red-node", "editability": "never",
                       "edit_rule": "-", "description": "-",
                       "paths": ["a/red.py"]}])
    write_annotation(tmp_path, "b",
                     [{"component": "green-node", "editability": "editable",
                       "edit_rule": "-", "description": "-",
                       "paths": ["b/green.py"], "connects_to": ["red-node"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["b/green.py"], policy, ctx())
    nudges = [v for v in verdicts if v.nudge]
    assert len(nudges) == 1
    assert nudges[0].slug == "green-node"
    assert nudges[0].status == "pass"


def test_no_nudge_when_no_red_components(tmp_path):
    write_annotation(tmp_path, "src",
                     [{"component": "g", "editability": "editable",
                       "edit_rule": "-", "description": "-", "paths": ["src/x.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["src/x.py"], policy, ctx())
    assert [v for v in verdicts if v.nudge] == []


def test_red_file_itself_gets_no_nudge_only_violation(tmp_path):
    # Editing the red file itself is a violation, not merely a nudge.
    write_annotation(tmp_path, "engine",
                     [{"component": "red", "editability": "never",
                       "edit_rule": "-", "description": "-",
                       "paths": ["engine/red.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["engine/red.py"], policy, ctx())
    assert any(v.status == "violation" for v in verdicts if not v.nudge)
    # the red file itself is already guarded -> not nudged
    assert [v for v in verdicts if v.nudge and v.path == "engine/red.py"] == []


# ===========================================================================
# 9. unannotated_policy honored (SPEC §4.1)
# ===========================================================================


def test_unannotated_policy_pass(tmp_path):
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    cfg["unannotated_policy"] = "pass"
    # a component exists but does not match the changed file
    write_annotation(tmp_path, "src", [{"component": "c", "editability": "never",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/guarded.py"]}])
    policy = load_base_policy(tmp_path, cfg)
    verdicts = [v for v in ag.run_gate(["docs/readme.md"], policy, ctx()) if not v.nudge]
    assert len(verdicts) == 1
    assert verdicts[0].status == "pass"
    assert verdicts[0].source == "unannotated"


def test_unannotated_policy_require_blocks_without_override(tmp_path):
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    cfg["unannotated_policy"] = "require"
    write_annotation(tmp_path, "src", [{"component": "c", "editability": "never",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/guarded.py"]}])
    policy = load_base_policy(tmp_path, cfg)
    # unmatched file, no override -> violation under require
    verdicts = [v for v in ag.run_gate(["docs/readme.md"], policy, ctx()) if not v.nudge]
    assert verdicts[0].status == "violation"
    assert verdicts[0].source == "unannotated"


# ===========================================================================
# 10. _prescriptive messaging (DESIGN: prescriptive, not descriptive)
# ===========================================================================


def test_prescriptive_never_violation_message(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "formal-templates", "editability": "never",
                                        "edit_rule": "proofs must stay sound", "description": "-",
                                        "paths": ["src/tmpl.py"]}])
    policy = load_base_policy(tmp_path)
    v = [x for x in ag.run_gate(["src/tmpl.py"], policy, ctx()) if not x.nudge][0]
    msg = ag._prescriptive(v, policy)
    # names the slug + level
    assert "formal-templates" in msg
    assert "never" in msg
    # includes the reason (edit_rule)
    assert "proofs must stay sound" in msg
    # mentions reverting
    assert "revert" in msg.lower() or "git checkout" in msg
    # mentions the concrete override actions
    assert "Arch-Override" in msg  # justification action
    assert "code owner" in msg.lower()  # code_owner action
    # discourages thrashing
    assert "keep editing" in msg.lower()


def test_prescriptive_frozen_all_mode_uses_AND(tmp_path):
    # frozen with override_mode "all" at the config level should join with AND.
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    cfg["override_mode"] = "all"  # _prescriptive checks config["override_mode"] == "all"
    write_annotation(tmp_path, "src", [{"component": "cal", "editability": "frozen",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/cal.py"]}])
    policy = load_base_policy(tmp_path, cfg)
    v = [x for x in ag.run_gate(["src/cal.py"], policy, ctx()) if not x.nudge][0]
    msg = ag._prescriptive(v, policy)
    assert " AND " in msg


def test_prescriptive_guard_deletion_says_restore_markers(tmp_path):
    root_policy = load_base_policy(tmp_path)
    v = ag.Verdict(
        path="strat/golden.py", slug="signal-clause", level="never",
        status="violation", needed=["justification"], got=[],
        reason="no lookahead", source="guard-deletion")
    msg = ag._prescriptive(v, root_policy)
    assert "signal-clause" in msg
    assert "arch:begin" in msg and "arch:end" in msg
    assert "restore" in msg.lower()
    # includes the reason
    assert "no lookahead" in msg


def test_prescriptive_pass_returns_empty(tmp_path):
    policy = load_base_policy(tmp_path)
    v = ag.Verdict(path="x", slug="s", level="editable", status="pass")
    assert ag._prescriptive(v, policy) == ""


def test_prescriptive_conditional_says_requires_justification(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "cond", "editability": "conditional",
                                        "edit_rule": "only under X", "description": "-",
                                        "paths": ["src/c.py"]}])
    policy = load_base_policy(tmp_path)
    v = [x for x in ag.run_gate(["src/c.py"], policy, ctx()) if not x.nudge][0]
    msg = ag._prescriptive(v, policy)
    # conditional is not "must not be edited"; it "requires justification to edit"
    assert "requires justification to edit" in msg


# ===========================================================================
# 11. _fmt_pr_comment (DESIGN: PR-comment delivery)
# ===========================================================================


def test_fmt_pr_comment_blocked_contains_marker_and_violations(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "nevz", "editability": "never",
                                        "edit_rule": "keep sound", "description": "-",
                                        "paths": ["src/n.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["src/n.py"], policy, ctx())
    out = ag._fmt_pr_comment(verdicts, policy)
    assert "<!-- arch-gate -->" in out
    assert "Blocked" in out
    assert "nevz" in out
    assert "src/n.py" in out


def test_fmt_pr_comment_passed_when_no_violations(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "ed", "editability": "editable",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/e.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["src/e.py"], policy, ctx())
    out = ag._fmt_pr_comment(verdicts, policy)
    assert "<!-- arch-gate -->" in out
    assert "Passed" in out
    assert "Blocked" not in out


def test_fmt_pr_comment_shows_override_details(tmp_path):
    write_annotation(tmp_path, "src", [{"component": "nevz", "editability": "never",
                                        "edit_rule": "-", "description": "-",
                                        "paths": ["src/n.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = ag.run_gate(["src/n.py"], policy, ctx(approved_owners=["@alice"]))
    out = ag._fmt_pr_comment(verdicts, policy)
    # overridden change surfaces in the "Allowed with override" section
    assert "override" in out.lower()
    assert "code_owner" in out


# ===========================================================================
# 12. Validation errors -> SystemExit (SPEC §6)
# ===========================================================================


def test_load_policy_duplicate_slug_raises(tmp_path):
    # two annotation files, same slug -> duplicate
    write_annotation(tmp_path, "a", [{"component": "dup", "editability": "never",
                                      "edit_rule": "-", "description": "-", "paths": ["a/x.py"]}])
    write_annotation(tmp_path, "b", [{"component": "dup", "editability": "editable",
                                      "edit_rule": "-", "description": "-", "paths": ["b/y.py"]}])
    policy_path = write_policy(tmp_path, BASE_POLICY_CFG)
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, policy_path)
    assert "duplicate" in str(ei.value).lower()


def test_load_policy_invalid_editability_level_raises(tmp_path):
    write_annotation(tmp_path, "a", [{"component": "c", "editability": "sacred",
                                      "edit_rule": "-", "description": "-", "paths": ["a/x.py"]}])
    policy_path = write_policy(tmp_path, BASE_POLICY_CFG)
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, policy_path)
    assert "editability" in str(ei.value).lower()


def test_load_policy_missing_level_config_raises(tmp_path):
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    del cfg["levels"]["frozen"]  # a required level has no config entry
    policy_path = write_policy(tmp_path, cfg)
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, policy_path)
    assert "frozen" in str(ei.value).lower() and "configured" in str(ei.value).lower()


def test_load_policy_undefined_override_signal_raises(tmp_path):
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    cfg["levels"]["never"]["override"] = ["justification", "ghost_signal"]
    policy_path = write_policy(tmp_path, cfg)
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, policy_path)
    assert "ghost_signal" in str(ei.value)


def test_load_policy_bad_on_change_raises(tmp_path):
    cfg = json.loads(json.dumps(BASE_POLICY_CFG))
    cfg["levels"]["never"]["on_change"] = "nuke"  # not pass|require|block
    policy_path = write_policy(tmp_path, cfg)
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, policy_path)
    assert "on_change" in str(ei.value)


def test_load_policy_missing_file_raises(tmp_path):
    with pytest.raises(SystemExit) as ei:
        ag.load_policy(tmp_path, tmp_path / "does_not_exist.json")
    assert "not found" in str(ei.value).lower()


# ===========================================================================
# 13. Determinism (SPEC §4.2: pure & deterministic)
# ===========================================================================


def test_determinism_identical_verdicts_across_runs(tmp_path):
    write_annotation(tmp_path, "src",
                     [{"component": "nevz", "editability": "never", "edit_rule": "-",
                       "description": "-", "paths": ["src/n.py"]},
                      {"component": "condz", "editability": "conditional", "edit_rule": "-",
                       "description": "-", "paths": ["src/c.py"]}])
    policy = load_base_policy(tmp_path)
    changed = ["src/n.py", "src/c.py", "docs/x.md"]
    c = ctx(pr_body=GOOD_PR_BODY)
    v1 = ag.run_gate(changed, policy, c)
    v2 = ag.run_gate(changed, policy, c)
    assert [x.__dict__ for x in v1] == [x.__dict__ for x in v2]


@requires_git
def test_determinism_git_marker_runs_stable(tmp_path):
    root = git_repo(tmp_path)
    policy = load_base_policy(tmp_path)
    src = "x\n# arch:begin sig never\ng = 1\n# arch:end sig\n"
    (root / "f.py").write_text(src)
    base = git_commit(root, "base")
    (root / "f.py").write_text(src.replace("g = 1", "g = 2"))
    head = git_commit(root, "edit")
    v1 = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    v2 = ag.run_gate(["f.py"], policy, ctx(), base=base, head=head, repo_root=root)
    assert [x.__dict__ for x in v1] == [x.__dict__ for x in v2]


# ===========================================================================
# Extra: reports every touched component (SPEC §4.2 "report every")
# ===========================================================================


def test_reports_every_touched_component(tmp_path):
    write_annotation(tmp_path, "src",
                     [{"component": "nevz", "editability": "never", "edit_rule": "-",
                       "description": "-", "paths": ["src/n.py"]},
                      {"component": "edz", "editability": "editable", "edit_rule": "-",
                       "description": "-", "paths": ["src/e.py"]}])
    policy = load_base_policy(tmp_path)
    verdicts = [v for v in ag.run_gate(["src/n.py", "src/e.py"], policy, ctx()) if not v.nudge]
    paths = {v.path for v in verdicts}
    assert paths == {"src/n.py", "src/e.py"}
    by = {v.path: v for v in verdicts}
    assert by["src/n.py"].status == "violation"
    assert by["src/e.py"].status == "pass"
