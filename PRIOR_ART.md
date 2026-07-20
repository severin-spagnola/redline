# Prior Art & Positioning

**Status:** research-grounded · **Date:** 2026-07-20

This document establishes what is *novel* about the editability-policy mechanism
specified in `SPEC.md` and pitched in `DESIGN.md`. It is deliberately
non-promotional: its job is to let a skeptical reader (or a future maintainer)
trust that the niche is genuinely unfilled. Every factual claim about an existing
tool cites a source URL from the prior-art investigation (~15 tools/standards,
104 extracted claims, each adversarially verified). Where the research
*contradicts* a framing in `DESIGN.md`/`SPEC.md`, it is flagged rather than
smoothed over.

---

## 1. The gap, in one paragraph

No existing tool or standard combines all of the following: a policy that is
(a) **graded** — more than binary allow/deny (e.g. `editable` / `conditional` /
`never` / `frozen`); (b) **semantic** — carrying the *reason* an edit is
forbidden and the *invariant* it protects, not just a path; (c)
**component-level** — attached to an architectural unit, including intra-file
regions, not only whole files; (d) **read by the agent to scope it *before* it
acts**; and (e) **deterministically enforced at the merge boundary**, in a layer
the agent does not control. After investigating roughly fifteen tools and
standards, each of these five properties exists *somewhere*, but nothing puts
them together. The landscape splits cleanly into three buckets, none of which is
a graded, agent-legible, deterministically-enforced edit policy — plus one
genuine conceptual neighbor in a different domain.

---

## 2. The landscape, in three buckets

### Bucket 1 — Binary path allow/deny (access control)

These say *which files* an agent may touch, as an on/off list. They are the
closest in mechanism, and the furthest in intent: no levels, no rationale, and —
by the vendors' own admission — best-effort rather than guaranteed.

