#!/usr/bin/env bash
# Deploy the ESB Smart Meter integration into a Home Assistant config directory.
#
#   ./deploy.sh             deploy (backup + mirror) to $HA_CONFIG (default /Volumes/config)
#   ./deploy.sh --dry-run   show what would change, touch nothing
#
# The target is made an exact mirror of custom_components/esb_smart_meter
# (files removed from the repo are removed from HA). A timestamped backup of
# the previous install is kept in $HA_CONFIG/esb_smart_meter_backups/.
set -euo pipefail

HA_CONFIG="${HA_CONFIG:-/Volumes/config}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)/custom_components/esb_smart_meter"
TARGET_DIR="$HA_CONFIG/custom_components/esb_smart_meter"
BACKUP_ROOT="$HA_CONFIG/esb_smart_meter_backups"
KEEP_BACKUPS=5
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

fail() { echo "❌ $*" >&2; exit 1; }

[[ -f "$SOURCE_DIR/manifest.json" ]] || fail "Source $SOURCE_DIR not found (run from the repo)."
[[ -f "$HA_CONFIG/configuration.yaml" && -f "$HA_CONFIG/.HA_VERSION" ]] \
  || fail "$HA_CONFIG is not a Home Assistant config directory (is the share mounted?)."
command -v rsync >/dev/null || fail "rsync is required."

if [[ -z "${DEPLOY_ALLOW_DIRTY:-}" ]] && ! git -C "$SOURCE_DIR" diff --quiet HEAD -- . 2>/dev/null; then
  fail "Uncommitted changes in custom_components/esb_smart_meter. Commit them or set DEPLOY_ALLOW_DIRTY=1."
fi

version() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$1"; }
SRC_VERSION="$(version "$SOURCE_DIR/manifest.json")"
OLD_VERSION="none"
[[ -f "$TARGET_DIR/manifest.json" ]] && OLD_VERSION="$(version "$TARGET_DIR/manifest.json")"
echo "🚀 ESB Smart Meter $OLD_VERSION → $SRC_VERSION  (HA $(cat "$HA_CONFIG/.HA_VERSION"), $HA_CONFIG)"

RSYNC_ARGS=(-a --delete --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store')
if (( DRY_RUN )); then
  rsync "${RSYNC_ARGS[@]}" --dry-run --itemize-changes "$SOURCE_DIR/" "$TARGET_DIR/"
  echo "ℹ️  Dry run: nothing changed."
  exit 0
fi

if [[ -d "$TARGET_DIR" ]]; then
  BACKUP="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)-$OLD_VERSION"
  mkdir -p "$BACKUP_ROOT"
  rsync -a --exclude '__pycache__/' "$TARGET_DIR/" "$BACKUP/"
  echo "💾 Backup: $BACKUP"
  # Keep only the newest $KEEP_BACKUPS backups (portable: works with BSD and GNU tools)
  ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort \
    | awk -v keep="$KEEP_BACKUPS" '{a[NR]=$0} END{for(i=1;i<=NR-keep;i++) print a[i]}' \
    | while IFS= read -r old; do rm -rf "$old"; done
fi

mkdir -p "$TARGET_DIR"
rsync "${RSYNC_ARGS[@]}" "$SOURCE_DIR/" "$TARGET_DIR/"
# Drop stale bytecode so HA recompiles from the new sources
find "$TARGET_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

[[ "$(version "$TARGET_DIR/manifest.json")" == "$SRC_VERSION" ]] || fail "Version check failed after copy."
echo "✅ Deployed $SRC_VERSION. Restart Home Assistant to load it (Settings → System → Restart)."
if [[ -n "${BACKUP:-}" ]]; then
  echo "↩️  Rollback: rsync -a --delete \"$BACKUP/\" \"$TARGET_DIR/\" && restart HA"
fi
