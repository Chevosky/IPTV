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
git restore --source=HEAD --staged --worktree -- .
git switch --detach origin/main

# The command recollects upstream inputs and validates outputs from this checkout.
exec "$@"
