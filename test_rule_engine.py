"""
Pytest suite for rule_engine.py (deterministic auto-labeling) and drift.py
(surfacing unratified + uncovered components).

The behavior contract under test is DESIGN.md, section
"Auto-labeling new components (the rule library)":

    Precedence for a new component (FIRST MATCH WINS):
      1. explicit rule    (redline.rules.json match: path_glob/name_regex/type)
      2. parent           (a subcomponent inherits its parent's level)
      3. sibling-majority (a new sibling inherits the majority level of peers)
      4. default + flag    (none of the above -> default, tagged ratified:false)

    The ⚠ unratified semantics: everything auto-labeled carries
    `ratified: false` and is surfaced by `redline drift` until a human confirms.
    Auto-labeling NEVER self-ratifies (a DESIGN non-negotiable).

Both modules are single stdlib-only files (Python 3.11), NOT a package. We load
them by absolute path (relative to THIS test file) so the suite runs from any
cwd, exactly as test_arch_gate.py does. rule_engine.py does `import arch_gate`
and drift.py does `import arch_gate` + `import rule_engine`, so the redline dir
must be on sys.path and each module must be registered in sys.modules BEFORE
exec_module (also required so dataclass `field(default_factory=...)` and
__module__ resolve cleanly).

The modules under test are treated as read-only; where a real bug is found it is
reported and marked xfail rather than worked around.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the single-file modules by absolute path (robust to any cwd), and put
# the redline dir on sys.path so rule_engine's `import arch_gate` (and drift's
# `import arch_gate` / `import rule_engine`) resolve.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load(mod_name: str, filename: str):
    """importlib-load <filename> as <mod_name>, registering in sys.modules
    BEFORE exec so intra-module imports + dataclass default_factory resolve."""
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# arch_gate must exist before rule_engine executes its top-level `import arch_gate`.
ag = _load("arch_gate", "arch_gate.py")
re_ = _load("rule_engine", "rule_engine.py")
drift = _load("drift", "drift.py")

Rule = re_.Rule
RuleLibrary = re_.RuleLibrary
Classification = re_.Classification
LEVEL_RANK = re_.LEVEL_RANK


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def write_rules(tmp_path: Path, cfg: dict, name: str = "redline.rules.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(cfg))
    return p


def rule(match: dict, level: str, reason: str = "") -> "re_.Rule":
    return Rule(match=match, level=level, reason=reason)


def lib(rules=None, *, inherit_subcomponent=True, inherit_sibling_majority=True,
        default="editable") -> "re_.RuleLibrary":
    return RuleLibrary(
        rules=list(rules or []),
        inherit_subcomponent=inherit_subcomponent,
        inherit_sibling_majority=inherit_sibling_majority,
        default=default,
    )


# A policy config whose annotation_glob matches redline.meta.json anywhere, and
# whose levels are all configured (arch_gate.load_policy validates this). Mirrors
# examples/arch.policy.json.
BASE_POLICY_CFG = {
    "spec_version": "0.1",
    "annotation_glob": "**/redline.meta.json",
    "protected_branches": ["main"],
    "unannotated_policy": "pass",
    "override_mode": "any",
    "levels": {
        "editable": {"on_change": "pass"},
        "conditional": {"on_change": "require", "override": ["justification", "code_owner"]},
        "never": {"on_change": "block", "override": ["justification", "code_owner"]},
        "frozen": {"on_change": "block",
                   "override": ["justification", "code_owner", "strong_label"],
                   "override_mode": "all"},
    },
    "overrides": {
        "justification": {"type": "pr_body_block", "heading": "Arch-Override"},
        "code_owner": {"type": "codeowners_review"},
        "strong_label": {"type": "label", "name": "arch-frozen-approved"},
    },
}


def write_policy(repo: Path, cfg: dict | None = None, name: str = "arch.policy.json") -> Path:
    cfg = json.loads(json.dumps(BASE_POLICY_CFG)) if cfg is None else cfg
    p = repo / name
    p.write_text(json.dumps(cfg))
    return p


def write_meta(repo: Path, rel_dir: str, components, name: str = "redline.meta.json") -> Path:
    """Write a redline.meta.json annotation file under rel_dir."""
    d = repo / rel_dir if rel_dir else repo
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    payload = {"components": components} if isinstance(components, list) else components
    p.write_text(json.dumps(payload))
    return p


def write_code(repo: Path, rel_path: str, text: str = "x = 1\n") -> Path:
    p = repo / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ===========================================================================
# 0. Smoke — the public surface the tests + drift.py depend on exists.
# ===========================================================================


def test_public_surface_present():
    for name in ("Rule", "RuleLibrary", "Classification", "classify", "make_stub",
                 "LEVELS", "LEVEL_RANK"):
        assert hasattr(re_, name), f"rule_engine is missing {name!r}"
    # LEVEL_RANK strictly orders leniency -> strictness (used for tie-breaks).
    assert LEVEL_RANK == {"editable": 0, "conditional": 1, "never": 2, "frozen": 3}


# ===========================================================================
# 1. Rule.matches (DESIGN: each rule is a match on path_glob / name_regex / type)
# ===========================================================================


def test_rule_matches_path_glob_hit_and_miss():
    r = rule({"path_glob": "strat/signal/**"}, "never")
    assert r.matches(path="strat/signal/ma.py", name="ma.py") is True
    assert r.matches(path="ui/button.py", name="button.py") is False


def test_rule_matches_name_regex_hit_and_miss():
    r = rule({"name_regex": ".*(_ma|indicator|feature).*"}, "conditional")
    assert r.matches(path="anywhere/fast_ma.py", name="fast_ma.py") is True
    assert r.matches(path="anywhere/router.py", name="router.py") is False


def test_rule_matches_type_hit_and_miss():
    r = rule({"type": "service"}, "conditional")
    assert r.matches(path="p", name="n", ctype="service") is True
    assert r.matches(path="p", name="n", ctype="model") is False
    # missing ctype (default "") also fails a type rule
    assert r.matches(path="p", name="n") is False


def test_rule_matches_all_keys_must_agree():
    # When several keys are present they are ANDed: every present key must match.
    r = rule({"path_glob": "strat/**", "name_regex": "_ma$", "type": "indicator"}, "never")
    assert r.matches(path="strat/fast_ma", name="fast_ma", ctype="indicator") is True
    # path ok, regex ok, but type wrong -> no match
    assert r.matches(path="strat/fast_ma", name="fast_ma", ctype="other") is False
    # path ok, type ok, but regex misses -> no match
    assert r.matches(path="strat/slow", name="slow", ctype="indicator") is False


def test_rule_with_empty_match_dict_matches_nothing():
    # A rule with no match keys must NOT be an accidental catch-all.
    r = rule({}, "never")
    assert r.matches(path="anything.py", name="anything.py") is False
    assert r.matches(path="", name="", ctype="") is False
    # even a match dict with only unrecognized keys is not a catch-all
    r2 = rule({"bogus": "value"}, "never")
    assert r2.matches(path="anything.py", name="anything.py") is False


# ===========================================================================
# 2. RuleLibrary.load — parse rules/inherit/default; validation -> SystemExit
# ===========================================================================


def test_load_parses_rules_inherit_default(tmp_path):
    cfg = {
        "rules": [
            {"match": {"path_glob": "strat/signal/**"}, "level": "never", "reason": "causal"},
            {"match": {"name_regex": "_ma$"}, "level": "conditional"},
        ],
        "inherit": {"subcomponent": False, "sibling_majority": True},
        "default": "conditional",
    }
    lb = RuleLibrary.load(write_rules(tmp_path, cfg))
    assert len(lb.rules) == 2
    assert lb.rules[0].level == "never" and lb.rules[0].reason == "causal"
    assert lb.rules[0].match == {"path_glob": "strat/signal/**"}
    assert lb.rules[1].level == "conditional" and lb.rules[1].reason == ""
    assert lb.inherit_subcomponent is False
    assert lb.inherit_sibling_majority is True
    assert lb.default == "conditional"


def test_load_missing_file_is_default_green_empty_rules(tmp_path):
    # No rule library -> default-green (editable), empty rules, inheritance ON.
    lb = RuleLibrary.load(tmp_path / "does_not_exist.rules.json")
    assert lb.rules == []
    assert lb.default == "editable"
    assert lb.inherit_subcomponent is True
    assert lb.inherit_sibling_majority is True


def test_load_invalid_rule_level_raises(tmp_path):
    cfg = {"rules": [{"match": {"path_glob": "a/**"}, "level": "sacred"}]}
    with pytest.raises(SystemExit) as ei:
        RuleLibrary.load(write_rules(tmp_path, cfg))
    assert "sacred" in str(ei.value)


def test_load_invalid_default_raises(tmp_path):
    cfg = {"rules": [], "default": "immutable"}
    with pytest.raises(SystemExit) as ei:
        RuleLibrary.load(write_rules(tmp_path, cfg))
    assert "immutable" in str(ei.value) or "default" in str(ei.value).lower()


def test_load_rule_missing_match_raises(tmp_path):
    cfg = {"rules": [{"level": "never", "reason": "no match key"}]}
    with pytest.raises(SystemExit) as ei:
        RuleLibrary.load(write_rules(tmp_path, cfg))
    assert "match" in str(ei.value).lower()


def test_load_rule_match_not_an_object_raises(tmp_path):
    # 'match' present but not a dict -> still rejected.
    cfg = {"rules": [{"match": "strat/**", "level": "never"}]}
    with pytest.raises(SystemExit) as ei:
        RuleLibrary.load(write_rules(tmp_path, cfg))
    assert "match" in str(ei.value).lower()


def test_load_invalid_json_raises(tmp_path):
    p = tmp_path / "redline.rules.json"
    p.write_text("{ this is not json ]")
    with pytest.raises(SystemExit) as ei:
        RuleLibrary.load(p)
    assert "json" in str(ei.value).lower()


# ===========================================================================
# 3. classify — the core precedence contract (FIRST MATCH WINS)
#    rule -> parent -> sibling-majority -> default
# ===========================================================================


def test_classify_explicit_rule_wins_over_parent_and_siblings():
    # (a) An explicit rule beats BOTH a parent_level and sibling_levels.
    lb = lib([rule({"path_glob": "strat/signal/**"}, "never", "causal-by-contract")])
    cls = re_.classify(
        "strat/signal/ma.py", lb,
        parent_level="editable",
        sibling_levels=["editable", "editable", "editable"],
    )
    assert cls.level == "never"
    assert cls.source == "rule"
    assert cls.reason == "causal-by-contract"
    assert cls.ratified is False


def test_classify_rule_default_reason_when_rule_has_none():
    lb = lib([rule({"path_glob": "x/**"}, "conditional")])  # no reason
    cls = re_.classify("x/thing.py", lb)
    assert cls.source == "rule" and cls.level == "conditional"
    assert cls.reason  # non-empty fallback reason is supplied


def test_classify_parent_inheritance_when_no_rule():
    # (b) No matching rule, but a parent_level -> inherit parent (source=parent).
    lb = lib([rule({"path_glob": "strat/signal/**"}, "never")])  # does not match
    cls = re_.classify("engine/util/helper.py", lb, parent_level="conditional")
    assert cls.level == "conditional"
    assert cls.source == "parent"
    assert cls.ratified is False
    assert "parent" in cls.reason.lower()


def test_classify_sibling_majority_when_no_rule_no_parent():
    # (c) No rule, no parent, sibling_levels -> majority (source=sibling-majority).
    cls = re_.classify(
        "pkg/newmod.py", lib(),
        sibling_levels=["conditional", "conditional", "editable"],
    )
    assert cls.level == "conditional"  # 2 vs 1 majority
    assert cls.source == "sibling-majority"
    assert cls.ratified is False


def test_classify_sibling_majority_tie_breaks_to_stricter_level():
    # (d) A TIE breaks toward the STRICTER level. ["never","editable"] -> never,
    #     because LEVEL_RANK["never"]=2 > LEVEL_RANK["editable"]=0. Verify hard.
    cls = re_.classify("pkg/x.py", lib(), sibling_levels=["never", "editable"])
    assert cls.level == "never"
    assert cls.source == "sibling-majority"
    # order of the tied inputs must not matter (deterministic strict-wins)
    cls_rev = re_.classify("pkg/x.py", lib(), sibling_levels=["editable", "never"])
    assert cls_rev.level == "never"
    # a three-way tie also resolves to the strictest present level
    cls3 = re_.classify("pkg/y.py", lib(),
                        sibling_levels=["editable", "conditional", "frozen"])
    assert cls3.level == "frozen"


def test_classify_default_when_nothing_matches():
    # (e) Nothing matches -> default level, source=default.
    lb = lib([rule({"path_glob": "strat/signal/**"}, "never")], default="editable")
    cls = re_.classify("docs/readme_gen.py", lb)  # no rule/parent/sibling
    assert cls.level == "editable"
    assert cls.source == "default"
    assert cls.ratified is False
    # a non-green default is honored too
    lb2 = lib(default="conditional")
    assert re_.classify("anything.py", lb2).level == "conditional"


def test_classify_every_branch_is_unratified():
    # (f) A DESIGN non-negotiable: auto-labeling NEVER self-ratifies. Assert
    #     ratified is False on EACH of the four precedence branches.
    lb = lib([rule({"path_glob": "r/**"}, "never")], default="editable")
    by_rule = re_.classify("r/a.py", lb)
    by_parent = re_.classify("z/a.py", lb, parent_level="conditional")
    by_sibling = re_.classify("z/a.py", lb, sibling_levels=["never", "never"])
    by_default = re_.classify("z/a.py", lb)
    assert by_rule.source == "rule" and by_rule.ratified is False
    assert by_parent.source == "parent" and by_parent.ratified is False
    assert by_sibling.source == "sibling-majority" and by_sibling.ratified is False
    assert by_default.source == "default" and by_default.ratified is False


def test_classify_inherit_subcomponent_false_disables_parent():
    # (g) inherit.subcomponent=False -> parent inheritance is skipped; falls
    #     through to sibling-majority (then default).
    lb = lib(inherit_subcomponent=False)
    # parent given but disabled, siblings present -> sibling-majority wins
    cls = re_.classify("z/a.py", lb, parent_level="never",
                       sibling_levels=["conditional", "conditional"])
    assert cls.source == "sibling-majority" and cls.level == "conditional"
    # parent given but disabled, no siblings -> default (NOT parent)
    cls2 = re_.classify("z/a.py", lb, parent_level="never")
    assert cls2.source == "default" and cls2.level == "editable"


def test_classify_inherit_sibling_majority_false_disables_sibling():
    # (g) inherit.sibling_majority=False -> sibling inheritance is skipped.
    lb = lib(inherit_sibling_majority=False)
    cls = re_.classify("z/a.py", lb, sibling_levels=["never", "never"])
    assert cls.source == "default" and cls.level == "editable"
    # parent still works when only sibling inheritance is disabled
    cls2 = re_.classify("z/a.py", lb, parent_level="never",
                        sibling_levels=["editable"])
    assert cls2.source == "parent" and cls2.level == "never"


def test_classify_ignores_bogus_sibling_levels():
    # Unknown sibling level strings are filtered out; only valid LEVELS count.
    cls = re_.classify("z/a.py", lib(),
                       sibling_levels=["garbage", "conditional", "nonsense"])
    assert cls.source == "sibling-majority" and cls.level == "conditional"
    # all-bogus siblings -> nothing to inherit -> default
    cls2 = re_.classify("z/a.py", lib(), sibling_levels=["garbage", "nonsense"])
    assert cls2.source == "default"


def test_classify_parent_level_not_a_real_level_is_ignored():
    # A parent_level outside LEVELS must not be inherited (guards bad input).
    cls = re_.classify("z/a.py", lib(), parent_level="sacred",
                       sibling_levels=["never", "never"])
    assert cls.source == "sibling-majority" and cls.level == "never"


# ===========================================================================
# 4. make_stub — the redline.meta.json component object for an auto-label
# ===========================================================================


def test_make_stub_shape_and_editability_matches_classification():
    cls = re_.classify("strat/signal/ma.py",
                       lib([rule({"path_glob": "strat/signal/**"}, "never", "no lookahead")]))
    stub = re_.make_stub("signal-clause", "strat/signal/**", cls)
    assert stub["component"] == "signal-clause"
    # editability equals the classification level (the whole point).
    assert stub["editability"] == cls.level == "never"
    assert stub["paths"] == ["strat/signal/**"]
    # auto-labeled stubs are ALWAYS unratified.
    assert stub["ratified"] is False
    # auto_reason carries source + reason for drift to display.
    assert stub["auto_reason"] == f"{cls.source}: {cls.reason}"
    assert "rule" in stub["auto_reason"]
    # display_name defaults from the slug when not provided.
    assert stub["display_name"] == "Signal Clause"
    # an explicit display_name is honored.
    stub2 = re_.make_stub("signal-clause", "strat/signal/**", cls, display_name="Signal!")
    assert stub2["display_name"] == "Signal!"


def test_make_stub_default_branch_is_editable_and_unratified():
    cls = re_.classify("z/a.py", lib(default="editable"))
    stub = re_.make_stub("z-component", "z/**", cls)
    assert stub["editability"] == "editable"
    assert stub["ratified"] is False
    assert stub["auto_reason"].startswith("default:")


# ===========================================================================
# 5. drift._read_ratified — finds ratified:false; ignores true / absent
# ===========================================================================


def _drift_repo(tmp_path: Path) -> Path:
    """Build a small fake repo: policy + a couple of meta files + code dirs."""
    repo = tmp_path
    write_policy(repo)
    return repo


def test_read_ratified_finds_only_false(tmp_path):
    repo = _drift_repo(tmp_path)
    write_meta(repo, "a", [
        {"component": "auto-never", "editability": "never", "paths": ["a/**"],
         "ratified": False, "auto_reason": "rule: causal"},
        {"component": "human-ok", "editability": "never", "paths": ["a/ok/**"],
         "ratified": True},
    ])
    # ratified absent -> defaults to true (ratified), so must NOT be listed.
    write_meta(repo, "b", [
        {"component": "no-flag", "editability": "editable", "paths": ["b/**"]},
    ])
    got = drift._read_ratified(repo, "**/redline.meta.json")
    slugs = {c["component"] for c in got}
    assert slugs == {"auto-never"}
    # the record carries its meta path (repo-relative) for the report.
    rec = got[0]
    assert rec["_meta_path"] == "a/redline.meta.json"
    assert rec["auto_reason"] == "rule: causal"


def test_read_ratified_handles_single_object_and_bad_json(tmp_path):
    repo = _drift_repo(tmp_path)
    # a bare object (not wrapped in {"components": [...]}) with ratified:false
    write_meta(repo, "solo", {"component": "solo-auto", "editability": "conditional",
                              "paths": ["solo/**"], "ratified": False})
    # a malformed meta file is skipped, not fatal.
    bad_dir = repo / "bad"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "redline.meta.json").write_text("{ not valid json ]")
    got = drift._read_ratified(repo, "**/redline.meta.json")
    assert {c["component"] for c in got} == {"solo-auto"}


# ===========================================================================
# 6. run_drift.unratified_components — exactly the ratified:false components
# ===========================================================================


def test_run_drift_unratified_components(tmp_path):
    repo = _drift_repo(tmp_path)
    write_meta(repo, "core", [
        {"component": "engine", "editability": "never", "paths": ["core/**"],
         "ratified": True},
        {"component": "new-auto", "editability": "conditional", "paths": ["core/new/**"],
         "ratified": False, "auto_reason": "sibling-majority: conditional"},
    ])
    # code so the dirs exist (covered by the components' paths).
    write_code(repo, "core/engine.py")
    write_code(repo, "core/new/mod.py")
    result = drift.run_drift(repo, repo / "arch.policy.json",
                             repo / "redline.rules.json", scan_roots=["core"])
    unr = result["unratified_components"]
    assert [c["component"] for c in unr] == ["new-auto"]


# ===========================================================================
# 7. run_drift.uncovered_paths — uncovered dir -> proposal w/ suggested level;
#    covered dir -> not proposed.
# ===========================================================================


def test_run_drift_uncovered_vs_covered(tmp_path):
    repo = _drift_repo(tmp_path)
    # A rule library so the proposal for an uncovered dir gets a real level.
    write_rules(repo, {
        "rules": [{"match": {"path_glob": "wild/**"}, "level": "never",
                   "reason": "wild code is thesis-critical"}],
        "inherit": {"subcomponent": True, "sibling_majority": True},
        "default": "editable",
    })
    # 'covered' is owned by a component (paths glob matches its files).
    write_meta(repo, "covered", [
        {"component": "owned", "editability": "editable", "paths": ["covered/**"]},
    ])
    write_code(repo, "covered/owned_mod.py")
    # 'wild' has code but no component owns it -> should surface as uncovered.
    write_code(repo, "wild/loose.py")

    result = drift.run_drift(repo, repo / "arch.policy.json",
                             repo / "redline.rules.json",
                             scan_roots=["covered", "wild"])
    proposals = {p["path"]: p for p in result["uncovered_paths"]}
    # covered dir does NOT appear.
    assert "covered" not in proposals
    # wild dir appears, with the rule-engine-suggested level from the matching rule.
    assert "wild" in proposals
    wild = proposals["wild"]
    assert wild["level"] == "never"
    assert wild["source"] == "rule"
    # it ships a ready, unratified stub for the human to adopt.
    stub = wild["suggested_stub"]
    assert stub["editability"] == "never"
    assert stub["ratified"] is False
    assert stub["paths"] == ["wild/**"]


def test_run_drift_uncovered_default_level_when_no_rule(tmp_path):
    repo = _drift_repo(tmp_path)
    # No rules file at all -> default-green library; uncovered dir suggests default.
    write_code(repo, "loose/a.py")
    result = drift.run_drift(repo, repo / "arch.policy.json",
                             repo / "redline.rules.json",  # does not exist
                             scan_roots=["loose"])
    proposals = {p["path"]: p for p in result["uncovered_paths"]}
    assert "loose" in proposals
    assert proposals["loose"]["level"] == "editable"
    assert proposals["loose"]["source"] == "default"


def test_run_drift_all_covered_yields_no_proposals(tmp_path):
    repo = _drift_repo(tmp_path)
    write_meta(repo, "pkg", [
        {"component": "pkgc", "editability": "editable", "paths": ["pkg/**"]},
    ])
    write_code(repo, "pkg/a.py")
    write_code(repo, "pkg/sub/b.py")
    result = drift.run_drift(repo, repo / "arch.policy.json",
                             repo / "redline.rules.json", scan_roots=["pkg"])
    assert result["uncovered_paths"] == []


# ===========================================================================
# 8. --fail-on-unratified semantics via main() argv (SystemExit / return code)
# ===========================================================================


def _run_drift_main(repo: Path, extra_argv) -> int:
    """Invoke drift.main with argv; normalize SystemExit -> int return code."""
    argv = [
        "--repo-root", str(repo),
        "--policy", str(repo / "arch.policy.json"),
        "--rules", str(repo / "redline.rules.json"),
        *extra_argv,
    ]
    try:
        rc = drift.main(argv)
    except SystemExit as e:  # in case main() ever raises instead of returns
        rc = e.code if isinstance(e.code, int) else 1
    return rc


def test_main_fail_on_unratified_returns_1_when_unratified_exist(tmp_path, capsys):
    repo = _drift_repo(tmp_path)
    write_meta(repo, "core", [
        {"component": "auto-thing", "editability": "never", "paths": ["core/**"],
         "ratified": False, "auto_reason": "rule: x"},
    ])
    write_code(repo, "core/x.py")
    rc = _run_drift_main(repo, ["--scan", "core", "--fail-on-unratified"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "auto-thing" in out
    assert "UNRATIFIED" in out


def test_main_fail_on_unratified_returns_0_when_all_ratified(tmp_path, capsys):
    repo = _drift_repo(tmp_path)
    write_meta(repo, "core", [
        {"component": "confirmed", "editability": "never", "paths": ["core/**"],
         "ratified": True},
    ])
    write_code(repo, "core/x.py")
    rc = _run_drift_main(repo, ["--scan", "core", "--fail-on-unratified"])
    assert rc == 0


def test_main_without_flag_returns_0_even_with_unratified(tmp_path, capsys):
    # Without --fail-on-unratified, unratified components are reported but the
    # command still succeeds (exit 0) — it's a surfacing tool by default.
    repo = _drift_repo(tmp_path)
    write_meta(repo, "core", [
        {"component": "auto-thing", "editability": "never", "paths": ["core/**"],
         "ratified": False},
    ])
    write_code(repo, "core/x.py")
    rc = _run_drift_main(repo, ["--scan", "core"])
    assert rc == 0


# ===========================================================================
# 9. Determinism — run_drift twice yields an identical result.
# ===========================================================================


def test_run_drift_is_deterministic(tmp_path):
    repo = _drift_repo(tmp_path)
    write_rules(repo, {
        "rules": [{"match": {"path_glob": "wild/**"}, "level": "conditional"}],
        "default": "editable",
    })
    write_meta(repo, "core", [
        {"component": "engine", "editability": "never", "paths": ["core/**"],
         "ratified": True},
        {"component": "auto", "editability": "conditional", "paths": ["core/auto/**"],
         "ratified": False},
    ])
    write_code(repo, "core/engine.py")
    write_code(repo, "core/auto/a.py")
    write_code(repo, "wild/w.py")
    write_code(repo, "loose/l.py")
    r1 = drift.run_drift(repo, repo / "arch.policy.json",
                         repo / "redline.rules.json", scan_roots=["core", "wild", "loose"])
    r2 = drift.run_drift(repo, repo / "arch.policy.json",
                         repo / "redline.rules.json", scan_roots=["core", "wild", "loose"])
    assert r1 == r2
    # sanity: it actually found both an unratified comp and >=1 uncovered dir,
    # so determinism is over a non-trivial result.
    assert [c["component"] for c in r1["unratified_components"]] == ["auto"]
    assert {p["path"] for p in r1["uncovered_paths"]} >= {"wild", "loose"}
