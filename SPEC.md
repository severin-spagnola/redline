# Editability Policy Spec (v0.1 draft)

**Status:** draft · **Date:** 2026-07-19

A repository-declared, machine-readable **editability policy**: per-component
annotations stating how editable each part of a codebase is, plus a
**deterministic gate** that enforces those rules at the version-control boundary
so an LLM coding agent (or a human) cannot land a change to a protected
component without the required approval — *even under a vague prompt*.

This document specifies the **format** and the **gate semantics**. A reference
implementation follows the spec; the spec is the source of truth.

---

## 0. Motivation & the enforcement model

LLM coding agents edit whatever a prompt leads them to. Under a general prompt
("fix the failing test", "clean this up") an agent has no principled reason to
avoid a soundness-critical module — e.g. formal-verification proof templates
whose correctness the entire product depends on. Existing mechanisms are one of
three things, and none is a graded, semantic, agent-legible edit policy:

- **Binary path allow/deny** (Claude Code `Edit()` deny, `.cursorignore`,
  `.aiignore`, `.clineignore`): access control, no levels, no rationale, and —
  per vendor docs and open bug reports — best-effort, not guaranteed.
- **Post-hoc review gates** (CODEOWNERS + branch protection, CodeRabbit,
  architecture fitness functions): enforce *after* the edit, don't declare
  editability, agent never reads them before acting.
- **Advisory prose** (AGENTS.md, `.cursor/rules`, CLAUDE.md,
  copilot-instructions.md): soft, model-interpreted, ignorable.

**The core principle of this spec: an LLM can never be a hard boundary by
itself.** Any rule the model *reads* — prompt, MCP response, AGENTS.md — is
advisory: it can be ignored, forgotten, or rationalized away. Enforcement must
live in a layer the model does not control.

That layer is the **version-control gate**. The agent may edit anything in its
working copy; it simply **cannot merge a change touching a protected component
into a protected branch** unless the change carries the required approval
signal. The diff is the enforcement surface. This is deterministic file-path
checking — no model judgment in the loop, nothing the agent can route around
(it drives neither the CI runner nor the branch-protection ruleset).

This mirrors the "LLM proposes, a deterministic gate decides" pattern: the model
*proposes* an edit; a deterministic check *decides* whether it may become real.

### The three layers (only the gate is load-bearing)

| Layer | Mechanism | Strength | Agent can bypass? |
|---|---|---|---|
| Advisory | MCP progressive-disclosure lookup / prompt convention | soft — scopes the agent's plan | **yes** (it's information) |
| Interceptor | pre-edit hook (e.g. Claude Code `PreToolUse`, exit 2 = block) | hard for that agent, *if the hook is protected* | no, but agent-specific |
| **Gate** | **CI/PR check: diff vs policy → block merge** | **hard, absolute** | **no** |

The **gate is the guarantee**. The advisory and interceptor layers are optional
ergonomics that reduce wasted round-trips and scope the agent's reasoning; a
conforming deployment MAY ship them but MUST NOT depend on them for enforcement.
This spec fully specifies the format and the gate; it defines the advisory and
interceptor layers as OPTIONAL bindings (§7).

---

## 1. Terminology

- **Component** — a named architectural unit (a module, a directory, a cluster
  of files). Has a machine-readable `component` slug.
- **Policy** — the set of editability annotations for a repository, expressed as
  one or more annotation files.
- **Annotation file** — a JSON file (default name `redline.meta.json`, but the
  name is configurable) placed alongside the code it describes; may hold one
  component object or `{ "components": [ ... ] }`.
- **Editability level** — one of `editable` | `conditional` | `never` |
  `frozen` (§2.2).
- **Gate** — the deterministic check that maps a diff's changed paths to
  components, looks up each editability level, and decides pass/block per the
  configured rules (§4).
- **Override signal** — evidence attached to a change that permits an otherwise
  blocked change to pass (§5).
- **Protected branch** — a branch (e.g. `main`) whose merges the gate guards.

The key words MUST, MUST NOT, SHOULD, MAY are used per RFC 2119.

---

## 2. The annotation format

