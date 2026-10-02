#!/bin/bash
set -eu

VAULT_PATH="${VAULT_PATH:-/data/vault}"
STATE_DIR="${DRIVE_SYNC_STATE_DIR:-/data/drive-sync-state}"
RCLONE_CONFIG_PATH="$STATE_DIR/rclone.conf"
RESYNC_MARKER="$STATE_DIR/.resync_done"
FILTERS_FILE="$STATE_DIR/filters.txt"
# VaultWatcher (app chatbot) chi bat dau theo doi khi file nay ton tai - xem
# VAULT_READY_MARKER. Khong co no, watcher co the chot "baseline" luc vault moi keo
# ve duoc 1 nua, roi coi toan bo file raw/ den sau la "nguon moi" va chay Ingest
# Agent hang loat (ton phi that + ghi de Supabase).
READY_MARKER="$VAULT_PATH/.drive-sync-ready"
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

# Server chi can NOI DUNG vault. Cau hinh Obsidian cua tung may (.obsidian/ - rieng
# workspace.json doi lien tuc moi lan mo/dong tab), git, va file tam cua Drive for
# Desktop khong duoc dong bo: vua vo ich vua de sinh xung dot. Doi noi dung file
# nay thi bisync bat buoc --resync lai (xoa $RESYNC_MARKER roi khoi dong lai).
cat > "$FILTERS_FILE" <<'EOF'
- .obsidian/**
- .git
- .git/**
- .trash/**
- .tmp.driveupload/**
- .tmp.drivedownload/**
- desktop.ini
- Thumbs.db
- .DS_Store
- ~$*
- .drive-sync-ready
EOF

REMOTE_PATH="${DRIVE_REMOTE_NAME}:${DRIVE_FOLDER_PATH}"

run_bisync() {
    rclone bisync "$REMOTE_PATH" "$VAULT_PATH" \
        --workdir "$STATE_DIR/bisync-workdir" \
        --filters-file "$FILTERS_FILE" \
        --drive-skip-gdocs \
        --create-empty-src-dirs \
        --resilient \
        --conflict-resolve newer \
        "$@"
}

if [ ! -f "$RESYNC_MARKER" ]; then
    rm -f "$READY_MARKER"
    # Chan cau hinh sai DRIVE_FOLDER_PATH (tro nham thu muc rong/khac) truoc khi
    # dung vao du lieu: thu muc vault that luon co CLAUDE.md o goc.
    if ! rclone lsf "$REMOTE_PATH/CLAUDE.md" >/dev/null 2>&1; then
        echo "LOI: khong thay CLAUDE.md trong $REMOTE_PATH - kiem tra lai DRIVE_FOLDER_PATH/tai khoan Drive" >&2
        exit 1
    fi
    # Drive la nguon su that. --resync cua bisync GOP ca 2 phia (file chi co o
    # server se bi day nguoc len Drive), nen phai keo 1 chieu truoc de volume
    # server (co the con du lieu cu tu cac lan thu nghiem truoc) khop han voi
    # Drive roi moi chot baseline.
    echo "[drive-sync] Lan dau chay - keo 1 chieu $REMOTE_PATH -> $VAULT_PATH (Drive la nguon su that)"
    rclone sync "$REMOTE_PATH" "$VAULT_PATH" \
        --filter-from "$FILTERS_FILE" --drive-skip-gdocs --create-empty-src-dirs
    echo "[drive-sync] Thiet lap baseline (--resync) giua $REMOTE_PATH va $VAULT_PATH"
    run_bisync --resync
    touch "$RESYNC_MARKER"
else
    echo "[drive-sync] Da co baseline tu truoc, bo qua --resync"
fi
touch "$READY_MARKER"

echo "[drive-sync] Da san sang, dong bo 2 chieu moi ${SYNC_INTERVAL_SECONDS}s."
while true; do
    if ! run_bisync; then
        echo "[drive-sync] bisync loi 1 lan - thu lai o vong lap ke tiep (khong xoa marker, tranh resync lai toan bo)" >&2
    fi
    sleep "$SYNC_INTERVAL_SECONDS"
done
