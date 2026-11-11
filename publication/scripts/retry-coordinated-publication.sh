#!/usr/bin/env bash
set -euo pipefail

# This runs only in an Actions checkout. Retry once, and only on concurrent main movement.
if "$@"; then
    exit 0
else
    status=$?
fi
if [[ "$status" != 75 ]]; then
    exit "$status"
fi

git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
exact_state_dir="$(mktemp -d)"
trap 'rm -rf "$exact_state_dir"' EXIT
exact_state=(
  guide-iptvorg-exact.xml
  iptvorg-selected.channels.xml
  iptvorg-missing-selected.channels.xml
  iptvorg-epg-coverage.json
)
for file in "${exact_state[@]}"; do
    if [[ -f "$file" ]]; then
        cp "$file" "$exact_state_dir/$file"
    fi
done
git restore --source=HEAD --staged --worktree -- .
git switch --detach origin/main
for file in "${exact_state[@]}"; do
    if [[ -f "$exact_state_dir/$file" ]]; then
        cp "$exact_state_dir/$file" "$file"
    fi
done

# The command recollects upstream inputs and validates outputs from this checkout.
exec "$@"
