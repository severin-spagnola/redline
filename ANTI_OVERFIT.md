# The anti-overfit stack

Redline's path gate stops the LLM editing templates/instances directly. That is
necessary and cheap, but it is a **syntactic** guard (a write boundary), and
overfitting is a **semantic** property (what code does on data it hasn't earned
the right to fit). A path boundary cannot fully contain a semantic problem — close
the "add a template" door and the leak finds a window.

So redline ships four layers, ordered **cheap → expensive**, each catching a
failure the others miss, and **none putting an LLM in the trust path**:

| # | Layer | Catches | Cost | Mechanism | Authority |
|---|---|---|---|---|---|
| 1 | **Path redline** (`arch_gate.py`) | direct edits to templates/instances | ~0 | file write-boundary at the merge gate | **hard block** |
| 2 | **Static lint** (`overfit_lint.py`) | hardcoded names, magic constants in *process* files | ms, deterministic | AST scan: identifier-literals, magic numbers, `==`/`startswith`/`in` on names | flag → human |
| 3 | **Fresh-context judge** (`overfit_judge.py`) | *disguised* construct-overfit; overfit proposer criteria; laundered generalization | one LLM call | no-context subagent, diff + theory, **blind to motivation** | flag → human |
| 4 | **Sealed ratchet** (`SEALED_RATCHET.md`) | *outcome* overfit; selection-gaming; the autonomous-loop gradient toward the visible set | full eval | held-out set the loop **can't see**; promote iff target↑ AND no sealed family↓ | **hard gate on promotion** |

## The two axes that make this a stack, not a pile

**Axis 1 — syntactic → semantic.** Layers 1–2 look at *what the code is* (a path,
a literal). Layers 3–4 look at *what the code does* (a judgment, a measurement).
Overfitting lives on the semantic end, so 1–2 alone are theater; 3–4 are where the
real containment is. But 1–2 are ~free, so they filter the dumb cases before you
spend an LLM call or a sealed eval.

**Axis 2 — who is trusted.** Only layers **1 and 4 are hard** (block / gate) —
and both are **deterministic**: a file-path check and a numeric ratchet. Layers 2
and 3 only **flag**, because:
- Layer 2 is deterministic but *heuristic* (a legitimate constant can look like a
  magic one) — so it advises, with an allowlist.
- Layer 3 is an *LLM* — and an LLM must never be the boundary (a judge that blocks
  is a model deciding what's allowed, the exact thing this whole system forbids).
  It flags fast; the sealed set confirms.

This is the same discipline as redline itself, applied recursively: **the LLM
proposes, deterministic things a human ratified dispose.** The judge proposes a
suspicion; the human and the sealed set dispose.

## How they compose in an autonomous loop

```
   LLM proposes a process edit
            │
   ┌────────▼─────────┐   fail → rejected, cheapest possible
   │ 1. path redline  │   (never touches templates/instances)
   └────────┬─────────┘
            │ pass
   ┌────────▼─────────┐   flag → surfaced to human; does NOT block
   │ 2. static lint   │   (hardcoded name / magic constant?)
   └────────┬─────────┘
            │ (flags noted)
   ┌────────▼─────────┐   flag → surfaced to human; does NOT block
   │ 3. fresh judge   │   (disguised overfit vs. theory?)   [one LLM call]
   └────────┬─────────┘
            │ (flags noted)
   ┌────────▼─────────┐   the ONLY semantic hard gate
   │ 4. sealed ratchet│   promote iff target↑ AND no sealed family↓
   └────────┬─────────┘   loop gets a BINARY promote/reject, never the score
      promote │ reject
         ▼    │    ▼
      lands   │  discarded
```

Order matters for cost: **run the cheap guards first.** Don't spend a sealed eval
(expensive, and a scarce independence resource) on a change the path redline or
lint already rejected. The lint/judge flags travel *with* the change to the human
reviewing a promotion — so when the sealed set says "promote," the human still
sees "…but the lint flagged a magic constant on line 40; confirm it's not fitting."

## What each layer CANNOT do (so you don't over-trust it)

- **Redline** can't see overfit inside a process file. (Layers 2–4 cover this.)
- **Lint** can't see overfit with no literal signature — a process that's
  general-looking but behaviorally fit. False positives on legit constants.
  (Layer 3–4.)
- **Judge** has false negatives (clever disguise reads as general) and false
  positives (legit-specific reads as overfit). Its "pass" is worthless; only its
  "flag" is actionable. (Layer 4 is the truth.)
- **Sealed ratchet** can't prevent the *human eyeball leak* (you inspect a sealed
  failure and generalize from it). Only the **burn-on-inspection discipline** in
  `SEALED_RATCHET.md` addresses that — and it's a rule, not a tool.

## The honest summary

- Do all four. They're cheap→expensive and cover different failures.
- **Redline + lint** kill the frequent, dumb, direct instance-fitting for
  ~nothing.
- **Judge** is a fast advisory filter for the disguised version — trust its flags,
  never its passes.
- **The sealed ratchet is the only thing that measures generalization**, and it is
  honest only while the loop and the human stay blind to it. Redline without a
  sealed set is a very convincing way to overfit while feeling protected.
