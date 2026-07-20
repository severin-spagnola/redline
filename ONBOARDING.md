# Redline Onboarding — a guided prompt for your coding agent

> **This file is a prompt.** Paste it into your own LLM coding agent (Claude
> Code, Cursor, etc.), or point the agent at this path, and it will walk you
> through standing up Redline for this repository. You stay the authority: the
> agent **drafts**, you **ratify**. Nothing the agent proposes becomes real
> until you say so.

---

## TL;DR for the agent

You are helping the user set up **Redline** — a version-control gate that stops
an AI coding agent from silently editing this project's thesis-critical code
under a vague prompt. Redline enforces at the merge boundary, deterministically,
with **no LLM in the trust path**. Your job is to *draft* the policy; the user
*ratifies* it. Do these five steps, and **STOP at every checkpoint** for the
user's confirmation before writing files or moving on:

1. **Elicit the thesis** — ask the user what silently breaks everything if
   edited wrong. **STOP.**
2. **Discover components** — explore the repo, propose a component table (files
   each owns). **STOP** for merge/split/rename.
3. **Draft editability + the WHY** — per component, draft `editability` +
   `edit_rule` + `sacred_invariants`, *derived from the thesis*. Default 🟢
   `editable`. **STOP** for ratification, then write `redline.meta.json` files.
4. **Elaborate the thesis into a rule library** — turn the user's intent into
   explicit `redline.rules.json` rules so *future* components auto-label with no
   LLM. **STOP** for ratification, then write `redline.rules.json`.
5. **Wrap up** — run the gate, install the CI check, make it required.

### Checkpoints (non-negotiable)

At each **STOP** you MUST pause and get an explicit "yes / here are my edits"
from the user before proceeding. Do not batch steps. Do not write any file
before the user has ratified its contents. If the user goes quiet, ask — do not
assume.

### The one rule that governs everything below

**The LLM proposes; the human ratifies; a deterministic gate enforces.** You may
suggest what looks thesis-critical, but *your suggestion is never what the gate
reads* — the user's ratified label is. And auto-labeling of future code is the
deterministic application of **human-authored rules**, never an LLM judgment at
classification time. Never quietly widen scope; never mark something `never`
without the user confirming the reason.

---

## Step 1 — Elicit the thesis

Before touching the repo, ask the user to state, in plain language:

- **What is this project's core purpose / non-negotiable correctness
  properties?**
- **What are the things that, if edited wrong, silently break everything** —
  where the change compiles, tests pass, but the result is quietly invalid?

Give them domain examples so they can pattern-match:

- **Quant / trading:** "signals must be causal — no look-ahead; the backtest
  must not peek at future bars."
- **Formal methods:** "the proof templates / obligations are the trust root; if
  they're weakened, every 'proof' is meaningless."
- **Compiler:** "the IR lowering must preserve semantics; a pass may not change
  observable behavior."
- **Crypto:** "constant-time paths must stay constant-time; no data-dependent
  branches or table lookups on secrets."
- **Consensus / numerics:** "the ordering/agreement invariant (or the
  calibration ground truth) must not drift."

Capture their answer verbatim — it is the **source of every label you draft
later**. If their thesis is vague, ask follow-ups until you have concrete
"never edit X because Y" statements.

> **STOP.** Do not proceed until the user has given you their thesis. Everything
> downstream is *derived from* this; you are not inventing criticality, you are
> encoding theirs.

---

## Step 2 — Discover candidate components

Now explore the repo and propose the **structural** half — the list of
components and the files/dirs each owns.

How to discover them (in rough order of quality):

- If a real analyzer is available (e.g. **CodeBoarding**) or a prior
  `enriched_arch.json` exists, seed from it.
- Otherwise, run an **import/dependency scan** and look at entry points,
  top-level packages, and directory structure.
- At minimum, **read the tree** and group files into named units.

Propose the result as a table for the user to edit:

| Component (slug)   | Owns (paths/globs)              | What it is (one line)              |
|--------------------|---------------------------------|------------------------------------|
| `signal-pipeline`  | `strat/signal/**`               | Computes trade signals from bars.  |
| `execution`        | `strat/exec/**`                 | Turns signals into orders.         |
| `backtest-harness` | `bt/**`                         | Replays history, scores PnL.       |
| `ui`               | `ui/**`, `web/**`               | Dashboards and reporting.          |

