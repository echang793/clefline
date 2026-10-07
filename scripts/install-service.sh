#!/usr/bin/env bash
# Install clefline as two launchd agents: the server (always on, restarted after a
# crash) and a weekly cleanup of old jobs and songs.
#
#   scripts/install-service.sh              install (or reinstall) and start both
#   scripts/install-service.sh --uninstall  stop and remove both
#
# Flags used by the tests (and handy for a dry look): --no-load renders the plists
# without touching launchd; --dest/--logs/--repo override where things go.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/clefline"
LABELS=(com.clefline.server com.clefline.cleanup)
LOAD=1
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --no-load)   LOAD=0 ;;
    --uninstall) UNINSTALL=1 ;;
    --dest)      DEST="$2"; shift ;;
    --logs)      LOGS="$2"; shift ;;
    --repo)      REPO="$2"; shift ;;
    -h|--help)   sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

DOMAIN="gui/$(id -u)"

if [ "$UNINSTALL" = 1 ]; then
  for label in "${LABELS[@]}"; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$DEST/$label.plist"
    echo "removed $label"
  done
  exit 0
fi

# An always-on service must not run from an iCloud-synced folder: cold reads of
# evicted files stall imports for minutes, and sync can pull files out from under it.
case "$REPO" in
  *"Mobile Documents"*|"$HOME/Desktop"/*|"$HOME/Documents"/*)
    echo "error: $REPO looks iCloud-synced (Desktop/Documents/Mobile Documents)." >&2
    echo "Move the project somewhere like ~/Projects/clefline and run this again." >&2
    exit 1 ;;
esac

if [ "$LOAD" = 1 ] && [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "error: $REPO/.venv/bin/python not found. Create the venv first (see README)." >&2
  exit 1
fi

mkdir -p "$DEST" "$LOGS"
for label in "${LABELS[@]}"; do
  target="$DEST/$label.plist"
  sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$REPO/deploy/$label.plist" > "$target"
  xattr -c "$target" 2>/dev/null || true
  plutil -lint "$target" >/dev/null
  echo "wrote $target"
done

if [ "$LOAD" = 0 ]; then
  echo "(--no-load: launchd not touched)"
  exit 0
fi

for label in "${LABELS[@]}"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$DEST/$label.plist"
done

echo
launchctl list | grep clefline || { echo "error: neither agent is loaded" >&2; exit 1; }
echo
echo "Server log: $LOGS/server.log   (app log: data/logs/clefline.log)"
