# Redline — thesis-enforcement guardrails for AI coding agents

> Encode a project's non-negotiable theses as a **machine-checked,
> version-controlled property of the code itself**, so that defeating them
> requires a deliberate, reasoned, human-visible override — instead of being the
> accidental path of least resistance for an agent under a vague prompt.

*Mark the code an agent must not touch **redline**; the merge gate enforces it.*

---

## The problem

Under a vague prompt ("make the backtest pass", "fix the failing test", "clean
this up"), an LLM optimizes the **local** objective by editing wherever is
nearest the symptom — which is routinely the wrong place: downstream of the real
cause, the overfit patch, or the load-bearing invariant whose whole point is to
stay fixed.

This is **not a reasoning failure.** Ask any frontier model what look-ahead bias
is and it will lecture you — then introduce it anyway, because the
locally-easiest diff to a backtest (`.shift(-1)`, a same-bar close, a reindex)
happens to peek at the future. Catching it doesn't take intelligence, just
**memory of the project's theory** — the thing you keep re-typing as *"NO, do not
edit X, the entire point of the project is…"*.

Keystone makes that correction **permanent and machine-enforced.**

## Why nobody built this (and the honest scope)

After investigating ~15 tools and standards (see [`PRIOR_ART.md`](PRIOR_ART.md)),
nothing combines: a **graded, semantic, component-level** edit policy that
carries the **reason** and the **invariant**, scopes the agent **before** it
acts, and is **deterministically enforced at the merge boundary**. What exists is
either dumber (binary path ignore/deny), later (post-hoc review), or softer
(advisory prose like AGENTS.md).

It's genuinely valuable mainly on **theory-critical projects** — quant, formal
methods, compilers, consensus, crypto, numerics — where the easy edit is
catastrophic. On a CRUD app the easy edit is usually fine, which is why the pain
never drove anyone to build this. The canonical example throughout these docs is
a **quant backtest**, where an agent editing the signal logic under a vague
prompt reintroduces look-ahead bias and silently invalidates the whole result.

## How it works — one policy source, enforced where the model can't reach

An LLM can **never** be a hard boundary by itself: any rule it *reads* (prompt,
MCP, AGENTS.md) is advisory and can be ignored. So enforcement lives in the layer
the model doesn't control — the **version-control gate**. The agent may edit
anything locally; it simply **cannot merge a change touching a protected
component** into a protected branch without the required approval. The diff is
the enforcement surface. Deterministic file-path checking, no model in the loop.

This is the same principle formal verification tools apply to *proofs* — *the
LLM proposes, a deterministic checker decides* — applied one level up, to the
act of editing code.

```
        ┌───────────────────────────── one policy source ─────────────────────────────┐
        │   *.meta.json  (per-component editability + edit_rule + sacred_invariants)    │
        │   arch.policy.json  (level behaviors + override signals — fully configurable) │
        └───────────────┬──────────────────────┬───────────────────────┬───────────────┘
                        │                       │                       │
                 (advisory, soft)        (interceptor, hard        (GATE, absolute)
                        │                 for that agent)                │
                ┌───────▼───────┐        ┌───────▼───────┐       ┌───────▼───────┐
                │  MCP / prompt  │        │  PreToolUse   │       │  CI on the PR │
                │  (scopes plan) │        │  hook (exit 2)│       │  blocks merge │
                └────────────────┘        └───────────────┘       └───────────────┘
                  ergonomics only          stops the thrash          THE GUARANTEE
```

Only the **gate** is load-bearing. The advisory and interceptor layers are
ergonomics that keep the agent from wasting round-trips.

## The four editability levels

| Level | Color | Meaning | Default gate behavior |
|---|---|---|---|
| `editable` | 🟢 green | normal work | pass |
| `conditional` | 🟡 yellow | editable only under its `edit_rule` | passes only with an override signal |
| `never` | 🔴 red | do-not-touch; the norm is "don't" | blocked; override required |
| `frozen` | 🟥 dark red | hardest lock (e.g. calibration ground truth) | blocked; *strong* override (all signals) |

You mark the **exceptions**; everything defaults 🟢 green. A brand-new file is
green (never blocks work) but gets a non-blocking *"label this?"* nudge if it
sits near a red component.

## Onboarding + auto-labeling (keeping the graph honest as the code grows)

Two questions this raises: *how do you create the graph in the first place*, and
*what happens when new code is added later?* Both keep the LLM out of the trust
path.

**Creating the graph** — feed [`ONBOARDING.md`](ONBOARDING.md) to your own coding
agent. It's a guided prompt that walks you through: state your project's thesis →
discover candidate components → draft each one's editability + `edit_rule` +
`sacred_invariants` *from your thesis* → **you ratify**. The agent proposes; your
confirmation is what's written.

**New code later** — you don't hand-label every new component, and an LLM doesn't
auto-decide (that would put it back in the trust path). Instead, onboarding also
produces a **rule library** ([`redline.rules.json`](DESIGN.md)) — human-ratified
rules (`path_glob` / `name_regex` / type → level). New components are then
auto-labeled by **deterministically applying those rules** (precedence: explicit
rule → parent inheritance → sibling-majority → default 🟢). Anything auto-labeled
is **enforced immediately** at its level but tagged **⚠ unratified**, and
[`drift.py`](drift.py) lists every unratified/uncovered component until a human
confirms it. The ruleset *is* the human decision, made once, up front —
classification at runtime is pure deterministic parsing, no model.

## Components

| File | What it is |
|---|---|
| [`SPEC.md`](SPEC.md) | The format + gate semantics (the source of truth). |
| [`DESIGN.md`](DESIGN.md) | Every design decision + rationale. |
| [`PRIOR_ART.md`](PRIOR_ART.md) | Cited proof of what does/doesn't exist. |
| [`arch_gate.py`](arch_gate.py) | **The deterministic gate.** Diff → components → levels → pass/block. Marker-aware (intra-file), guard-deletion-aware, emits prescriptive PR comments. Stdlib only. |
| [`build_labeler.py`](build_labeler.py) → `labeler.html` | **The human labeling tool.** Click your architecture graph to mark 🟡🔴; exports the policy. The LLM is never in the trust path — a human labels. |
| [`ONBOARDING.md`](ONBOARDING.md) | **The guided setup prompt.** Feed to your agent; it drafts the graph + rule library from your thesis, you ratify. |
| [`rule_engine.py`](rule_engine.py) | **Deterministic auto-labeler.** Applies the human-ratified `redline.rules.json` to classify new components (explicit rule → parent → sibling-majority → default). No model at classification time. |
| [`drift.py`](drift.py) | **The drift report.** Lists ⚠ unratified + uncovered components so nothing silently goes stale. |
| [`hooks/pretooluse_arch_guard.py`](hooks/pretooluse_arch_guard.py) | Claude Code `PreToolUse` hook — blocks a protected edit *before* it happens (fail-open on error). |
| [`hooks/pre-push`](hooks/pre-push), [`hooks/install-hooks.sh`](hooks/install-hooks.sh) | Local git hooks for pre-push feedback. |
| [`examples/arch_gate.yml`](examples/arch_gate.yml), [`post_comment.sh`](examples/post_comment.sh) | The GitHub Action: run the gate, post a sticky prescriptive PR comment, fail the required check on violation. |
| [`examples/arch.policy.json`](examples/arch.policy.json) | Example gate config. |
| [`test_arch_gate.py`](test_arch_gate.py) | The gate's test suite. |

## Quick start

```bash
# 1. Label your architecture (once, a few minutes): mark the thesis-critical
#    minority; everything else stays green.
python build_labeler.py --in ../enriched_arch.json --out labeler.html
open labeler.html          # click nodes → 🟡🔴 → Export policy JSON

# 2. Drop the exported arch.policy.json at your repo root, and the per-component
#    editability into your *.meta.json files (or add `# arch:begin … never`
#    markers around intra-file clauses).

# 3. Enforce on PRs: copy examples/arch_gate.yml into .github/workflows/ and make
#    it a REQUIRED status check in branch protection. Now a change to a `never`
#    component can't merge without a stated reason or a code-owner sign-off.

# 4. (Optional) In-session ergonomics: register the PreToolUse hook and install
#    the pre-push hook so an agent gets the prescriptive block before the PR.
```

## The prescriptive feedback loop (why the agent doesn't just thrash)

When a change is blocked, the gate doesn't say "denied" — it says *what to do
next*, delivered where the agent is already looking (a sticky PR comment + the
pre-edit hook):

> You edited `signal-clause` (never) at `strat/golden.py:L40-47` — signals must
> be causal, no lookahead. This component must not be edited. To pass: revert
> those lines (`git checkout <base> -- strat/golden.py`), OR add a
> `## Arch-Override` block to the PR body with a `reason:`. **Do NOT keep editing
> to make this check pass.**

The agent doesn't need to *remember* the rule; the environment re-presents it,
prescriptively, every time it acts.

## Non-negotiables (so future changes don't erode the idea)

- The **LLM is never in the trust path** — not for classifying what's sacred, not
  for enforcement. A human ratifies; the deterministic gate enforces.
- The **gate is the guarantee**; advisory/hook layers must never be depended on.
- **Deleting a guard = violating it.**
- **Green by omission**; annotate exceptions only.
- Scope is **theory-critical projects**; don't oversell as universal.