### 2.1 Component object

```json
{
  "component": "formal-templates",
  "display_name": "Formal Proof Templates",
  "editability": "never",
  "edit_rule": "One sentence: exactly when and how this may change.",
  "description": "One sentence: what this component is.",
  "paths": ["src/verify/proof_templates/**"],
  "sacred_invariants": ["behaviors that must never change"],
  "connects_to": ["other-component-slugs"]
}
```

Required: `component`, `editability`, `edit_rule`, `description`.
Recommended: `paths`, `display_name`, `sacred_invariants`.
Optional (used by the diagram view, not the gate): `connects_to`, `role`,
`role_tag`, `subcomponents`, `layer`, `zone`.

- `component` — slug, `^[a-z0-9]+(-[a-z0-9]+)*$`, unique across the policy.
- `paths` — array of repo-relative globs the component owns. **This is what the
  gate uses to map a changed file → component.** If omitted, the component's
  directory (the location of its annotation file) is its implicit path scope.
- `edit_rule` — human- and agent-legible sentence stating the condition under
  which an edit is legitimate. Consumed by the advisory layer and shown to
  reviewers; the gate does not parse its prose.
- `sacred_invariants` — the properties that must survive any edit; the "why"
  behind the level.

### 2.2 Editability levels

| Level | Meaning | Default gate behavior (§4.2) |
|---|---|---|
| `editable` | No special constraint; normal work. | pass |
| `conditional` | Editable only under `edit_rule`; a human/agent must affirm the condition holds. | pass **only** with an override signal |
| `never` | Not to be edited except deliberately; the norm is "don't". | block; passes only with override |
| `frozen` | Hardest lock (e.g. calibration ground truth). | block; passes only with the *strong* override |

Levels are **ordered** `editable < conditional < never < frozen` by strictness.
A deployment's gate config (§4.1) chooses the behavior per level from the full
menu below — the level names are fixed by the spec; the *policy* they trigger is
configurable, so a repo can be as strict or lax as it wants without inventing
new levels.

### 2.3 Path → component resolution

For a changed file `f`, the gate resolves its component as:

1. The component whose `paths` glob matches `f`, choosing the **most specific**
   match (longest matching glob) if several match.
2. Else the component whose annotation-file directory is the **deepest ancestor**
   of `f` (nearest-file-wins, like `.gitignore`/AGENTS.md nesting).
3. Else `f` is **unannotated** → treated per `unannotated_policy` (§4.1),
   default `pass`.

A file matching no component's `paths` and sitting under no annotated directory
is unannotated. Overlap where two components both claim a file via `paths` is a
policy error the validator MUST report (§6).

---

## 3. Repository layout

```
<repo>/
  arch.policy.json          # gate configuration (§4.1)  [name configurable]
  **/redline.meta.json        # per-component annotations   [name configurable]
```

A single merged view MAY be produced (an `enriched_arch.json`) for tooling, but
the gate operates on the annotation files + the policy config directly so it has
no build step as a prerequisite.

---

## 4. The gate

### 4.1 Gate configuration (`arch.policy.json`)

```json
{
  "spec_version": "0.1",
  "annotation_glob": "**/redline.meta.json",
  "protected_branches": ["main", "release/*"],
  "unannotated_policy": "pass",
  "levels": {
    "editable":    { "on_change": "pass" },
    "conditional": { "on_change": "require", "override": ["justification", "code_owner"] },
    "never":       { "on_change": "block",   "override": ["justification", "code_owner"] },
    "frozen":      { "on_change": "block",   "override": ["justification", "code_owner", "strong_label"] }
  },
  "overrides": {
    "justification": { "type": "pr_body_block", "heading": "Arch-Override" },
    "code_owner":    { "type": "codeowners_review" },
    "strong_label":  { "type": "label", "name": "arch-frozen-approved" }
  }
}
```

- `on_change` ∈ `pass` | `require` | `block`:
  - `pass` — changes always allowed.
  - `require` — allowed **iff** at least one configured `override` is satisfied.
  - `block` — same enforcement as `require` (needs an override) but reported as a
    hard block in output; semantically `block` and `require` both mean "needs an
    override to pass". They differ only in messaging/severity, so a repo can
    distinguish "you must justify this" (`require`, conditional) from "you are
    editing a do-not-touch component" (`block`, never/frozen).
