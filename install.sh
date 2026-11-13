#!/usr/bin/env bash
# Install qa-digest as an agent skill, check its dependencies, and explain how
# to use it.
#
#   ./install.sh            install for Claude Code (~/.claude/skills/qa-digest)
#   ./install.sh --codex    install for Codex       (~/.codex/skills/qa-digest)
#   ./install.sh --dest DIR install somewhere else (e.g. <project>/.claude/skills/qa-digest)
#
# Safe to rerun: it replaces the installed copy with this checkout.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.claude/skills/qa-digest"
AGENT="Claude Code"

while [ $# -gt 0 ]; do
  case "$1" in
    --codex) DEST="$HOME/.codex/skills/qa-digest"; AGENT="Codex"; shift ;;
    --dest)  DEST="${2:?--dest needs a directory}"; AGENT="agent"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$(cd "$(dirname "$DEST")" 2>/dev/null && pwd)/$(basename "$DEST")" = "$SRC" ]; then
  echo "Already running from $DEST — nothing to copy."
else
  echo "Installing qa-digest for $AGENT -> $DEST"
  mkdir -p "$DEST/scripts"
  # Only what the skill needs at runtime: no .git, tests, venvs, or caches.
  cp "$SRC/SKILL.md" "$SRC/LICENSE" "$SRC/requirements.txt" "$DEST/"
  cp "$SRC/scripts/qa_digest.py" "$SRC/scripts/evidence_queue.py" \
     "$SRC/scripts/file_issues.py" "$DEST/scripts/"
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo
  echo "python3 not found. Install Python 3.9+ and rerun ./install.sh." >&2
  exit 1
fi

echo
echo "Checking dependencies..."
echo
if "$PY" "$DEST/scripts/qa_digest.py" --check; then
  echo
  echo "Installed. Open a new $AGENT session to pick up the skill."
else
  cat <<'EOF'

Almost there — install the missing pieces, then rerun ./install.sh:

  brew install ffmpeg                                   # required
  python3 -m pip install Pillow numpy                   # required
  python3 -m pip install faster-whisper scenedetect     # transcript + scene fallback

EOF
  "$PY" "$DEST/scripts/qa_digest.py" --usage
  exit 1
fi
