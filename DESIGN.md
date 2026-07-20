# Thesis-Enforcement Guardrails — consolidated design

**Status:** design agreed across discussion, 2026-07-19/20. Spec + deterministic
gate already built and tested (see `SPEC.md`, `arch_gate.py`, 6/6 smoke tests).
This doc records every decision so the build is unambiguous.

---

## The one-sentence pitch

> Encode a project's non-negotiable theses as a machine-checked, version-controlled
> property of the code itself, so that defeating them requires a deliberate,
> reasoned, human-visible override — instead of being the accidental path of least
> resistance for any agent under a vague prompt.

Not "editability levels" (too dev-ops). Not "an LLM marks critical code" (that
reintroduces the weakness one level up). It is: **your architecture's
reasons-for-existing become git-enforced invariants.**

### The failure it kills

Under a vague prompt ("make the backtest work", "fix the failing test"), an LLM
optimizes the *local* objective by editing wherever is nearest the symptom —
which is routinely the wrong place: downstream of the real cause, the overfit
patch, or the load-bearing invariant whose whole point is to stay fixed. This is
**not a reasoning failure** (the model *knows* what lookahead bias is); it is a
**consistency failure** — nothing in its context encodes which code is
thesis-critical and why. Catching it doesn't take intelligence, just memory of
the project's theory. That memory is exactly what a machine-checked annotation
provides, so the human stops re-typing "NO, DO NOT EDIT X, THE ENTIRE POINT IS…".

### Why nobody built it (honest)

1. The people with the pain (quant, formal methods, compilers, consensus, crypto,
   numerics) rarely overlap with the people shipping agent-guardrail dev-tools,
   who build for the median CRUD repo where the easy edit is usually fine.
2. The **enforcement** half is genuinely easy (~200 lines, done) — but
3. the **classification** half ("which code is thesis-critical") is the hard part
   every naive version dies on, *because you can't trust an LLM to decide what an
   LLM isn't allowed to touch.* Resolved below by the two-speed model.

Scope honestly: this is for **theory-critical projects** where the easy edit is
catastrophic. That's the market. The canonical worked example is a quant backtest
(look-ahead bias); it applies equally to formal-methods proof obligations,
compiler passes, consensus code, and numerics.

> **Accuracy notes (from the adversarially-verified prior-art review — see
> `PRIOR_ART.md`):**
> - Claude Code `Edit()`/`permissions.deny` is **best-effort, not a hard guard**
>   — multiple primary bug reports show it failing to block. Do not claim it as
>   hard enforcement; the *CI gate* is the only hard layer here.
> - The novelty is the **specific five-way combination** (graded · semantic ·
>   component-level · read-before-acting · deterministically-merge-enforced), not
>   "no tool has levels" — Aider has coarse `read:`/`file:` levels. State the
>   combination, not a blanket negative.
> - "Policy Cards" (the closest mechanism-neighbor) has **aspirational, not
>   implemented** runtime enforcement — which strengthens novelty, since it stops
>   exactly where this project's real gate begins.

---

## The trust architecture (two-speed)

The core rule: **an LLM can never be a hard boundary, and never the authority on
what is sacred.** So classification and enforcement are separated by trust:

| Layer | Who | Trust | Cadence |
|---|---|---|---|
| Draft classification | (optional) an LLM suggests "this looks thesis-critical" | untrusted, advisory | on new code |
| **Ratification** | **a human** labels green/yellow/red on the graph | **trusted — the source of truth** | minutes, once, amortized |
| **Enforcement** | **deterministic diff-gate at the merge boundary** | **absolute, un-bypassable** | every PR |

An LLM classifier, if ever added, is a drafting *assistant only* — it may
pre-color the graph for a human to confirm, but its output is never what the gate
reads. The **ratified label** is. This is the same "LLM proposes, deterministic
gate a human approved disposes" pattern that formal verification applies to
proofs — applied one level up, to the act of editing code.

---

## Labeling UX (decisions)

- **The graph IS the labeling surface.** The architecture diagram already built
  (nodes, zones, hover, click-expand) becomes the tool: you look at the picture
  and mark nodes.
- **Label from scratch, mark exceptions only.** You select **reds** (never) and
  **yellows** (conditional); **everything unmarked defaults green** (editable).
  You annotate the load-bearing minority, not the whole repo — that's why it's
  "a few minutes".
- **The policy file stores only the non-green set.** Green is the absence of a
  mark.
- **Nodes decompose into subgraphs, including intra-file.** A backtest/replay
  file is not one node but a subgraph: data-load → feature-calc →
  **signal-clause** → sizing → execution → PnL. You can mark the *signal-clause*
  node red without locking the whole file.

### Default for NEW / unlabeled code

- Unmarked (incl. brand-new files) = **green**, so new work never blocks.
- **But** the gate emits a **non-blocking nudge** — `review: label this?` — when a
  changed/new file *imports*, *sits under*, or `connects_to` a **red** node, so a
  new fit-critical file doesn't silently stay green forever. Convenience default
  + safety nudge; never fails the build on its own.

---

## Intra-file anchoring (how a node points at code that survives edits)

