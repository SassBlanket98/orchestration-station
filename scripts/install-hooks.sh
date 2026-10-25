#!/usr/bin/env bash
# Install the repo's git hooks into .git/hooks (also reached when a global core.hooksPath forwards there).
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-common-dir)/hooks"
mkdir -p "$hooks"
for h in "$root"/scripts/hooks/*; do
  ln -sf "$h" "$hooks/$(basename "$h")"
  echo "installed $(basename "$h")"
done
