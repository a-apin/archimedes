#!/bin/bash
# setup-env.sh — copies .env from main worktree if missing
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/.env"

if [ -f "$TARGET" ]; then
  echo "✅ .env already exists"
  exit 0
fi

# Resolve the main worktree from this checkout's own git metadata rather than a
# hardcoded per-developer path: every worktree of a repo shares one "common" .git
# directory, and that common .git lives directly inside the main worktree's root
# (git-worktree(1) § FILES). This makes the script portable across developers,
# clone locations, and repo renames instead of assuming a specific $HOME layout.
GIT_COMMON_DIR="$(cd "$SCRIPT_DIR" && git rev-parse --git-common-dir 2>/dev/null || true)"
if [ -n "$GIT_COMMON_DIR" ]; then
  MAIN_WORKTREE="$(cd "$SCRIPT_DIR" && cd "$(dirname "$GIT_COMMON_DIR")" && pwd)"
  MAIN="$MAIN_WORKTREE/.env"
else
  MAIN=""
fi

if [ -n "$MAIN" ] && [ -f "$MAIN" ]; then
  cp "$MAIN" "$TARGET"
  echo "✅ Copied .env from main worktree ($MAIN)"
else
  echo "❌ No .env found in main worktree${MAIN:+ ($MAIN)}"
  echo "   Create one with: CIRCLE_API_KEY=... CIRCLE_ENTITY_SECRET=... WALLET_ID=... WALLET_ADDRESS=..."
  exit 1
fi