Emphasize to the user: **this is the structural half — you propose, they
correct.** Invite them to merge, split, rename, or drop rows. A component can be
a directory, a cluster of files, or (later) an intra-file region.

> **STOP.** Get the user's confirmed component list before drafting any labels.

---

## Step 3 — Draft editability + the WHY per component

For each **confirmed** component, draft three things, **derived from the thesis
in Step 1** (not invented):

- `editability` — one of `editable` (🟢) / `conditional` (🟡) / `never` (🔴) /
  `frozen` (🟥). **Default to `editable`.** Only propose `conditional`/`never`/
  `frozen` where the user's thesis implies the edit is dangerous. Reserve
  `frozen` for the hardest locks (calibration ground truth, the trust root).
- `edit_rule` — one sentence: exactly when/how this may legitimately change.
- `sacred_invariants` — the properties that must survive any edit (the "why"
  behind the level). These should quote the user's thesis.

Present the drafts to the user as a flip-list — component, proposed level,
one-line reason — and ask them to confirm or flip each. Say plainly: *"I'm
proposing these based on your thesis; your confirmation is what makes them real."*
This mirrors the whole design — **LLM proposes, human ratifies.**

> **STOP.** Get the user to ratify each level before writing anything.

Once ratified, write one `redline.meta.json` per component (place it alongside
the code it describes; the name is configurable but default to
`redline.meta.json`). Use **exactly** this format:

```json
{
  "component": "signal-pipeline",
  "display_name": "Signal Pipeline",
  "editability": "never",
  "edit_rule": "Only with a code-owner review AND a stated reason; the causality contract may never be relaxed.",
  "description": "Computes trade signals from price bars.",
  "paths": ["strat/signal/**"],
  "sacred_invariants": ["signals are causal — no look-ahead; a decision uses only information available at bar close"],
  "connects_to": ["execution", "backtest-harness"]
}
```

Notes to honor:

- Required fields: `component`, `editability`, `edit_rule`, `description`.
  Recommended: `paths`, `display_name`, `sacred_invariants`. `connects_to` is
  optional (diagram only; the gate does not use it).
- `component` is a slug matching `^[a-z0-9]+(-[a-z0-9]+)*$`, unique across the
  whole policy.
- `paths` are repo-relative globs and are **what the gate uses** to map a
  changed file to a component. No two components may claim the same file.
- For a `never`/`frozen` region **inside** a file that is otherwise editable,
  don't lock the whole file — use intra-file markers instead:

  ```python
  # arch:begin signal-clause never reason="signals must be causal — no look-ahead"
  def compute_signal(fast, slow):
      return fast > slow          # decision uses only info available at bar close
  # arch:end signal-clause
  ```

  An edit to any line between the markers is treated as an edit to that
  component at that level — and **deleting the markers is itself a violation**
  (an agent must not escape the lock by removing the guard).

