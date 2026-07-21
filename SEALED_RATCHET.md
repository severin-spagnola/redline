# The Sealed Ratchet — the backstop redline can't be

**Status:** spec + discipline. This is the one guard that contains *outcome*
overfitting — the kind with no syntactic signature, that the path redline, the
static lint, and the LLM judge all miss.

## Why the other guards aren't enough

- **Path redline** stops the LLM editing templates/instances directly (a write
  boundary). It cannot see a *process* that is secretly overfit.
- **Static lint** catches hardcoded names/magic constants in process code
  (deterministic). It cannot see a process that is overfit *without* any such
  literal.
- **Fresh-context judge** catches disguised construct-overfit (advisory, LLM). It
  has false negatives, and it is an LLM — never a gate.

All three are guards on *what the code looks like*. But overfitting is a property
of *what the code does on data it hasn't earned the right to fit*. In a fully
autonomous loop the failure isn't one bad edit — it's **gradient descent toward
the visible set over many iterations**. Every individual edit passes redline,
passes the lint, passes the judge. The *aggregate* is overfitting, because the
loop's fitness signal is "does better on the set I can see," and it will slowly,
legally, shape processes to that set.

The only thing that catches this is a set the loop's reward **cannot see**, so the
loop cannot optimize toward it. That is the sealed held-out set. It is honest
*precisely because the loop is blind to it.*

## The invariant

> Promotion of any process change is gated on a **sealed held-out set** that the
> autonomous loop cannot read, cannot log, cannot use as a reward signal, and
> cannot influence. A change is promoted only if it **improves its target** on
> visible data **AND does not regress** the sealed set on any previously-passing
> family. The sealed number is checked by a process outside the loop; the loop
> never receives it as feedback.

Two halves, both required:

1. **Blindness** — the loop must be structurally unable to see the sealed number.
   Not "we don't show it" — *unable*. If any path exists for the sealed result to
   reach the loop's fitness function, log, prompt, or memory, it becomes a target
   and stops measuring generalization (Goodhart). The set must be sealed at the
   infrastructure level: separate storage, not mounted in the loop's workspace,
   results written only to a channel the loop can't read.

2. **Ratchet, not threshold** — "≥X% on the target" is gameable by trading away
   coverage elsewhere (this is selection-gaming's exact move). The real gate is a
   *monotonic* one:
   - the change must **improve** (or hold) its declared target on visible data, and
   - it must **not lower** the sealed number on **any family that was passing
     before**. A single regression on a previously-green family blocks promotion,
     even if the target went up.

   Overfit edits almost always read as "up here, quietly down there." The ratchet
   is what makes "down there" fatal.

## How it wires into the autonomous loop

```
                          ┌─────────────────────────────────────────┐
   loop proposes ───────► │  VISIBLE eval  (dev/train set)           │
   a process edit         │  loop CAN see this; it's the fitness sig │
                          └──────────────────┬──────────────────────┘
                                             │ candidate that improves target
                                             ▼
   ┌──────────────────────────────────────────────────────────────────────┐
   │  PROMOTION GATE  (runs OUTSIDE the loop, on the sealed set)            │
   │    promote  IFF   target improved on visible                          │
   │                   AND  no previously-passing sealed family regressed  │
   │    the loop NEVER receives the sealed number — only a binary          │
   │    promote / reject, and even that must not encode the margin.        │
   └───────────────────────────────┬───────────────────────────────────────┘
                     promote        │        reject
                        ▼           │           ▼
                 change lands       │     change discarded
                                    │     (loop gets: "rejected", nothing more)
```

- The loop optimizes against the **visible** set freely — that's fine, that's what
  it's for.
- The **sealed** set is evaluated by a separate runner. It returns to the loop at
  most a **binary** promote/reject — never the score, never which family moved,
  never the margin. Any richer signal is a channel the loop will learn to exploit.
- Even the binary should be used sparingly: if the loop can propose→check→propose
  against the sealed gate rapidly, it can hill-climb the *binary* over many tries
  (a slow information leak). Batch promotions; rate-limit sealed evals; prefer
  promoting on a cadence rather than per-edit.

## The discipline no tool can enforce (the human leak)

The most insidious overfit is **you** looking at a sealed failure, understanding
it, and generalizing a process from n=1. No file boundary, lint, or judge stops a
human from eyeballing a specific failure and laundering it into a "general"
process. And the sealed set can't detect it if your generalization happens to help
this round.

The only defense is a rule you keep:

> **Every sealed card a human inspects is burned.** The moment you look at a
> specific sealed failure and act on it, that card has leaked into the process
> through your eyes. Rotate it out of the sealed set permanently. The sealed set's
> honesty decays with every human eyeball; treat inspection as consumption.

Maintain a **reserve** of sealed families never yet inspected, and rotate burned
ones out. A sealed set that has been fully inspected is a dev set wearing a
disguise.

## Checklist for a sound sealed ratchet

- [ ] The sealed set lives where the loop **cannot read it** (separate infra, not
      in the loop's workspace/context/memory).
- [ ] The loop's fitness/reward signal is computed **only** from visible data.
- [ ] Promotion requires **target-improved AND no previously-passing sealed family
      regressed** (ratchet, not threshold).
- [ ] The loop receives at most a **binary** promote/reject — never the score,
      margin, or which family moved.
- [ ] Sealed evals are **rate-limited / batched** so the binary can't be
      hill-climbed.
- [ ] A **reserve** of never-inspected sealed families exists; any card a human
      inspects is **burned and rotated out**.
- [ ] The redline, lint, and judge run **before** the sealed eval — they're the
      cheap filters; the sealed set is the expensive truth. Don't spend a sealed
      eval on a change a cheaper guard already rejected.

## The one sentence

> The redline stops direct instance-fitting; the sealed held-out set is what
> actually measures whether a process generalizes — and it is honest only for as
> long as the loop (and the human) cannot see or optimize against it.
