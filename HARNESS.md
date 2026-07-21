# Redline as a harness — plugging into any project + coding loop

This is the deployment picture: how redline goes from "a repo of tools" to "the
environment an autonomous coding loop runs against." The governing idea, which
determines the whole design:

> **Redline is a wall with a sign on it, not a driver.** It does not run your
> loop. It blocks invalid/overfit changes and surfaces clear feedback everywhere
> an LLM looks. Any coding loop — yours, Cursor, Claude Code — then hits the wall,
> reads the sign, and adapts. Redline owns the *environment*; the loop stays
> yours.

That separation is deliberate: a guard that also drives the loop is a guard the
loop's author controls. Redline stays a pure boundary, so *any* agent adapts to
it the same way — by finding the correct change, because the wrong one won't land.

## The plug-in flow (any project)

```
1. INSTALL      pip install redline    (or vendor redline.py + the modules)
2. ONBOARD      feed ONBOARDING.md to your agent → it drafts the component graph
                + editability + the redline.rules.json rule library → YOU RATIFY.
                (LLM proposes, human ratifies — the graph is the human's, once.)
3. CONFIGURE    drop arch.policy.json at the repo root; mark templates/instances
                never/frozen, processes editable/conditional.
4. ENFORCE      copy examples/workflows/redline.yml → .github/workflows/, make it
                a REQUIRED status check. Now no invalid/overfit push can merge.
5. SEAL         (for autonomous loops) redline sealed --init-baseline with your
                held-out eval, stored OUTSIDE the loop's workspace.
6. RUN YOUR LOOP  against this environment. Redline blocks + explains; the loop
                adapts. You never build orchestration into redline.
```

## Bring your own model / auth

Nothing in redline calls a hosted API directly. The one LLM component (the
fresh-context judge) shells out to whatever command you give it:

```
redline judge --run 'claude -p'      # your logged-in Claude subscription
redline judge --run 'llm'            # simonw/llm, any backend
redline judge --run './my_api.sh'    # your own API wrapper
```

`claude -p` uses your existing Claude auth/subscription — no separate key. The
judge is advisory, so even here the model is never in the trust path.

## What the loop SEES vs. what it must NEVER see (the firewall)

This is the crux of running a loop without turning it into an overfitting engine.
Surface feedback **everywhere the agent looks** — rules, reasons, verdicts — so it
complies. Withhold exactly one thing — the **sealed score's magnitude** — so it
can't game.

| Signal | Surface to the loop? | Why |
|---|---|---|
| The editability policy (what's protected + `edit_rule` + `sacred_invariants`) | ✅ YES | it must know the rules to comply |
| Gate verdict + **prescriptive reason** ("you edited signal-clause (never) — revert or justify") | ✅ YES | this is how it learns the correct move |
| Lint flags ("hardcoded name on line 40") | ✅ YES | narrows it toward general code |
| Judge flags ("this branches on a specific module") | ✅ YES | advisory nudge toward the theory |
| CI feedback / auditor comments | ✅ YES | the loop should read all of it |
| Sealed gate **verdict** (PROMOTE / REJECT) | ✅ YES | a binary is safe; it means "try again" |
| Sealed gate **scores / margin / which family moved** | ❌ **NEVER** | the moment the loop sees the number, it optimizes toward it → Goodhart → sophisticated overfitting |

The agent "adapting to the guard" is **good** when the guard is deterministic
(path redline, the sealed binary): adapting means *find the correct fix*. It is
**catastrophic** only if it can adapt against the *sealed magnitude* — which it
never sees. So: maximal transparency on rules/reasons/verdicts; zero transparency
on the held-out number.

### Concrete firewall rules

- The sealed baseline/scores live **outside the repo** (`redline sealed` refuses
  to write them inside it). Not in the loop's workspace, context, memory, or logs.
- Pipe `redline sealed`'s **stdout** (just `PROMOTE`/`REJECT`) to the loop. Never
  pipe its `--verbose` **stderr** (the score breakdown) anywhere the loop reads.
- Rate-limit / batch sealed evals. If the loop can propose→check→propose against
  the sealed gate rapidly, it can hill-climb even the *binary* (a slow leak).
- Never let a human paste a sealed score into a prompt the loop will see. Burn any
  sealed card a human inspects (SEALED_RATCHET.md).

## The loop, drawn (redline is the environment, not a box in the loop)

```
        YOUR CODING LOOP (yours to build / bring)
        ┌───────────────────────────────────────────────┐
        │  writer proposes a change                       │
        │        │                                        │
        │        ▼                                        │
        │   [ hits the redline environment ] ◄────────────┼──── redline check
        │        │                                        │      (gate hard-blocks;
        │   reads the verdict + prescriptive reason +     │       lint/judge flag)
        │   lint/judge flags + CI/auditor feedback        │
        │        │                                        │
        │   revises  ──►  (loop)                          │
        │        │                                        │
        │        ▼  candidate that passes `check`         │
        │   [ sealed gate ] ◄─────────────────────────────┼──── redline sealed
        │        │  gets ONLY  PROMOTE / REJECT           │      (scores stay outside)
        │   PROMOTE → land   REJECT → revise (no score)   │
        └───────────────────────────────────────────────┘
```

Redline provides `check` and `sealed` as the two walls. Your loop drives the
writer and reads the feedback. Because the feedback is clear and the walls are
deterministic, the loop *naturally* learns to produce changes that (a) don't touch
protected code, (b) don't hardcode instances, and (c) actually generalize — not
because redline taught it, but because nothing else lands.

## What redline does NOT provide (on purpose)

- **The loop runner / orchestration.** Bring your own (or use Claude Code, Cursor,
  etc.). Redline is the environment they run against.
- **The held-out set + eval command.** Yours — the *content* is project-specific.
  Redline provides the generic seal/ratchet/binary *mechanism* (`sealed_gate.py`);
  you point it at your eval.
- **A hosted model.** Bring your own via `--run` (subscription or API).

## One-screen quickstart for an autonomous loop

```bash
pip install redline                      # or vendor the modules
# (onboard once: feed ONBOARDING.md to your agent, ratify the graph + rules)
cp examples/workflows/redline.yml .github/workflows/   # required check → blocks bad merges

# seal your held-out eval, OUTSIDE the repo:
redline sealed --init-baseline --eval-cmd './score_sealed.sh' \
  --target axi_ordering --baseline-file ~/.redline-sealed/myproj.json

# in your loop, per candidate:
redline check --repo-root . --base origin/main --head HEAD \
  --theory THEORY.md --judge-run 'claude -p'          # hard gate + advisory flags
# if check passes, gate promotion on the sealed ratchet (loop sees only the word):
verdict=$(redline sealed --eval-cmd './score_sealed.sh' --target axi_ordering \
  --baseline-file ~/.redline-sealed/myproj.json)      # PROMOTE | REJECT
```

The loop reads `verdict`; it never reads the file behind it. That single
withheld number is what keeps the whole thing honest.
