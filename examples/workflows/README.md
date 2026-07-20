# Workflow templates

Copy these into `.github/workflows/` in your repo (drop the `.template`
suffix) to enable Redline in CI:

- **`redline.yml.template`** → `.github/workflows/redline.yml` — the editability
  gate: runs on PRs, posts a sticky prescriptive comment, and fails a required
  status check on violation. Make it a **required** check in branch protection.
- **`tests.yml.template`** → `.github/workflows/tests.yml` — runs Redline's own
  test suite (only needed if you fork/modify the gate).

They live here as `.template` files so the repo can be pushed without a GitHub
token carrying `workflow` scope; renaming is a one-time step in your own repo.
