#!/bin/sh
# Giu /data/vault dong bo voi 1 library Seafile, dung tai khoan "service account"
# rieng (khong phai tai khoan ca nhan cua ai) - can duoc admin cap quyen read+write
# vao dung library nay tu truoc qua web admin cua Seafile.
#
# Bien moi truong bat buoc:
#   SEAFILE_SERVER_URL   - vd https://seafile.xxx.sslip.io (co ca giao thuc, KHONG dau / cuoi)
#   SEAFILE_SYNC_EMAIL    - email tai khoan service account
#   SEAFILE_SYNC_PASSWORD - mat khau tai khoan do
#   SEAFILE_LIBRARY_ID    - repo_id cua library can dong bo (lay tu web Seafile,
#                           vao library do -> xem URL, hoac qua API /api2/repos/)
# Tuy chon:
#   VAULT_PATH        - noi checkout file that (mac dinh /data/vault)
#   SEAF_CONFIG_DIR   - noi luu config seaf-cli, PHAI la volume ben bi mat (mac dinh /data/seaf-sync-config)

set -eu

VAULT_PATH="${VAULT_PATH:-/data/vault}"
SEAF_CONFIG_DIR="${SEAF_CONFIG_DIR:-/data/seaf-sync-config}"
CCNET_DIR="$SEAF_CONFIG_DIR/ccnet"
# seaf-cli init -d nhan THU MUC CHA, no tu tao "seafile-data" ben trong do (da xac
# nhan qua test that: truyen thang duong dan ".../seafile-data" lam -d se loi vi
# thu muc chua ton tai / bi long sai cap).
SEAFILE_DATA_PARENT_DIR="$SEAF_CONFIG_DIR"

for var in SEAFILE_SERVER_URL SEAFILE_SYNC_EMAIL SEAFILE_SYNC_PASSWORD SEAFILE_LIBRARY_ID; do
    eval "val=\${$var:-}"
    if [ -z "$val" ]; then
        echo "LOI: thieu bien moi truong bat buoc $var" >&2
        exit 1
    fi
done

mkdir -p "$VAULT_PATH" "$SEAF_CONFIG_DIR"

if [ ! -f "$CCNET_DIR/seafile.ini" ]; then
    echo "[seaf-sync] Lan dau chay - khoi tao config tai $SEAF_CONFIG_DIR"
    seaf-cli init -d "$SEAFILE_DATA_PARENT_DIR" -c "$CCNET_DIR"
fi

echo "[seaf-sync] Khoi dong seaf-cli daemon..."
seaf-cli start -c "$CCNET_DIR" || true

sleep 2

if seaf-cli list -c "$CCNET_DIR" 2>/dev/null | grep -q "$SEAFILE_LIBRARY_ID"; then
    echo "[seaf-sync] Library $SEAFILE_LIBRARY_ID da duoc cau hinh dong bo tu truoc, khong can sync lai."
else
    echo "[seaf-sync] Dang ghep library $SEAFILE_LIBRARY_ID -> $VAULT_PATH ..."
    seaf-cli sync -c "$CCNET_DIR" \
        -l "$SEAFILE_LIBRARY_ID" \
        -s "$SEAFILE_SERVER_URL" \
        -u "$SEAFILE_SYNC_EMAIL" \
        -p "$SEAFILE_SYNC_PASSWORD" \
        -d "$VAULT_PATH"
fi

echo "[seaf-sync] Da san sang, giu tien trinh song va tu kiem tra dinh ky."
while true; do
    if ! seaf-cli status -c "$CCNET_DIR" >/tmp/seaf-status.log 2>&1; then
        echo "[seaf-sync] seaf-cli daemon khong phan hoi, thu khoi dong lai..."
        seaf-cli start -c "$CCNET_DIR" || true
    fi
    sleep 30
done
