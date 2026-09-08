# Maintainer checks

Use these commands to review schema, validator, or large registry changes.
For contribution instructions and record formats, see the [README](../README.md).

## Registry review

Routine updates may change up to `max(100, ceil(existing game and mod records * 0.10))`
records without the mass-change override. For larger changes, a maintainer must apply the
`registry-mass-change-approved` label to the pull request before CI will accept it.

The command checks proposed data with the validator from the protected base
branch. Set upstream_remote to the canonical repository, base_branch to the
protected target branch, and proposed_ref to the change under review. Leave
allow_mass_change empty unless the pull request has approval for a mass change.

<!-- local-history-validation: -->
```sh
upstream_remote=upstream
base_branch=main
proposed_ref=HEAD
allow_mass_change=
# Set only after protected approval: allow_mass_change=--allow-mass-change

sh -s -- "$upstream_remote" "$base_branch" "$proposed_ref" "${allow_mass_change:-}" <<'SH'
set -eu
upstream_remote=$1
base_branch=$2
proposed_ref=$3
allow_mass_change=${4:-}
case "$allow_mass_change" in
  '' | --allow-mass-change) ;;
  *) printf '%s\n' 'usage: fourth argument must be --allow-mass-change or empty' >&2; exit 2 ;;
esac

repo_root=$(git rev-parse --show-toplevel) || exit 2
if ! git -C "$repo_root" remote get-url "$upstream_remote" >/dev/null; then
  printf '%s\n' "configure $upstream_remote for the protected target repository first" >&2
  exit 2
fi
tmp_parent=${TMPDIR:-/tmp}
case "$tmp_parent" in
  /*) ;;
  *) printf '%s\n' 'TMPDIR must be an absolute path' >&2; exit 2 ;;
esac
common_dir=$(git -C "$repo_root" rev-parse --git-common-dir)
case "$common_dir" in
  /*) ;;
  *) common_dir="$repo_root/$common_dir" ;;
esac
repo_key=$(printf '%s' "$common_dir" | cksum | awk '{print $1}')
check_root="$tmp_parent/blindgaming-registry-check-$repo_key"
case "$check_root" in
  "$tmp_parent"/blindgaming-registry-check-[0-9]*) ;;
  *) printf '%s\n' 'refusing an unexpected check workspace path' >&2; exit 2 ;;
esac
base_validator="$check_root/base-validator"
base_data="$check_root/base-data"
proposed_data="$check_root/proposed-data"

remove_registered_worktrees() {
  for worktree in "$proposed_data" "$base_data" "$base_validator"; do
    if [ -e "$worktree/.git" ]; then
      git -C "$repo_root" worktree remove --force "$worktree"
    fi
  done
  git -C "$repo_root" worktree prune
}

remove_workspace() {
  case "$check_root" in
    "$tmp_parent"/blindgaming-registry-check-[0-9]*) ;;
    *) return 1 ;;
  esac
  if [ -e "$check_root" ]; then
    rm -rf "$check_root"
  fi
}

cleanup() {
  result=$?
  cleanup_failed=0
  trap - EXIT HUP INT TERM
  for worktree in "$proposed_data" "$base_data" "$base_validator"; do
    if [ -e "$worktree/.git" ]; then
      git -C "$repo_root" worktree remove --force "$worktree" || cleanup_failed=1
    fi
  done
  git -C "$repo_root" worktree prune || cleanup_failed=1
  remove_workspace || cleanup_failed=1
  if [ "$result" -eq 0 ] && [ "$cleanup_failed" -ne 0 ]; then result=1; fi
  exit "$result"
}
trap cleanup EXIT HUP INT TERM

remove_registered_worktrees
remove_workspace
git -C "$repo_root" fetch --quiet "$upstream_remote" "$base_branch"
base_ref=$(git -C "$repo_root" rev-parse --verify FETCH_HEAD^{commit})
mkdir -p "$check_root"
git -C "$repo_root" worktree add --detach "$base_validator" "$base_ref"
git -C "$repo_root" worktree add --detach "$base_data" "$base_ref"
git -C "$repo_root" worktree add --detach "$proposed_data" "$proposed_ref"

export UV_PROJECT_ENVIRONMENT="$check_root/uv-environment"
export UV_CACHE_DIR="$check_root/uv-cache"
unset VIRTUAL_ENV
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_ADDOPTS='-p no:cacheprovider'
uv --directory "$base_validator" sync --locked --extra test
uv --directory "$base_validator" run blindgaming-registry validate \
  --root "$proposed_data" --base "$base_data" ${allow_mass_change:+"$allow_mass_change"}
uv --directory "$base_validator" run blindgaming-registry format \
  --root "$proposed_data" --check
SH
```