- **Claude Code `Edit(path)` deny rules** in `.claude/settings.json`. Path-scoped
  `Edit`/`Read` rules do exist, cover all built-in file-writing tools, and can
  scope a directory subtree; they are enforced by the harness, not the model,
  and a prompt/CLAUDE.md instruction cannot override them
  ([Configure permissions — Claude Code Docs](https://code.claude.com/docs/en/agent-sdk/permissions)).
  **But the "hard guarantee" framing does not survive scrutiny.** Multiple
  primary bug reports show path-scoped `Edit`/`Write` deny rules failing to
  block: [#6631](https://github.com/anthropics/claude-code/issues/6631)
  (deny non-functional, v1.0.93), [#22907](https://github.com/anthropics/claude-code/issues/22907)
  and [#34741](https://github.com/anthropics/claude-code/issues/34741)
  (absolute-path deny rules don't block), [#11662](https://github.com/anthropics/claude-code/issues/11662)
  (absolute paths bypass Bash deny), and [#16461](https://github.com/anthropics/claude-code/issues/16461)
  (model escapes a Write/Edit block via a Bash heredoc — closed *not planned*).
  A separate report shows `permissions.deny` on `~/.claude/hooks/**` being
  overridden by the agent it was meant to restrain, with the immutable-region
  feature requested-but-absent and the issue closed *not planned*
  ([#11226](https://github.com/anthropics/claude-code/issues/11226)). Note the
  seam this *does* provide: a **`PreToolUse` hook** runs before permission
  evaluation and its deny holds even in `bypassPermissions`; the docs ship a
  ready "Block edits to protected files" hook
  ([hooks guide](https://code.claude.com/docs/en/hooks-guide)) — this is exactly
  the interceptor seam `SPEC.md §7.3` binds to.
- **`.cursorignore`, `.aiignore`, `.clineignore`, `.codeiumignore`** — one
  ignore-file per vendor. These primarily exclude files from *indexing/context*
  and are best-effort: Cursor's own docs state "complete protection isn't
  guaranteed," ignored files still appear in listings, and terminal commands the
  agent runs are not blocked (files remain reachable via `cat`/`less`)
  ([per-tool comparison — Agent Rules Builder](https://www.agentrulegen.com/guides/how-to-protect-files-from-ai-agents)).
  JetBrains' **`.aiignore`** is the same shape — a gitignore-style read/access
  exclusion, binary ignore/allow, with no graded edit levels — and JetBrains
  concedes in its own help text that ignored files "may still be processed due to
  unforeseen issues," i.e. it is explicitly *not* a guaranteed guardrail
  ([JetBrains AI Assistant tracker, LLM project](https://youtrack.jetbrains.com/projects/LLM/issues/LLM-20812)).
  Worse for the guarantee: **agents specifically bypass `.aiignore`** — an open
  bug shows the Codex agent grepping and reading inside an excluded folder
  ([same tracker](https://youtrack.jetbrains.com/projects/LLM/issues/LLM-20812)).
- The cross-tool survey's blunt conclusion: "No AI coding tool provides
  guaranteed security enforcement for file access … All restrictions should be
  treated as defense-in-depth, not security boundaries"
  ([Agent Rules Builder](https://www.agentrulegen.com/guides/how-to-protect-files-from-ai-agents)).

**What's missing:** levels, rationale, sacred-invariant metadata, per-directory
*policy* semantics — and any enforcement the agent cannot route around. These are
access-control lists, not an editability *policy*.

> **Honest correction to any "no tool has levels at all" claim.** The universal
> negative is too strong and the research refutes it: **Aider** exposes
> coarse `read:` vs `file:` (editable) treatment of files in `aider_conf.yml`,
> which is a *two-level* distinction of sorts
> ([aider config](https://aider.chat/docs/config/aider_conf.html), surfaced as a
> counter-source during verification). The novelty claim is therefore *not*
> "no one distinguishes read from edit"; it is the specific five-way combination
> of §1 — graded **and** semantic **and** component-level **and** pre-act
> agent-legible **and** merge-gate-enforced.

### Bucket 2 — Post-hoc review gates (enforce *after* the edit)

These are real enforcement, but they fire *after* the agent has already written
the code, and they say nothing to the agent beforehand about what is editable.

- **CODEOWNERS + branch protection.** This is GitHub's *own recommended* way to
  restrict what the Copilot coding agent may change: "Protect important Copilot
  and MCP configuration files with a `CODEOWNERS` file, and enable the 'Require
  review from Code Owners' rule"
  ([Building guardrails for GitHub Copilot cloud agent — GitHub Docs](https://docs.github.com/en/copilot/tutorials/cloud-agent/build-guardrails)).
  The Copilot cloud agent is governed by the *same* branch rulesets as humans;
  its defaults are action-level (can't push to the default branch, can't merge
  PRs), not per-path. Crucially, CODEOWNERS entries alone enforce *nothing*
  without branch protection, and the agent never parses CODEOWNERS before
  editing — it binds a reviewer/merge gate, not a pre-edit editability level
  ([CODEOWNERS as AI Agent Identity Governance — Aido Labs](https://aidolabs.mintlify.app/articles/practices/codeowners-ai-agent-governance)).
  Copilot agent mode still has *no* command deny list (filed Oct 2025, still open
  per the same survey).
- **CodeRabbit** governs at the review/PR gate, not by scoping edits: it applies
  path- and AST-based *instructions* to directories and relies on protected-branch
  rules blocking merges until reviewers approve — "policy as configuration means
  encoding the rules a change must satisfy," validated *against* a change rather
  than gating the edit before it happens
  ([AI Governance for Coding Agents — CodeRabbit](https://www.coderabbit.ai/guides/ai-governance-coding-agents)).
- **Architecture fitness functions** (ArchUnit, dependency-cruiser, NetArchTest,
  arch-go), from *Building Evolutionary Architectures* (Ford, Parsons, Kua). A
  fitness function is "an objective integrity assessment of some architectural
  characteristic" that runs in CI and fails the build on a violation — it detects
  **drift after a change is made**, and in the agentic framing is explicitly a
  *post-execution feedback signal* fed back into the agent's verify loop, not a
  pre-edit guardrail
  ([Architecture Fitness Function — aipatternbook](https://aipatternbook.com/architecture-fitness-function);
  [ArchUnit User Guide](https://www.archunit.org/userguide/html/000_Index.html);
  [dependency-cruiser rules](https://github.com/sverweij/dependency-cruiser/blob/main/doc/rules-reference.md)).
  The O'Reilly treatment of agentic architecture governance exposes these
  fitness functions *as MCP tools the agent runs to validate*, the opposite
  direction from an agent reading a "never edit" annotation before writing
  ([How Agentic AI Empowers Architecture Governance — O'Reilly Radar](https://www.oreilly.com/radar/how-agentic-ai-empowers-architecture-governance/)).

**What's missing:** the agent never reads them before acting; they check
*structure/dependencies* or gate *review*, they do not *declare editability*.
`SPEC.md §9` is right that these **compose** with — rather than duplicate — the
editability gate.

### Bucket 3 — Advisory prose (soft, ignorable)

These *do* carry rationale and can be per-directory, but they are natural
language interpreted by the model, with no enforcement — the exact weakness
`DESIGN.md` and `SPEC.md §0` build the gate to close.

- **AGENTS.md** — the de facto standard, read by 15+ agents (Codex, Jules,
  Cursor, Aider, Copilot, Devin…), used by 60k+ projects, now stewarded by the
  Agentic AI Foundation under the Linux Foundation
  ([agents.md](https://agents.md/)). It supports *nested, directory-scoped* files
  (nearest-file-wins) — structurally analogous to a per-directory
  `redline.meta.json` — but only for **prose guidance**: no editability levels, no
  enforced boundary, adherence is voluntary. (Some ecosystems do write ad-hoc
  "never modify" sentences into it — surfaced during verification via
  [morphllm's AGENTS.md guide](https://www.morphllm.com/agents-md-guide) — which
  is precisely the unstructured, unenforced version of this idea.)
- **`.cursor/rules` `.mdc`, CLAUDE.md, copilot-instructions.md** — persistent
  natural-language instruction files, project/team/user scoped. Cursor rules are
  framed throughout the literature as *constraining the LLM to generate code*,
  i.e. context injection, not a write-permission layer
  ([cursor.com/docs/rules](https://cursor.com/docs/rules)).

The decisive evidence that this is an **unmet need** rather than a solved one is
an empirical study of Cursor rules across **401 repositories (1,876 `.mdc`
files, 69,409 coded lines)**: its taxonomy has five themes and **20 codes**
(Convention, Guideline, Project, LLM Directive, Example) and **none** expresses
per-directory/per-module editability, mutability, "never edit," or change-policy
metadata. The closest analog — "LLM Directive / Behavior" — is English-language
prohibitions in prompt context, soft and model-interpreted. AI-specific
directives appear in only ~50% of repos, and the authors attribute the low
adoption partly to **developers being unsure how to express constraints to
LLMs** — direct evidence that a structured way to declare per-component edit
constraints is missing
([Beyond the Prompt: An Empirical Study of Cursor Rules, Jiang & Nam, MSR '26](https://arxiv.org/pdf/2512.18925)).

**What's missing:** enforcement. Anything the model merely *reads* can be
ignored, forgotten, or rationalized away — the core premise of `SPEC.md §0`.

---

## 3. The one genuine neighbor: "Policy Cards"

The single closest thing in the entire landscape is **Policy Cards: Machine-
Readable Runtime Governance for Autonomous AI Agents** (Juraj Mavračić, arXiv
2510.24383, submitted Oct 2025)
([abstract](https://arxiv.org/abs/2510.24383) ·
[full text](https://arxiv.org/html/2510.24383)). It is a machine-readable,
version-controlled, allow/deny + obligations policy artifact **designed to travel
with the agent** and extend the Model/Data/System Card lineage with a *normative*
layer. That is the same *shape* as this project's policy: a checked-in,
machine-checkable card that scopes an agent.

But it is scoped to **compliance, regulatory, and ethical** constraints — *not
code editability*. Its abstract contains no per-module/per-directory editability,
no "do not edit"/"frozen" code metadata, no source-file edit scoping, no
CODEOWNERS/MCP/hook integration. Same pattern, different domain: it validates
the *idea of a governance card that scopes an agent* while leaving the code-
editability niche completely open.

> **Precision correction (flagging the research).** `DESIGN.md`/`SPEC.md` lean on
> a "runtime-enforced, travels-with-agent" reading of Policy Cards. The
> travels-with-agent part is verbatim-supported, but the **runtime-enforcement is
> aspirational, not implemented**: the paper delegates enforcement to "integrated
> policy gateways or API-level middleware," lists "enforcement back-ends" as
> *future work* (§9), models agent behavior as *self-adherence* via autonomous
> checks, and carries an explicit no-warranty disclaimer. So Policy Cards
> validate the **shape** and the *runtime-governance framing*, but they do **not**
> demonstrate a deterministic un-bypassable enforcement layer — which is exactly
> the half this project makes load-bearing (the merge-boundary gate, `SPEC.md §4`).
> If anything, that strengthens the novelty: the neighbor stops where this
> project's contribution begins.

---

## 4. CodeBoarding specifically (an integration target, not a competitor)

**CodeBoarding** is descriptive architecture-for-agents: it generates high-level
system diagrams, component diagrams, and Markdown/Mermaid docs in a
`.codeboarding/` directory, positioned as "a visual map of a codebase" that "both
humans and agents can use," with the tagline "See what your AI is building before
it breaks"
([README](https://github.com/CodeBoarding/CodeBoarding/blob/main/README.md)). It
brands itself "an open standard for code understanding" — the nearest thing in
the field to an "architecture-as-code for agents" standard-claim — but the
standard is **descriptive** (a map), not a prescriptive edit policy.

Verified against primary sources (the README, its own generated
`overview.md` fetched directly, and its full issue tracker):

- Its generated component entries use **only descriptive fields** — a Mermaid
  node, dependency edges, a description sentence, related classes/methods, and a
  source-file list with line ranges. There is **no** editability, mutability,
  permission, "frozen," "protected," or "sacred" field
  ([overview.md, verified by direct fetch](https://raw.githubusercontent.com/CodeBoarding/CodeBoarding/main/.codeboarding/overview.md)).
- Its machine-readable outputs — `analysis.json`, `fingerprint.json` — are
  **incremental-analysis baselines**, not policy files; they carry no `edit_rule`
  or `sacred_invariants` semantics.
- **Zero** of its ~10 open issues concern edit permissions, write-guarding, or
  agent editing guardrails; the roadmap points at broader multi-repo analysis and
  language coverage
  ([issues](https://github.com/CodeBoarding/CodeBoarding/issues)).
- It is **actively maintained** (latest release v0.13.0, 2026-07-13; 26 releases)
  and *still* has no policy/permission layer — this is a mature tool that simply
  does not occupy this niche.

CodeBoarding is therefore a natural **integration target, not a competitor**:
feed its analysis of the codebase *in* as the substrate a human then labels
green/yellow/red. The relationship is complementary — it answers "what is this
component and what does it connect to," which is upstream of "how editable is it."

> **Correction to flag.** The research contests the superlative "*closest*
> adjacent standard-claim." Verification judged **AGENTS.md** a more prominent
> "open standard for AI coding agents" and structurally closer to per-directory
> edit-scoping than CodeBoarding
> ([verdict cites agents.md](https://agents.md/)). Treat CodeBoarding as the
> closest *architecture-map* standard-claim, and AGENTS.md as the closest *agent-
> instruction* standard — do not over-claim CodeBoarding as the single nearest
> neighbor overall (that title belongs to Policy Cards on mechanism, §3).

---

## 5. What this project adds

The unfilled combination is the whole point: a **graded** (`editable` /
`conditional` / `never` / `frozen`), **semantic** (`edit_rule` +
`sacred_invariants` carry the *reason* and the *invariant*), **component-level**
(down to intra-file marker regions, `SPEC.md §2, DESIGN.md`) editability policy
that an agent can **read to scope its plan before acting** (the optional MCP /
prompt bindings, `SPEC.md §7`) and that is **deterministically enforced at the
merge boundary** by a diff-gate in a layer the agent does not control
(`SPEC.md §4`). Bucket 1 has (d) partially but not (a)/(b)/(c)/(e)-reliably;
bucket 2 has (e) but not (a)/(b)/(d); bucket 3 has (b)/(c) but not (e); Policy
Cards have the card shape and the *idea* of (e) but in the wrong domain and
without an implemented gate. This project is the intersection.

This maps directly onto the **thesis-enforcement** framing in `DESIGN.md`: the
policy encodes a project's *reasons-for-existing* as git-enforced invariants, so
defeating a thesis requires a deliberate, human-visible override rather than
being the accidental path of least resistance. The **golden-cross / lookahead**
example is the sharp case: under a vague prompt ("make the backtest work"), a
model that *knows exactly what lookahead bias is* will still reintroduce it,
because nothing in its context encodes that the signal clause is thesis-critical
and *why*. As `DESIGN.md` puts it, this is a **consistency failure, not a
reasoning failure** — and none of the fifteen tools above encodes which code is
thesis-critical in a form the agent reads before acting *and* the gate enforces
after. Marking the signal clause `never` with `reason="signals must be causal —
no lookahead"` is precisely the memory the model lacks.

---

## 6. Honest limitations / why it's niche

This is not a universal dev tool, and the research explains *why it was never
built*: the people with the pain rarely overlap with the people shipping agent-
guardrail tooling, who build for the median CRUD repo where the easy edit is
usually fine (`DESIGN.md §"Why nobody built it"`). Concretely:

- **The value is real only on theory-critical projects** — quant/backtesting,
  formal methods, compilers, distributed consensus, cryptography, numerics —
  where the "easy edit" is *catastrophic* (an overfit patch, a reintroduced
  lookahead, a broken invariant) rather than merely suboptimal. On a CRUD app,
  the nearest-the-symptom edit is usually acceptable, so the pain never drove
  anyone to build this. That is exactly why the niche is open — and why it stays
  niche.
- **The enforcement half is genuinely easy** (~200 lines, done and tested per
  `DESIGN.md`); the hard, failure-prone half is **classification** ("which code
  is thesis-critical"), which every naive version dies on because *you cannot
  trust an LLM to decide what an LLM isn't allowed to touch.* The design resolves
  this with a two-speed model (human ratifies, deterministic gate enforces, LLM
  advisory at most) — but it means the tool requires a human to spend the minutes
  to mark the load-bearing minority. It is not zero-effort.
- **The gate governs *merges*, not process/filesystem access** (`SPEC.md §9`).
  It is not a security sandbox; an agent can still modify files locally. OS-level
  sandboxing is a separate, composable concern.
- **Advisory/interceptor layers are ergonomics, not guarantees.** As the Claude
  Code and JetBrains evidence in bucket 1 shows, anything the agent *reads* or
  any hook the agent *can edit* is bypassable — which is why `SPEC.md` insists
  the CI merge-gate, and only the gate, is load-bearing. Honest scope: this
  reduces wasted round-trips and blocks the catastrophic merge; it does not make
  the agent *want* to behave.

---

### Sources cited (deduplicated)

- Claude Code permissions: https://code.claude.com/docs/en/agent-sdk/permissions ·
  hooks guide: https://code.claude.com/docs/en/hooks-guide
- Claude Code deny/bypass bug reports: [#6631](https://github.com/anthropics/claude-code/issues/6631) ·
  [#22907](https://github.com/anthropics/claude-code/issues/22907) ·
  [#34741](https://github.com/anthropics/claude-code/issues/34741) ·
  [#11662](https://github.com/anthropics/claude-code/issues/11662) ·
  [#16461](https://github.com/anthropics/claude-code/issues/16461) ·
  [#11226](https://github.com/anthropics/claude-code/issues/11226)
- Per-tool ignore-file comparison (Cursor/aiignore/clineignore/codeiumignore):
  https://www.agentrulegen.com/guides/how-to-protect-files-from-ai-agents
- JetBrains `.aiignore` (LLM project tracker): https://youtrack.jetbrains.com/projects/LLM/issues/LLM-20812
- Aider config (read:/file: levels): https://aider.chat/docs/config/aider_conf.html
- CODEOWNERS as agent governance: https://aidolabs.mintlify.app/articles/practices/codeowners-ai-agent-governance
- GitHub Copilot cloud-agent guardrails: https://docs.github.com/en/copilot/tutorials/cloud-agent/build-guardrails
- CodeRabbit AI governance: https://www.coderabbit.ai/guides/ai-governance-coding-agents
- Architecture fitness functions: https://aipatternbook.com/architecture-fitness-function ·
  ArchUnit: https://www.archunit.org/userguide/html/000_Index.html ·
  dependency-cruiser: https://github.com/sverweij/dependency-cruiser/blob/main/doc/rules-reference.md ·
  O'Reilly agentic governance: https://www.oreilly.com/radar/how-agentic-ai-empowers-architecture-governance/
- AGENTS.md (Linux Foundation): https://agents.md/
- Cursor rules docs: https://cursor.com/docs/rules ·
  Cursor-rules empirical study (401 repos / 1,876 .mdc): https://arxiv.org/pdf/2512.18925
- Policy Cards: https://arxiv.org/abs/2510.24383 · https://arxiv.org/html/2510.24383
- CodeBoarding README: https://github.com/CodeBoarding/CodeBoarding/blob/main/README.md ·
  generated overview.md: https://raw.githubusercontent.com/CodeBoarding/CodeBoarding/main/.codeboarding/overview.md ·
  issues: https://github.com/CodeBoarding/CodeBoarding/issues