- `override` — the list of override **signal names** (keys of `overrides`) that
  unlock this level. Semantics (`any` vs `all`) are set by `override_mode`
  (default `any`); a level may set its own `override_mode`.
- The **full menu** of behaviors from all three answers is supported: a repo can
  make every permissioned change merely `require` a justification (lenient), or
  `block` never+frozen hard (strict), or grade all four levels — purely by
  editing this config. No level behavior is hardcoded in the gate.

### 4.2 Algorithm

```
inputs: base_ref, head_ref, policy_config, annotation_files, pr_context
1. changed = paths changed between base_ref..head_ref   (git diff --name-only)
2. for each path p in changed:
     comp   = resolve_component(p)                        (§2.3)
     level  = comp.editability if comp else "__unannotated__"
     rule   = policy_config.levels[level]  (or unannotated_policy)
     if rule.on_change == "pass": continue
     satisfied = evaluate_overrides(rule.override, rule.override_mode, pr_context)
     if satisfied: record ALLOWED_WITH_OVERRIDE(p, comp, which signals)
     else:         record VIOLATION(p, comp, level, needed=rule.override)
3. exit 0 if no VIOLATION else exit 1   (block the merge)
```

- The gate is **pure and deterministic**: same diff + same policy + same PR
  context ⇒ same verdict. No network, no model.
- It reports **every** touched component and its verdict (not just the first
  violation), so a contributor sees the whole picture in one run.

### 4.3 Where it runs

- **Primary:** a CI job on `pull_request` targeting a protected branch. A
  VIOLATION fails the job; branch protection ("require status checks") makes the
  failing check block the merge. This is the un-bypassable guarantee.
- **Optional local:** the same checker as a `pre-commit` / `pre-push` hook for
  fast feedback. Advisory locally (a determined user can `--no-verify`); the CI
  job is the real gate.

---

## 5. Override signals

An override is machine-checkable evidence that a protected change was
deliberate. The spec defines these signal types; a deployment enables the ones
it wants in `overrides`. **Both** the reasoning-based and the human-approval
signals are first-class, and they compose.

- `pr_body_block` — the PR description contains a structured, parseable block:

  ```
  ## Arch-Override
  component: formal-templates
  reason: <why this change to a protected component is necessary>
  ```

  The gate parses it; the `reason` is free text (surfaced to reviewers). This is
  the "the agent/human must state its reasoning" signal.

- `codeowners_review` — a CODEOWNERS owner of the touched component's paths has
  an approving review on the PR. Reuses GitHub/GitLab native machinery; this is
  the "a human greenlit it" signal.

- `label` — a named PR label is present (e.g. `arch-frozen-approved`), applied
  deliberately by an authorized party.

- `env_ack` (local only) — an explicit environment variable / flag acknowledging
  the override for a local run; never a substitute for the CI gate.

`override_mode: any` (default) passes if ≥1 listed signal is satisfied;
`override_mode: all` requires every listed signal. A soundness-critical repo
sets `all` on `frozen` to demand *both* a written justification *and* a code
owner's approval.

---

## 6. Validation rules (the validator MUST enforce)

1. Every component has the required fields; `editability` ∈ the four levels.
2. `component` slugs are unique across the whole policy.
3. No file is claimed by two components' `paths` (overlap is ambiguous).
4. Every `overrides` signal referenced by a level's `override` list is defined.
5. `paths` globs are well-formed and repo-relative.
6. (Warning) A component with no `paths` and no annotation-file directory scope
   cannot be mapped from a diff — flag it.
7. (Warning) `connects_to` targets that resolve to no known slug.

---

## 7. Optional bindings (ergonomics; NOT the enforcement)

These make the agent comply willingly and scope its plan, reducing PRs that get
blocked at the gate. They are OPTIONAL and advisory by construction.

### 7.1 MCP progressive-disclosure binding

