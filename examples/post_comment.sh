#!/usr/bin/env bash
# post_comment.sh — post OR update a single STICKY PR comment with the gate verdict.
#
# The editability gate (arch_gate.py --comment-out) writes a Markdown body whose
# first line is the hidden marker `<!-- arch-gate -->`. This script finds an
# existing comment carrying that marker and UPDATES it in place, so each push
# refreshes one comment instead of spamming a new one every run. If none exists,
# it creates one. This is the agent-facing half of the anti-thrash loop
# (DESIGN.md): the agent reads the latest prescriptive fix, not a wall of stale
# comments.
#
# Usage:
#   post_comment.sh <comment-body-file>
#
# Environment (set by the GitHub Action; all required):
#   GH_TOKEN   — token with pull-requests:write (github.token is enough)
#   PR_NUMBER  — the pull request number
#   REPO       — owner/repo (e.g. octo/widgets)
#
# Requires: gh, jq. Uses only the gh CLI (no third-party actions).
set -euo pipefail

MARKER='<!-- arch-gate -->'

body_file="${1:-}"
if [ -z "${body_file}" ] || [ ! -f "${body_file}" ]; then
  echo "post_comment.sh: ERROR: comment body file '${body_file}' not found" >&2
  exit 2
fi

: "${GH_TOKEN:?post_comment.sh: GH_TOKEN must be set}"
: "${PR_NUMBER:?post_comment.sh: PR_NUMBER must be set}"
: "${REPO:?post_comment.sh: REPO must be set (owner/repo)}"

# Find the id of the existing sticky comment (the first issue comment whose body
# contains our marker). Empty if none. The PR's comment thread is the issue
# comments endpoint. --paginate walks all pages so we still match on busy PRs.
existing_id="$(
  gh api --paginate "repos/${REPO}/issues/${PR_NUMBER}/comments" \
    --jq "map(select(.body | contains(\"${MARKER}\"))) | first | .id // empty" \
    2>/dev/null || true
)"

if [ -n "${existing_id}" ]; then
  # UPDATE in place (PATCH the comment by id). -F body=@file passes the raw file
  # as the JSON `body` field without shell-escaping headaches.
  echo "post_comment.sh: updating existing arch-gate comment #${existing_id}"
  gh api --method PATCH "repos/${REPO}/issues/comments/${existing_id}" \
    -F body=@"${body_file}" >/dev/null
else
  # CREATE a new sticky comment.
  echo "post_comment.sh: creating new arch-gate comment"
  gh pr comment "${PR_NUMBER}" --repo "${REPO}" --body-file "${body_file}"
fi