The stable workspace is `$TMPDIR/blindgaming-registry-check-<per-repository
checksum>`. Each run unregisters only the three named worktrees from that
guarded workspace, prunes stale Git registrations, and removes that exact
workspace before recreating it. The EXIT trap preserves the first fetch, sync,
validate, or format failure status while it runs the same cleanup. Do not place
this workspace inside the registry repository.

The validator emits one screen-reader-friendly line per result:
`LEVEL path JSON-Pointer: message`. Warnings do not fail validation; errors
exit 1. Command-line usage and filesystem failures exit 2. CI never rewrites a
contributor branch.

## Validator development

The `validator-development` check tests proposed validator,
schema, and package changes in a separate workspace:

<!-- validator-development-check: -->
```sh
proposed_ref=HEAD

sh -s -- "$proposed_ref" <<'SH'
set -eu
proposed_ref=$1
repo_root=$(git rev-parse --show-toplevel) || exit 2
tmp_parent=${TMPDIR:-/tmp}
case "$tmp_parent" in
  /*) ;;
  *) printf '%s\n' 'TMPDIR must be an absolute path' >&2; exit 2 ;;
esac
common_dir=$(git -C "$repo_root" rev-parse --git-common-dir)
case "$common_dir" in
  /*) ;;
  *) common_dir="$repo_root/$common_dir" ;;
esac
repo_key=$(printf '%s' "$common_dir" | cksum | awk '{print $1}')
check_root="$tmp_parent/blindgaming-registry-validator-development-$repo_key"
case "$check_root" in
  "$tmp_parent"/blindgaming-registry-validator-development-[0-9]*) ;;
  *) printf '%s\n' 'refusing an unexpected validator-development workspace path' >&2; exit 2 ;;
esac
proposed_validator="$check_root/proposed-validator"

cleanup() {
  result=$?
  cleanup_failed=0
  trap - EXIT HUP INT TERM
  if [ -e "$proposed_validator/.git" ]; then
    git -C "$repo_root" worktree remove --force "$proposed_validator" || cleanup_failed=1
  fi
  git -C "$repo_root" worktree prune || cleanup_failed=1
  if [ -e "$check_root" ]; then rm -rf "$check_root" || cleanup_failed=1; fi
  if [ "$result" -eq 0 ] && [ "$cleanup_failed" -ne 0 ]; then result=1; fi
  exit "$result"
}
trap cleanup EXIT HUP INT TERM

if [ -e "$proposed_validator/.git" ]; then
  git -C "$repo_root" worktree remove --force "$proposed_validator"
fi
git -C "$repo_root" worktree prune
if [ -e "$check_root" ]; then rm -rf "$check_root"; fi
mkdir -p "$check_root"
git -C "$repo_root" worktree add --detach "$proposed_validator" "$proposed_ref"
export UV_PROJECT_ENVIRONMENT="$check_root/uv-environment"
export UV_CACHE_DIR="$check_root/uv-cache"
unset VIRTUAL_ENV
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_ADDOPTS='-p no:cacheprovider'
uv --directory "$proposed_validator" sync --locked --extra test
uv --directory "$proposed_validator" run python -m pytest validator/tests -q
SH
```

Both commands disable pytest caching and bytecode writing, keeping
`.venv`, `.pytest_cache`, and `__pycache__` out of every registry root.