An MCP server MAY expose the policy as tiered tools so an agent loads only what
it needs, in order:

- `permissions_index()` → the **slim tier**: `[{component, editability,
  description}]` for every component. Cheap, always safe to load first. Enough
  for the agent to know what exists, how locked it is, and what it does — so it
  can choose targets — without rationale, invariants, or paths.
- `component_policy(slug)` → the **deep tier** for one component: `edit_rule`,
  `sacred_invariants`, `paths`, `connects_to`. Loaded only for the components
  the agent has decided it needs to touch.
- `check_edit(path)` → resolves a path to its component + level + what an
  override would require, so the agent can self-check before proposing an edit.

Disclosure order: an agent SHOULD read `permissions_index()` when planning, pick
its target components, then `component_policy()` only those. This keeps the
common case cheap and scopes reasoning before a line is written.

### 7.2 Prompt-convention fallback (no server)

Where MCP is unavailable, a documented convention: an `AGENTS.md` fragment
tells the agent to read the slim index file first, choose targets, then read the
per-component annotation for those. Same tiering, no server.

### 7.3 Pre-edit interceptor binding

The policy MAY be compiled into a pre-edit hook (e.g. Claude Code `PreToolUse`
returning exit 2 on a would-be edit to a `never`/`frozen` path). Hard for that
agent *only if the hook itself is outside the agent's editable set* — which the
policy can declare (mark the hook path `frozen`). Still, the CI gate remains the
guarantee, because a hook is per-agent and, as documented in the research,
agents can sometimes edit their own hooks.

---

## 8. Conformance

A **conforming policy** = annotation files + `arch.policy.json` that pass §6.

A **conforming gate** MUST:
- resolve paths→components per §2.3,
- apply level behaviors and override evaluation per §4–§5 from the config
  (no hardcoded level policy),
- be deterministic and offline,
- exit non-zero on any VIOLATION,
- report every touched component's verdict.

A **conforming deployment** runs a conforming gate on PRs to its
`protected_branches` with the CI status check required by branch protection.
Optional bindings (§7) MAY be present and MUST NOT be relied on for enforcement.

---

## 9. Non-goals

- Not a security sandbox. It gates **merges**, not process/filesystem access. An
  agent can still read/modify files locally; OS sandboxing is a separate concern.
- Not a dependency/architecture-rule checker (ArchUnit, dependency-cruiser).
  Those validate structure; this governs *editability*. They compose.
- Not a replacement for AGENTS.md / CODEOWNERS. It **emits into** and **reads
  from** them (§5, §7.2) rather than replacing them.

---

## Appendix A — worked example (quant backtest / look-ahead bias)

A strategy's signal logic lives in `strat/golden.py`. The signal clause is marked
`never` with an intra-file marker (SPEC §, "marker anchoring"):

```python
# arch:begin signal-clause never reason="signals must be causal — no look-ahead"
def compute_signal(fast, slow):
    return fast > slow          # decision uses only info available at bar close
# arch:end signal-clause
```

`arch.policy.json` sets `never → block (justification｜code_owner)`.

- An agent, told *"improve the Sharpe"* or *"make the backtest pass"*, edits the
  clause to `return fast.shift(-1) > slow` — a locally-easy change that **peeks at
  the next bar** (look-ahead bias, which silently inflates every backtest metric).
- It opens a PR to `main`. The gate diffs the change, sees the edit lands inside
  the `signal-clause` `never` region → **VIOLATION**; the PR has no
  `## Arch-Override` and no code-owner approval → check fails, merge blocked.
- The gate posts a prescriptive comment: *"You edited `signal-clause` (never) at
  `strat/golden.py` — signals must be causal, no look-ahead. Revert those lines,
  OR add an `## Arch-Override` block with a reason. Do not keep editing to pass."*
- Deleting the markers to escape the block is itself a **VIOLATION**
  (guard-deletion). To land the change, a human must deliberately override.

The model *knows* what look-ahead bias is — ask it and it will explain it — but
under a vague prompt it introduces it anyway, because nothing encoded that this
clause is causal-by-contract. Now something does, and the merge deterministically
cannot happen without a human saying why.