Line numbers drift every commit, so a naive line-range lock is wrong within one
commit. Chosen approach: **marker comments** (simplest, language-agnostic,
survives edits because markers move with the code), with the two other methods
(symbol+pattern, AST-path) available as stronger anchors later.

```
# arch:begin signal-clause never  reason="signals must be causal — no lookahead"
signal = (fast > slow)            # ... the guarded region ...
# arch:end signal-clause
```

- The gate treats an edit to any line **between** a region's markers as an edit to
  that component at that level.
- **Deleting the guard counts as editing the guarded thing.** If a
  `# arch:begin … never` marker present in the base ref disappears in the diff,
  that is a VIOLATION (else an agent escapes the lock by removing the markers).
  Cheap: diff the marker set base→head.

---

## Enforcement + the anti-thrash feedback loop (the critical UX)

The real risk is not a missing block — it's the agent **editing X → getting
blocked → forgetting → editing more → thrashing** until its budget is gone. Fix:
the constraint is **re-presented at every point the agent acts, prescriptively.**

- **Prescriptive, not descriptive, output.** Not "X is blocked" but: *"You edited
  `signal-clause` (never — reintroduces lookahead, which invalidates the whole
  backtest). To pass: `git checkout HEAD -- strat/golden.py` to revert those
  lines, OR add an `## Arch-Override` block stating why. Do NOT edit further."*
  The correct next action is inside the message.
- **Delivered where the agent is already looking, two ways (both, same policy
  source):**
  1. **Post-edit / pre-merge (the guarantee):** a **GitHub Action posts the
     verdict as a PR comment** (via `gh pr comment` / checks API) and fails the
     required status check. The agent reads PR state on its next loop and sees the
     prescriptive fix. **No app, no server, no custom API needed** — this is ~20
     lines on top of the workflow already drafted.
  2. **Pre-edit (stops the thrash in-session):** a Claude Code **`PreToolUse`
     hook** reads the same policy and refuses the edit at attempt time with the
     same message, so the doomed edit never happens and no round-trip is wasted.
- The agent doesn't need to *remember* "don't touch X"; the environment reminds it
  every time, with the reason and the fix. That is the deep version of "naturally
  reinforced over time" — reinforced *within a session*, not across sessions.

### Delivery mechanism decision (how the message reaches the agent)

- **Now:** GitHub **Action + PR comment** (Option A). Zero infra. This is the
  recommended starting point and covers ~90%.
- **Also now:** local **`PreToolUse` / pre-push hook** for pre-edit blocking.
- **Later (productization):** a **GitHub App** — needed only to (a) react to raw
  `push` server-side, (b) render rich line-level Check annotations, (c) gate the
  merge button directly, and (d) offer a one-click install to other repos. An App
  needs a hosted webhook server + auth; it is the "turn this into a company"
  version, deferred.
- **Truly pre-commit, server-side** (GitHub rejects the push and hands back a
  message) is only possible via a **pre-receive hook on GitHub Enterprise**
  (self-hosted). On github.com the equivalent is the client-side hook above. So
  the answer to "can the PR/push system hand the agent the message" is: **yes,
  via the Action's PR comment + the local hook — you do not need to build your own
  API or a third-party tool** for the core loop.

---

## Gate semantics (built + tested — recap)

One policy source (`*.meta.json` editability + `arch.policy.json` config) feeds a
deterministic checker (`arch_gate.py`). Per-level behavior and override signals
are **fully configurable**, not hardcoded:

- `editable` → pass · `conditional` → require override · `never` → block
  (override to pass) · `frozen` → block (strong override, e.g. `override_mode:
  all`).
- Override signals (compose): `pr_body_block` (stated reasoning),
  `codeowners_review` (human greenlit), `label`, `env_ack` (local).
- Runs in CI on PRs to protected branches; a violation fails the required status
  check → merge blocked. Deterministic, offline, no model in the trust path.
- 6/6 smoke tests pass: editable passes; never blocks; never+justification
  passes; conditional+owner passes; frozen w/ only justification blocks; frozen
  w/ all signals passes.

---

## Build order

1. **[DONE]** Spec (`SPEC.md`) + deterministic gate (`arch_gate.py`) + example
   policy + example Action skeleton. Tested.
2. **Prescriptive feedback + marker anchoring + PR-comment posting + PreToolUse
   hook** — the anti-thrash core that makes it usable by agents. *(highest value
   next)*
3. **Graph labeling tool** — extend the diagram into a from-scratch red/yellow
   marker (green by default), writing the policy; subgraph/intra-file nodes.
4. **Positioning / prior-art doc** (from the 69 verified research claims) +
   golden-cross / look-ahead worked example.
5. **[Later]** GitHub App for push-time + one-click install; LLM drafting
   assistant (advisory only); AST/symbol anchors as stronger intra-file options.

---

## Non-negotiables (so future changes don't erode the idea)

- The **LLM is never in the trust path** — not for classification authority, not
  for enforcement. Human ratifies; deterministic gate enforces.
- The **gate is the guarantee**; advisory/hook layers are ergonomics and must not
  be depended on for enforcement.
- **Deleting a guard = violating it.**
- **Green by omission**; annotate exceptions only.
- Scope is **theory-critical projects**; do not oversell as universal.
