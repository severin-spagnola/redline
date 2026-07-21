"""
Pytest suite for overfit_lint.py — the deterministic static instance-fitting
lint (layer 2 of the anti-overfit stack; see ANTI_OVERFIT.md).

Behavior contract under test: an AST scan of PROCESS code that flags the laziest
deterministically-detectable forms of instance-fitting —
  * hardcoded identifier/name literals in ==/!=/.startswith/.endswith/membership,
  * "magic" numeric constants in ==/!= comparisons (allowlist of structural ints),
  * membership tests against inline literal sets/lists of names.
It is ADVISORY (flag, never block) and deterministic.

The module under test is a single stdlib-only file, NOT a package. We load it by
absolute path (relative to THIS test file) so the suite runs from any cwd, and we
register it in sys.modules before exec_module so its dataclasses resolve cleanly.
overfit_lint imports arch_gate at import time, so the redline directory must be on
sys.path (overfit_lint inserts its own parent, but we also load arch_gate here so
the process_files scoping tests share one module instance).

The lint is treated as read-only; where a real bug is found it is reported and
marked xfail rather than worked around. Where the task says "confirm the actual
behavior" (>= flagging, mixed name-sets), the assertions below match what the
implementation ACTUALLY does, verified against the source.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the modules by absolute path (robust to any cwd).
#
# overfit_lint.py does `sys.path.insert(0, <its dir>); import arch_gate`. To make
# that import resolve to the SAME module object the scoping tests use, we put the
# redline dir on sys.path and load arch_gate first, registering it in sys.modules.
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load(mod_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # register BEFORE exec so dataclasses / __module__ / cross-imports resolve
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# arch_gate must import cleanly first (overfit_lint depends on it).
ag = _load("arch_gate", "arch_gate.py")
ol = _load("overfit_lint", "overfit_lint.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(allowed_ints=None, allowed_strings=None) -> "ol.LintConfig":
    """A LintConfig with the module defaults, plus any extra allowlist entries."""
    c = ol.LintConfig()
    if allowed_ints:
        c.allowed_ints |= set(allowed_ints)
    if allowed_strings:
        c.allowed_strings |= set(allowed_strings)
    return c


def _lint_src(tmp_path: Path, src: str, cfg=None, name: str = "proc.py"):
    """Write src to a .py file under tmp_path and lint it, returning findings."""
    p = tmp_path / name
    p.write_text(src)
    return ol.lint_file(p, cfg if cfg is not None else _cfg(), tmp_path)


def _kinds(findings):
    return [f.kind for f in findings]


def _count(findings, kind):
    return sum(1 for f in findings if f.kind == kind)


# ===========================================================================
# 0. Module smoke — public surface exists
# ===========================================================================

def test_public_surface_present():
    for name in (
        "_looks_like_name", "_Visitor", "lint_file", "process_files",
        "LintConfig", "Finding", "DEFAULT_ALLOWED_INTS", "NAME_BRANCH_METHODS",
    ):
        assert hasattr(ol, name), f"overfit_lint is missing public symbol {name!r}"


def test_arch_gate_is_the_shared_instance():
    # overfit_lint must have imported the very module object we loaded (so the
    # scoping tests exercise the same code path the lint uses).
    assert ol.ag is ag


# ===========================================================================
# 1. _looks_like_name (identifier / snake_case / prefix heuristics)
# ===========================================================================

@pytest.mark.parametrize("s", ["axi_", "csr_bank_top", "module_name", "axi_lite"])
def test_looks_like_name_true(s):
    assert ol._looks_like_name(s) is True


def test_looks_like_name_false_empty():
    assert ol._looks_like_name("") is False


def test_looks_like_name_false_too_long():
    # 65 chars — over the len > 64 cutoff (64 is still allowed, 65 is not).
    assert ol._looks_like_name("a" * 65) is False
    assert ol._looks_like_name("a" * 64) is True  # boundary: exactly 64 is fine


def test_looks_like_name_false_has_space():
    assert ol._looks_like_name("hello world") is False


def test_looks_like_name_false_looks_numeric():
    assert ol._looks_like_name("3.14") is False


# ===========================================================================
# 2. visit_Call — .startswith / .endswith on a name literal
# ===========================================================================

def test_startswith_literal_name_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(x):\n    return x.startswith("axi_")\n')
    assert _count(f, "hardcoded-name") == 1
    assert f[0].kind == "hardcoded-name"


def test_startswith_nonliteral_arg_not_flagged(tmp_path):
    # x.startswith(SOME_VAR) — arg is not a string constant -> nothing to flag.
    src = "SOME_VAR = 'axi_'\ndef r(x):\n    return x.startswith(SOME_VAR)\n"
    f = _lint_src(tmp_path, src)
    assert _count(f, "hardcoded-name") == 0


def test_endswith_literal_name_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(x):\n    return x.endswith("_ctrl")\n')
    assert _count(f, "hardcoded-name") == 1


def test_startswith_allowlisted_string_not_flagged(tmp_path):
    # allowed_strings suppresses the finding for that specific name.
    f = _lint_src(tmp_path, 'def r(x):\n    return x.startswith("axi_")\n',
                  cfg=_cfg(allowed_strings={"axi_"}))
    assert _count(f, "hardcoded-name") == 0


def test_other_method_with_name_literal_not_flagged(tmp_path):
    # Only startswith/endswith are NAME_BRANCH_METHODS; .find("axi_") is not one.
    f = _lint_src(tmp_path, 'def r(x):\n    return x.find("axi_")\n')
    assert _count(f, "hardcoded-name") == 0


# ===========================================================================
# 3. visit_Compare — equality/inequality against a name literal
# ===========================================================================

def test_eq_against_name_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(name):\n    return name == "csr_bank_top"\n')
    assert _count(f, "hardcoded-name") == 1


def test_noteq_against_name_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(name):\n    return name != "axi_lite"\n')
    assert _count(f, "hardcoded-name") == 1


def test_eq_against_allowlisted_name_not_flagged(tmp_path):
    # name == "x" where "x" is an allowlisted string -> suppressed. ("x" is an
    # identifier so it WOULD look like a name; the allowlist is what silences it.)
    f = _lint_src(tmp_path, 'def r(name):\n    return name == "x"\n',
                  cfg=_cfg(allowed_strings={"x"}))
    assert _count(f, "hardcoded-name") == 0


def test_eq_literal_on_left_side_also_flagged(tmp_path):
    # The visitor checks both operand and node.left, so "axi_lite" == name flags.
    f = _lint_src(tmp_path, 'def r(name):\n    return "axi_lite" == name\n')
    assert _count(f, "hardcoded-name") == 1


# ===========================================================================
# 4. visit_Compare — magic numeric constants (Eq/NotEq only)
# ===========================================================================

def test_eq_magic_int_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(port_count):\n    return port_count == 37\n')
    assert _count(f, "magic-constant") == 1


@pytest.mark.parametrize("n", [0, 32, 64, 1024])
def test_eq_allowed_int_not_flagged(tmp_path, n):
    # 0, 32, 64, 1024 are all in DEFAULT_ALLOWED_INTS -> no magic-constant finding.
    assert n in ol.DEFAULT_ALLOWED_INTS  # guard: the fixture's premise holds
    f = _lint_src(tmp_path, f'def r(port_count):\n    return port_count == {n}\n')
    assert _count(f, "magic-constant") == 0


def test_ge_magic_int_NOT_flagged_only_eq_noteq(tmp_path):
    # CONFIRMED FROM SOURCE: visit_Compare only enters the magic-constant branch
    # for ast.Eq / ast.NotEq. A `>=` (ast.GtE) comparison is NOT flagged, even
    # against a magic constant. This asserts the ACTUAL contract, not intuition.
    f = _lint_src(tmp_path, 'def r(threshold):\n    return threshold >= 37\n')
    assert _count(f, "magic-constant") == 0
    # ... and to be explicit that == on the same constant WOULD flag:
    f2 = _lint_src(tmp_path, 'def r(threshold):\n    return threshold == 37\n')
    assert _count(f2, "magic-constant") == 1


def test_eq_magic_float_flagged(tmp_path):
    # 3.14 is a float, not in the (int-only) allowlist -> magic-constant.
    f = _lint_src(tmp_path, 'def r(ratio):\n    return ratio == 3.14\n')
    assert _count(f, "magic-constant") == 1


def test_eq_magic_int_allowlisted_via_config_not_flagged(tmp_path):
    # 37 becomes legit once added to allowed_ints.
    f = _lint_src(tmp_path, 'def r(port_count):\n    return port_count == 37\n',
                  cfg=_cfg(allowed_ints={37}))
    assert _count(f, "magic-constant") == 0


def test_eq_bool_not_treated_as_magic_int(tmp_path):
    # _const_num explicitly excludes bool, so `flag == True` is not a magic int.
    f = _lint_src(tmp_path, 'def r(flag):\n    return flag == True\n')
    assert _count(f, "magic-constant") == 0


# ===========================================================================
# 5. _literal_name_set — membership against inline literal collections
# ===========================================================================

def test_membership_two_names_flagged(tmp_path):
    f = _lint_src(tmp_path, 'def r(name):\n    return name in {"axi_lite", "axi_full"}\n')
    assert _count(f, "name-set") == 1


def test_membership_single_name_not_flagged(tmp_path):
    # needs >= 2 name-like elements; one is not enough.
    f = _lint_src(tmp_path, 'def r(name):\n    return name in {"axi_lite"}\n')
    assert _count(f, "name-set") == 0


def test_membership_numbers_not_flagged(tmp_path):
    # numeric elements are not name-like -> no name-set finding.
    f = _lint_src(tmp_path, 'def r(name):\n    return name in [1, 2, 3]\n')
    assert _count(f, "name-set") == 0


def test_membership_nonliteral_collection_not_flagged(tmp_path):
    # `name in some_var` — operand is not a Set/List/Tuple literal.
    src = "some_var = {'axi_lite', 'axi_full'}\ndef r(name):\n    return name in some_var\n"
    f = _lint_src(tmp_path, src)
    assert _count(f, "name-set") == 0


def test_membership_mixed_name_and_nonname_not_flagged(tmp_path):
    # CONFIRMED FROM SOURCE: _literal_name_set requires len(out) == len(elts),
    # i.e. the WHOLE literal must be name-like. A set mixing a name and a number
    # yields out=["axi_lite"], len(out)=1 != len(elts)=2 -> returns [] -> 0.
    f = _lint_src(tmp_path, 'def r(name):\n    return name in {"axi_lite", 7}\n')
    assert _count(f, "name-set") == 0


def test_membership_two_names_all_allowlisted_not_flagged(tmp_path):
    # If every fresh name is on the allowlist there is nothing left to flag.
    f = _lint_src(tmp_path, 'def r(name):\n    return name in {"axi_lite", "axi_full"}\n',
                  cfg=_cfg(allowed_strings={"axi_lite", "axi_full"}))
    assert _count(f, "name-set") == 0


# ===========================================================================
# 6. Clean general-logic process code -> silent
# ===========================================================================

def test_clean_ranking_heuristic_is_silent(tmp_path):
    # A realistic ~10-line ranking heuristic: arithmetic, ratios, a 0.5 threshold,
    # a loop, and no branching on specific identifiers or magic instance constants.
    # Numeric literals here (0.0, 1, 0.5, 2, len) are all allowlisted/structural.
    src = (
        "def rank(candidates):\n"
        "    ranked = []\n"
        "    for c in candidates:\n"
        "        hits = c.get('hits', 0)\n"
        "        tries = c.get('tries', 0)\n"
        "        ratio = hits / tries if tries else 0.0\n"
        "        score = ratio * 2 + (1 if c.get('recent') else 0)\n"
        "        if score >= 0.5:\n"
        "            ranked.append((score, c))\n"
        "    ranked.sort(reverse=True)\n"
        "    return [c for _, c in ranked]\n"
    )
    f = _lint_src(tmp_path, src)
    assert f == [], f"expected no findings, got {_kinds(f)}: {[x.detail for x in f]}"


def test_clean_code_with_only_allowed_ints_is_silent(tmp_path):
    # Comparisons only against allowlisted structural ints -> silent.
    src = (
        "def r(width, count):\n"
        "    if width == 32:\n"
        "        return count == 0 or count == 1024\n"
        "    return count != 64\n"
    )
    f = _lint_src(tmp_path, src)
    assert f == [], f"expected no findings, got {_kinds(f)}"


# ===========================================================================
# 7. lint_file robustness — SyntaxError -> [] (never crash)
# ===========================================================================

def test_lint_file_syntax_error_returns_empty(tmp_path):
    # Unparseable Python must not raise; lint_file catches SyntaxError -> [].
    f = _lint_src(tmp_path, 'def broken(:\n    this is not python @@@ )(\n')
    assert f == []


def test_lint_file_missing_file_returns_empty(tmp_path):
    # OSError path: a nonexistent file also returns [] rather than raising.
    missing = tmp_path / "nope.py"
    assert ol.lint_file(missing, _cfg(), tmp_path) == []


# ===========================================================================
# 8. LintConfig.load — defaults + merge
# ===========================================================================

def test_lintconfig_load_missing_file_is_defaults(tmp_path):
    cfg = ol.LintConfig.load(tmp_path / "no_such_config.json")
    assert cfg.allowed_ints == set(ol.DEFAULT_ALLOWED_INTS)
    assert cfg.allowed_strings == set()


def test_lintconfig_load_none_path_is_defaults():
    cfg = ol.LintConfig.load(None)
    assert cfg.allowed_ints == set(ol.DEFAULT_ALLOWED_INTS)
    assert cfg.allowed_strings == set()


def test_lintconfig_load_merges_into_defaults(tmp_path):
    p = tmp_path / "overfit_lint.json"
    p.write_text(json.dumps({"allowed_ints": [37, 99], "allowed_strings": ["axi_", "csr_bank"]}))
    cfg = ol.LintConfig.load(p)
    # merged, not replaced: defaults are still present...
    assert set(ol.DEFAULT_ALLOWED_INTS).issubset(cfg.allowed_ints)
    # ... plus the extras.
    assert {37, 99}.issubset(cfg.allowed_ints)
    assert cfg.allowed_strings == {"axi_", "csr_bank"}


def test_lintconfig_loaded_allowlist_suppresses_findings(tmp_path):
    # End-to-end: a config file's allowlist actually changes lint_file output.
    p = tmp_path / "overfit_lint.json"
    p.write_text(json.dumps({"allowed_ints": [37], "allowed_strings": ["axi_"]}))
    cfg = ol.LintConfig.load(p)
    src = 'def r(x, port_count):\n    return x.startswith("axi_") and port_count == 37\n'
    f = _lint_src(tmp_path, src, cfg=cfg)
    assert f == [], f"allowlisted config should suppress all, got {_kinds(f)}"


# ===========================================================================
# 9. process_files scoping — only PROCESS (editable/conditional) code is linted
# ===========================================================================

def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_process_files_scopes_to_editable_excludes_never(tmp_path):
    """The key scoping guarantee: with a policy, process_files returns files from
    editable/conditional components and NOT from `never` (template) components.

    Components are declared WITHOUT explicit `paths` so each claims its whole
    directory subtree via dir_scope (arch_gate rule 2). A `never` template dir and
    an `editable` process dir each get their own redline.meta.json annotation.
    """
    # arch.policy.json — annotation_glob matches the redline.meta.json files.
    policy_cfg = {
        "spec_version": "0.1",
        "annotation_glob": "**/redline.meta.json",
        "protected_branches": ["main"],
        "unannotated_policy": "pass",
        "override_mode": "any",
        "levels": {
            "editable": {"on_change": "pass"},
            "conditional": {"on_change": "require", "override": ["justification"]},
            "never": {"on_change": "block", "override": ["justification"]},
            "frozen": {"on_change": "block", "override": ["justification"]},
        },
        "overrides": {
            "justification": {"type": "pr_body_block", "heading": "Arch-Override"},
        },
    }
    _write(tmp_path / "arch.policy.json", json.dumps(policy_cfg))

    # template dir -> never (excluded)
    _write(tmp_path / "templates" / "redline.meta.json",
           json.dumps({"components": [{"component": "tmpl", "editability": "never",
                                       "edit_rule": "-", "description": "-"}]}))
    _write(tmp_path / "templates" / "banks.py",
           'def r(name):\n    return name == "csr_bank_top"\n')

    # process dir -> editable (included)
    _write(tmp_path / "engine" / "redline.meta.json",
           json.dumps({"components": [{"component": "proc", "editability": "editable",
                                       "edit_rule": "-", "description": "-"}]}))
    _write(tmp_path / "engine" / "recognizer.py",
           'def r(x):\n    return x.startswith("axi_")\n')

    files = ol.process_files(tmp_path, tmp_path / "arch.policy.json")
    rels = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in files}

    assert "engine/recognizer.py" in rels, f"process file missing from {rels}"
    assert "templates/banks.py" not in rels, f"template file should be excluded; got {rels}"


def test_process_files_includes_conditional(tmp_path):
    # conditional components are ALSO process code and should be included.
    policy_cfg = {
        "spec_version": "0.1", "annotation_glob": "**/redline.meta.json",
        "protected_branches": ["main"], "unannotated_policy": "pass",
        "override_mode": "any",
        "levels": {
            "editable": {"on_change": "pass"},
            "conditional": {"on_change": "require", "override": ["j"]},
            "never": {"on_change": "block", "override": ["j"]},
            "frozen": {"on_change": "block", "override": ["j"]},
        },
        "overrides": {"j": {"type": "pr_body_block", "heading": "Arch-Override"}},
    }
    _write(tmp_path / "arch.policy.json", json.dumps(policy_cfg))
    _write(tmp_path / "cond" / "redline.meta.json",
           json.dumps({"components": [{"component": "condc", "editability": "conditional",
                                       "edit_rule": "-", "description": "-"}]}))
    _write(tmp_path / "cond" / "binder.py", "def r(x):\n    return x + 1\n")
    _write(tmp_path / "froz" / "redline.meta.json",
           json.dumps({"components": [{"component": "frozc", "editability": "frozen",
                                       "edit_rule": "-", "description": "-"}]}))
    _write(tmp_path / "froz" / "sealed.py", "def r(x):\n    return x\n")

    files = ol.process_files(tmp_path, tmp_path / "arch.policy.json")
    rels = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in files}
    assert "cond/binder.py" in rels        # conditional -> included
    assert "froz/sealed.py" not in rels     # frozen -> excluded


def test_process_files_no_policy_returns_all_py_minus_skips(tmp_path):
    # With no policy, process_files lints ALL .py under repo_root except _keep skips
    # (test/ tests/ dirs, test_ files, __pycache__, etc.).
    _write(tmp_path / "a.py", "x = 1\n")
    _write(tmp_path / "pkg" / "b.py", "y = 2\n")
    _write(tmp_path / "tests" / "c.py", "z = 3\n")             # skipped (tests dir)
    _write(tmp_path / "pkg" / "test_d.py", "w = 4\n")           # skipped (test_ prefix)
    _write(tmp_path / "__pycache__" / "e.py", "v = 5\n")        # skipped (__pycache__)

    files = ol.process_files(tmp_path, None)
    rels = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in files}
    assert rels == {"a.py", "pkg/b.py"}, f"unexpected file set: {rels}"


def test_process_files_nonexistent_policy_treated_as_no_policy(tmp_path):
    # process_files falls back to "all .py" when the policy path is not a file.
    _write(tmp_path / "a.py", "x = 1\n")
    files = ol.process_files(tmp_path, tmp_path / "does_not_exist.json")
    rels = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in files}
    assert rels == {"a.py"}


# ===========================================================================
# 10. Determinism — pure & repeatable
# ===========================================================================

def test_determinism_identical_findings_across_runs(tmp_path):
    src = (
        "def r(name, port_count):\n"
        '    if name.startswith("axi_"):\n'
        "        return port_count == 37\n"
        '    if name in {"csr_lite", "csr_full"}:\n'
        '        return name != "csr_bank_top"\n'
        "    return False\n"
    )
    p = tmp_path / "proc.py"
    p.write_text(src)
    cfg = _cfg()
    v1 = ol.lint_file(p, cfg, tmp_path)
    v2 = ol.lint_file(p, cfg, tmp_path)
    assert [f.__dict__ for f in v1] == [f.__dict__ for f in v2]
    # sanity: this rich fixture actually produced findings of each kind.
    assert _count(v1, "hardcoded-name") >= 2
    assert _count(v1, "magic-constant") == 1
    assert _count(v1, "name-set") == 1