Also drop the gate config at the repo root as `arch.policy.json`. A sound
starting point for a theory-critical repo:

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
    "frozen":      { "on_change": "block",   "override": ["justification", "code_owner", "strong_label"], "override_mode": "all" }
  },
  "overrides": {
    "justification": { "type": "pr_body_block", "heading": "Arch-Override" },
    "code_owner":    { "type": "codeowners_review" },
    "strong_label":  { "type": "label", "name": "arch-frozen-approved" }
  }
}
```

Confirm the `protected_branches` and override signals with the user — a repo can
be lenient (everything merely `require`s a justification) or strict (`never`/
`frozen` hard-block, `frozen` demands *all* signals) purely by editing this
file.

---

## Step 4 — Elaborate the thesis into a rule library (the important part)

Marking today's components is only half. The graph must **not silently go stale**
as the codebase grows — but an LLM must **never** decide what's thesis-critical
at classification time (that would put it right back in the trust path). The
resolution: **the user ratifies the classification RULES once, now; every future
component is then auto-labeled by applying those rules deterministically.**

Your task: take the user's natural-language intent from Step 1 and **elaborate it
into explicit, machine-checkable rules** — a *library of phrases/ideas and the
permissions they map to*. Build the ruleset as complete as their stated intent
supports. Examples of the translation:

- User: *"anything touching the signal pipeline is sacred"* →
  `{ "match": {"path_glob": "strat/signal/**"}, "level": "never", "reason": "signal logic is causal-by-contract — no look-ahead" }`
- User: *"indicator and feature code affects every downstream metric"* →
  `{ "match": {"name_regex": ".*(_ma|indicator|feature).*"}, "level": "conditional", "reason": "feature/indicator code affects every downstream metric" }`
- User: *"the UI is fine to change freely"* →
  `{ "match": {"path_glob": "ui/**"}, "level": "editable" }`

Write it as `redline.rules.json` using **exactly** this format (per DESIGN.md):

```json
{
  "rules": [
    { "match": {"path_glob": "strat/signal/**"}, "level": "never",
      "reason": "signal logic is causal-by-contract — no look-ahead" },
    { "match": {"name_regex": ".*(_ma|indicator|feature).*"}, "level": "conditional",
      "reason": "feature/indicator code affects every downstream metric" },
    { "match": {"path_glob": "ui/**"}, "level": "editable" }
  ],
  "inherit": { "subcomponent": true, "sibling_majority": true },
  "default": "editable"
}
```

Explain the **precedence** to the user so they understand what will happen when
new code lands (first match wins):

1. **Explicit rule** — a `redline.rules.json` rule matches → its level + reason.
2. **Parent inheritance** — a subcomponent inherits its parent's level.
3. **Sibling-majority** — a new sibling inherits the majority level of its
   same-rank peers.
4. **Default + flag** — nothing matches → `default` (green), tagged
   **⚠ auto-labeled, unratified**.

And explain **why this matters**, plainly: *"From now on, classifying new code is
deterministic parsing against these rules that you authored and approved. The
graph never silently goes stale, and no LLM ever decides what's sacred — it only
applied rules you wrote."*

Note the **⚠ unratified** safety property: anything auto-labeled carries
`"ratified": false` and an `"auto_reason"`. It is **enforced at its level
immediately** (an auto-`never` blocks exactly like a real `never`, so a genuinely
critical new component is protected the moment it lands) **but stays surfaced** —
`redline drift` lists every unratified component until a human confirms it (flips
`ratified` to `true`) or changes its level. Nothing critical is left unprotected
while it waits; nothing stays auto-forever in silence.

Present the full drafted ruleset for the user to **review and trim** — this is
the ratification. Trim rules that overreach; add rules for intent you captured
but didn't yet encode.

> **STOP.** Get the user to ratify (and trim) the rule library before writing
> `redline.rules.json`. This file is the standing human authorization for all
> future auto-labeling — it must be theirs, not yours.

---

## Step 5 — Wrap up

Once the `redline.meta.json` files, `arch.policy.json`, and `redline.rules.json`
are written and ratified, tell the user the next steps:

1. **Run the gate locally** against a test diff to confirm it resolves paths →
   components → levels the way they expect. A change to a `never` component with
   no override should fail; the same change with an `## Arch-Override` block (or
   a code-owner approval) should pass.
2. **Install the CI workflow** — copy the example from `examples/workflows/`
   into `.github/workflows/`. It runs the gate on every PR to a protected
   branch and posts a prescriptive comment on violation.
3. **Make it a required status check** in branch protection. *This is the step
   that makes Redline a guarantee* — without it, the check is advisory. An
   agent can edit anything locally, but it **cannot merge** a change to a
   protected component without the required override.
4. **(Optional) Install the hooks** — register the `PreToolUse` hook and the
   local `pre-push` hook so an agent gets the prescriptive block *before* the
   PR, in-session. These are ergonomics only; the CI gate remains the guarantee.
5. **Going forward:** as the repo grows, run **`redline drift`** to surface any
   unlabeled or unratified (⚠) components. Each ⚠ is a one-line decision for the
   user — confirm the auto-label or change it. That short tail is the only
   ongoing human step; everything else is the deterministic engine applying the
   rules they ratified today.

---

## Reminder to the agent (keep this true throughout)

- **You propose; the human ratifies; the gate enforces.** Your labels are drafts
  until the user confirms them.
- **Auto-labeling is deterministic application of human-authored rules** — never
  an LLM judgment at classification time.
- **Green by omission.** Default everything to `editable`; annotate only the
  thesis-critical exceptions.
- **Deleting a guard is violating it.** Never suggest removing markers to make a
  check pass.
- **Scope is theory-critical projects.** Don't oversell Redline as universal;
  on a CRUD app the easy edit is usually fine.
