# Sidecar rieng, chi lam 1 viec: giu /data/vault dong bo 2 chieu voi 1 library
# tren Seafile server, bang chinh "seaf-cli" - cong cu dong lenh CHINH THUC cua
# doi Seafile (khong phai tu build lai/doan mo ta dinh dang), da xac nhan cai
# duoc qua apt tren Debian trixie (khong co tren bookworm) bang test that.
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends seafile-cli curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
