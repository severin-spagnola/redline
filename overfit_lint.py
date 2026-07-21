#!/usr/bin/env python3
"""
overfit_lint.py — deterministic static lint for INSTANCE-FITTING in process code.

The path redline stops the LLM from editing templates/instances directly. It does
NOT stop the LLM from writing a *process* (a recognizer, binder, heuristic) that
secretly hardcodes instance knowledge:

    if module_name.startswith("axi_") and port_count == 37: ...   # overfit

That is a legal edit to a legal file; the file boundary can't see it, because
overfitting is a semantic property, not a path. This lint catches the *laziest,
deterministically-detectable* forms of that — magic constants and hardcoded
identifiers in process files — for zero tokens, before the expensive sealed-set
eval or the LLM judge run.

It is ADVISORY: it flags for a human. It is deterministic, so its flags are
reliable (no LLM in the loop). It does not block.

Scope: by default it lints files that are PROCESS code — i.e. components whose
redline editability is `editable` or `conditional` (templates/instances are
`never`/`frozen` and already redlined, and are *expected* to contain constants).
Point it at explicit paths to override.

What it flags (each is a heuristic; tune with an allowlist):
  * string literals that look like identifiers used in a comparison / .startswith
    / .endswith / dict-key lookup — i.e. branching on a specific name.
  * "magic" integer/float constants in comparisons (== , >=, in a guard) that are
    not in the allowlist of legitimate constants (0, 1, powers of two, etc.).
  * membership tests against inline literal sets/lists of names
    (name in {"axi_lite", "axi_full"}).

Stdlib only (uses `ast`). Reuses arch_gate to know which files are process code.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arch_gate as ag  # noqa: E402

# Integers that are almost never "instance knowledge" — structural/boilerplate.
DEFAULT_ALLOWED_INTS: Set[int] = {
    -1, 0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 63, 64, 100, 127, 128, 255, 256,
    512, 1000, 1024, 2048, 4096, 8192, 16384, 32768, 65535, 65536,
}

# Attribute/method names whose string arg is a branch-on-a-name (the smell).
NAME_BRANCH_METHODS = {"startswith", "endswith"}


@dataclass
class Finding:
    file: str
    line: int
    kind: str          # "hardcoded-name" | "magic-constant" | "name-set"
    snippet: str
    detail: str


@dataclass
class LintConfig:
    allowed_ints: Set[int] = field(default_factory=lambda: set(DEFAULT_ALLOWED_INTS))
    allowed_strings: Set[str] = field(default_factory=set)   # names known-legit to branch on
    # a string literal counts as an "identifier-like name" if it matches this
    identifier_like = None  # set below

    @staticmethod
    def load(path: Optional[Path]) -> "LintConfig":
        cfg = LintConfig()
        if path and path.is_file():
            raw = json.loads(path.read_text())
            cfg.allowed_ints |= set(raw.get("allowed_ints", []))
            cfg.allowed_strings |= set(raw.get("allowed_strings", []))
        return cfg


import re
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")           # a bare identifier
_SNAKEY_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")     # snake_case (module-ish)
_PREFIXY_RE = re.compile(r"^[a-z][a-z0-9]*_$")                # "axi_", "csr_"


def _looks_like_name(s: str) -> bool:
    """Does this string literal look like a source identifier / module name?"""
    if not s or len(s) > 64:
        return False
    return bool(_IDENT_RE.match(s) or _SNAKEY_RE.match(s) or _PREFIXY_RE.match(s))


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, cfg: LintConfig, src_lines: List[str]):
        self.path = path
        self.cfg = cfg
        self.src = src_lines
        self.findings: List[Finding] = []

    def _snip(self, node: ast.AST) -> str:
        ln = getattr(node, "lineno", None)
        if ln and 1 <= ln <= len(self.src):
            return self.src[ln - 1].strip()[:120]
        return ""

    def _const_str(self, n: ast.AST) -> Optional[str]:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return n.value
        return None

    def _const_num(self, n: ast.AST) -> Optional[Any]:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        return None

    # name.startswith("axi_") / .endswith(...)
    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in NAME_BRANCH_METHODS:
            for a in node.args:
                s = self._const_str(a)
                if s is not None and _looks_like_name(s) and s not in self.cfg.allowed_strings:
                    self.findings.append(Finding(
                        self.path, node.lineno, "hardcoded-name", self._snip(node),
                        f".{node.func.attr}(\"{s}\") — branching on a specific identifier/prefix"))
        self.generic_visit(node)

    # x == "axi_lite"  |  x == 37  |  x != "csr_bank"
    def visit_Compare(self, node: ast.Compare):
        operands = [node.left] + list(node.comparators)
        for op, operand in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.NotEq)):
                s = self._const_str(operand) or self._const_str(node.left)
                if s is not None and _looks_like_name(s) and s not in self.cfg.allowed_strings:
                    self.findings.append(Finding(
                        self.path, node.lineno, "hardcoded-name", self._snip(node),
                        f"equality against \"{s}\" — matching a specific identifier"))
                n = self._const_num(operand)
                if n is None:
                    n = self._const_num(node.left)
                if n is not None and n not in self.cfg.allowed_ints:
                    self.findings.append(Finding(
                        self.path, node.lineno, "magic-constant", self._snip(node),
                        f"comparison against magic constant {n!r}"))
            # membership: x in {"axi_lite", "axi_full"} / [ ... ]
            if isinstance(op, (ast.In, ast.NotIn)):
                names = self._literal_name_set(operand)
                if names:
                    fresh = [x for x in names if x not in self.cfg.allowed_strings]
                    if fresh:
                        self.findings.append(Finding(
                            self.path, node.lineno, "name-set", self._snip(node),
                            f"membership test against literal names {sorted(fresh)[:5]}"))
        self.generic_visit(node)

    def _literal_name_set(self, n: ast.AST) -> List[str]:
        elts = None
        if isinstance(n, (ast.Set, ast.List, ast.Tuple)):
            elts = n.elts
        if not elts:
            return []
        out = []
        for e in elts:
            s = self._const_str(e)
            if s is not None and _looks_like_name(s):
                out.append(s)
        # only flag if the WHOLE literal is name-like (a config of names), 2+
        return out if len(out) >= 2 and len(out) == len(elts) else []


def lint_file(path: Path, cfg: LintConfig, repo_root: Path) -> List[Finding]:
    try:
        src = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    rel = str(path.relative_to(repo_root)).replace(os.sep, "/")
    v = _Visitor(rel, cfg, src.splitlines())
    v.visit(tree)
    return v.findings


def process_files(repo_root: Path, policy_path: Optional[Path]) -> List[Path]:
    """Files that are PROCESS code: components with editability editable/conditional.
    Templates/instances are never/frozen and excluded (they legitimately hold
    constants). If no policy, lint all .py under repo_root."""
    if not policy_path or not policy_path.is_file():
        return [p for p in repo_root.rglob("*.py") if _keep(p)]
    policy = ag.load_policy(repo_root, policy_path)
    process_slugs = [c for c in policy.components if c.editability in ("editable", "conditional")]
    files: List[Path] = []
    for p in repo_root.rglob("*.py"):
        if not _keep(p):
            continue
        rel = str(p.relative_to(repo_root)).replace(os.sep, "/")
        comp = ag.resolve_component(rel, policy.components)
        # process code = resolves to an editable/conditional component
        if comp is not None and comp.editability in ("editable", "conditional"):
            files.append(p)
    return files


def _keep(p: Path) -> bool:
    parts = set(p.parts)
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache",
            "dist", "build", "tests", "test"}
    return not (parts & skip) and not p.name.startswith("test_")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Static instance-fitting lint for process code (advisory).")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--policy", default="arch.policy.json",
                    help="Restrict lint to process (editable/conditional) components. "
                         "If absent, lint all .py.")
    ap.add_argument("--config", default="overfit_lint.json",
                    help="Allowlist config (allowed_ints, allowed_strings).")
    ap.add_argument("--paths", nargs="*", help="Explicit files to lint (overrides policy scoping).")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail", action="store_true",
                    help="Exit 1 if any findings (for a 'review these' CI check). Default: advisory, exit 0.")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    cfg = LintConfig.load(Path(args.config))

    if args.paths:
        files = [Path(p) if Path(p).is_absolute() else repo_root / p for p in args.paths]
    else:
        pol = Path(args.policy)
        files = process_files(repo_root, pol if pol.is_file() else None)

    findings: List[Finding] = []
    for f in files:
        findings.extend(lint_file(f, cfg, repo_root))

    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    else:
        if not findings:
            print("[overfit-lint] no instance-fitting smells in process code.")
        else:
            print(f"[overfit-lint] {len(findings)} possible instance-fitting smell(s) in "
                  f"process code (ADVISORY — a human should confirm each is not overfit):\n")
            for f in findings:
                print(f"  {f.file}:{f.line}  [{f.kind}]")
                print(f"      {f.snippet}")
                print(f"      ↳ {f.detail}")
            print("\n  These are LEGAL edits to process files, but they branch on specific")
            print("  identifiers/constants — the signature of instance-fitting a process.")
            print("  Confirm each encodes GENERAL logic, not knowledge of a specific test case.")
            print("  Allowlist legitimate ones in overfit_lint.json.")

    return 1 if (findings and args.fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
