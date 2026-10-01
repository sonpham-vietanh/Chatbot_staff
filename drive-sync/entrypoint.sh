#!/bin/bash
set -eu

VAULT_PATH="${VAULT_PATH:-/data/vault}"
STATE_DIR="${DRIVE_SYNC_STATE_DIR:-/data/drive-sync-state}"
RCLONE_CONFIG_PATH="$STATE_DIR/rclone.conf"
RESYNC_MARKER="$STATE_DIR/.resync_done"
SYNC_INTERVAL_SECONDS="${SYNC_INTERVAL_SECONDS:-60}"

for var in DRIVE_RCLONE_CONFIG_B64 DRIVE_REMOTE_NAME DRIVE_FOLDER_PATH; do
    eval "val=\${$var:-}"
    if [ -z "$val" ]; then
        echo "LOI: thieu bien moi truong bat buoc $var" >&2
        exit 1
    fi
done

mkdir -p "$VAULT_PATH" "$STATE_DIR" "$STATE_DIR/bisync-workdir"

if [ ! -f "$RCLONE_CONFIG_PATH" ]; then
    echo "[drive-sync] Lan dau chay - ghi rclone.conf tu DRIVE_RCLONE_CONFIG_B64"
    echo "$DRIVE_RCLONE_CONFIG_B64" | base64 -d > "$RCLONE_CONFIG_PATH"
fi
export RCLONE_CONFIG="$RCLONE_CONFIG_PATH"

REMOTE_PATH="${DRIVE_REMOTE_NAME}:${DRIVE_FOLDER_PATH}"

run_bisync() {
    rclone bisync "$REMOTE_PATH" "$VAULT_PATH" \
        --workdir "$STATE_DIR/bisync-workdir" \
        --create-empty-src-dirs \
        --resilient \
        --conflict-resolve newer \
        "$@"
}

if [ ! -f "$RESYNC_MARKER" ]; then
    echo "[drive-sync] Lan dau chay - thiet lap baseline (--resync) giua $REMOTE_PATH va $VAULT_PATH"
    run_bisync --resync
    touch "$RESYNC_MARKER"
else
    echo "[drive-sync] Da co baseline tu truoc, bo qua --resync"
fi

echo "[drive-sync] Da san sang, dong bo 2 chieu moi ${SYNC_INTERVAL_SECONDS}s."
while true; do
    if ! run_bisync; then
        echo "[drive-sync] bisync loi 1 lan - thu lai o vong lap ke tiep (khong xoa marker, tranh resync lai toan bo)" >&2
    fi
    sleep "$SYNC_INTERVAL_SECONDS"
done
