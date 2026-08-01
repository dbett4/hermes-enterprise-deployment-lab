#!/usr/bin/env bash
# Record the demo as a terminal session.
#
# asciinema is preferred (small, replayable, embeddable). When it is absent this
# script does NOT pretend to have made a cast: it captures a timestamped plain
# text transcript instead and prints the exact steps to produce a real cast
# later. Nothing here fabricates a recording.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${OUT_DIR:-${ROOT_DIR}/.recordings}"
mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if command -v asciinema >/dev/null 2>&1; then
  CAST="${OUT_DIR}/demo-${STAMP}.cast"
  echo "==> Recording with asciinema -> ${CAST}"
  asciinema rec "$CAST" \
    --title "Hermes enterprise lab: two-phase guard, forced failure, resume" \
    --command "./scripts/demo.sh" \
    --overwrite
  echo
  echo "Recorded: ${CAST}"
  echo "Replay:   asciinema play ${CAST}"
  echo "Publish:  asciinema upload ${CAST}   # requires an explicit decision to publish"
  exit 0
fi

TRANSCRIPT="${OUT_DIR}/demo-${STAMP}.txt"
echo "==> asciinema not installed; capturing a plain text transcript instead"
echo "==> ${TRANSCRIPT}"
{
  echo "# Hermes enterprise deployment lab - demo transcript"
  echo "# captured: ${STAMP}"
  echo "# host python: $(python3 -V 2>&1)"
  echo "# git HEAD: $(git rev-parse HEAD 2>/dev/null || echo 'not a git repo')"
  echo "# NOTE: this is a text capture, not a terminal recording."
  echo
} > "$TRANSCRIPT"

set +e
./scripts/demo.sh 2>&1 | tee -a "$TRANSCRIPT"
STATUS=${PIPESTATUS[0]}
set -e

cat <<EOF

Transcript written: ${TRANSCRIPT}
Demo exit status:   ${STATUS}

To produce a real terminal recording (recording plan):
  1. brew install asciinema          # or: pipx install asciinema
  2. ./scripts/record-demo.sh        # this script auto-detects asciinema
  3. asciinema play .recordings/demo-*.cast     # verify locally first
  4. Publishing the cast is a separate, explicit decision — this script never
     uploads anything.

Expected runtime is a few seconds; the arc is deterministic, so the recording
does not need editing or retakes.
EOF

exit "$STATUS"
