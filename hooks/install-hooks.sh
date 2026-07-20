#!/usr/bin/env bash
# install-hooks.sh — install the editability-gate pre-push hook into a repo.
#
# Symlinks (or copies) tools/arch/spec/hooks/pre-push into <repo>/.git/hooks/.
# Idempotent: safe to re-run; re-installs cleanly. Advisory only — the CI gate
# (arch_gate.yml) is the real enforcement (see DESIGN.md / SPEC.md).
#
# Usage:
#   install-hooks.sh [TARGET_REPO]     install into TARGET_REPO (default: repo
#                                      containing this script's git tree, i.e.
#                                      the current repo)
#   install-hooks.sh --copy [REPO]     copy the hook instead of symlinking
#                                      (use when .git/hooks can't follow symlinks,
#                                      e.g. some Windows/worktree setups)
#   install-hooks.sh --uninstall [REPO]  remove the hook we installed
#   install-hooks.sh -h | --help
#
# It never clobbers a pre-existing NON-arch-gate pre-push hook: if one is present
# and it isn't ours, we refuse and tell you to merge manually.
set -euo pipefail

MARKER='arch-gate:pre-push'   # sentinel string present in our hook

# --- resolve this script's directory (source of the hook) -------------------
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
HOOK_SRC="${script_dir}/pre-push"

usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

mode="install"
copy=false
target=""

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) mode="uninstall" ;;
    --copy)      copy=true ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "install-hooks.sh: unknown flag: $1" >&2; usage; exit 2 ;;
    *)           target="$1" ;;
  esac
  shift
done

if [ ! -f "${HOOK_SRC}" ]; then
  echo "install-hooks.sh: ERROR: hook source not found at ${HOOK_SRC}" >&2
  exit 2
fi

# --- resolve target repo + its hooks dir ------------------------------------
# Default target: the git repo we're currently in.
if [ -z "${target}" ]; then
  target="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "${target}" ]; then
    echo "install-hooks.sh: ERROR: no TARGET_REPO given and not inside a git repo." >&2
    exit 2
  fi
fi

if [ ! -d "${target}" ]; then
  echo "install-hooks.sh: ERROR: target '${target}' is not a directory." >&2
  exit 2
fi

# Honor core.hooksPath and worktrees/submodules: ask git for the real hooks dir.
hooks_dir="$(git -C "${target}" rev-parse --git-path hooks 2>/dev/null || true)"
if [ -z "${hooks_dir}" ]; then
  echo "install-hooks.sh: ERROR: '${target}' is not a git repository." >&2
  exit 2
fi
# rev-parse may return a path relative to the repo; normalize to absolute.
case "${hooks_dir}" in
  /*) : ;;
  *)  hooks_dir="${target}/${hooks_dir}" ;;
esac
dest="${hooks_dir}/pre-push"

is_ours() {
  # True if $1 exists and is our hook (symlink to it, or a copy carrying MARKER).
  local f="$1"
  [ -e "${f}" ] || [ -L "${f}" ] || return 1
  if [ -L "${f}" ]; then
    local tgt; tgt="$(readlink "${f}" 2>/dev/null || true)"
    case "${tgt}" in */pre-push|"${HOOK_SRC}") return 0 ;; esac
  fi
  grep -q "${MARKER}" "${f}" 2>/dev/null && return 0
  return 1
}

# --- uninstall --------------------------------------------------------------
if [ "${mode}" = "uninstall" ]; then
  if [ ! -e "${dest}" ] && [ ! -L "${dest}" ]; then
    echo "install-hooks.sh: nothing to uninstall (${dest} absent)."
    exit 0
  fi
  if is_ours "${dest}"; then
    rm -f "${dest}"
    echo "install-hooks.sh: removed ${dest}"
    exit 0
  fi
  echo "install-hooks.sh: REFUSING to remove ${dest} — it is not the arch-gate hook." >&2
  exit 1
fi

# --- install ----------------------------------------------------------------
mkdir -p "${hooks_dir}"

# Refuse to clobber a foreign existing hook.
if { [ -e "${dest}" ] || [ -L "${dest}" ]; } && ! is_ours "${dest}"; then
  echo "install-hooks.sh: ERROR: a non-arch-gate pre-push hook already exists at" >&2
  echo "                  ${dest}" >&2
  echo "                  Merge our hook manually, or move yours aside, then re-run." >&2
  echo "                  (Our hook lives at: ${HOOK_SRC})" >&2
  exit 1
fi

# Idempotent re-install: drop any prior copy/symlink of ours first.
rm -f "${dest}"

if [ "${copy}" = true ]; then
  cp "${HOOK_SRC}" "${dest}"
  chmod +x "${dest}"
  echo "install-hooks.sh: copied pre-push → ${dest}"
else
  # Prefer a relative symlink when the hook lives under the same repo, so the
  # link survives the repo being moved; fall back to absolute otherwise.
  if command -v python3 >/dev/null 2>&1; then
    rel="$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${HOOK_SRC}" "${hooks_dir}" 2>/dev/null || true)"
  fi
  if [ -n "${rel:-}" ] && ln -s "${rel}" "${dest}" 2>/dev/null; then
    echo "install-hooks.sh: symlinked pre-push → ${dest}  (→ ${rel})"
  else
    ln -sf "${HOOK_SRC}" "${dest}"
    echo "install-hooks.sh: symlinked pre-push → ${dest}  (→ ${HOOK_SRC})"
  fi
fi

# Ensure the source is executable (symlink defers to it).
chmod +x "${HOOK_SRC}" 2>/dev/null || true

echo "install-hooks.sh: done. This hook is ADVISORY (bypass with 'git push --no-verify')."
echo "install-hooks.sh: the REQUIRED CI status check remains the real editability gate."
