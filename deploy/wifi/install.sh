#!/usr/bin/env bash
# Set up the permanent Wi-Fi AP (wlan0 → SSID "PiCar") and provisioning support.
# Run on the Pi from the project directory:  sudo ./deploy/wifi/install.sh
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo ./deploy/wifi/install.sh" >&2; exit 1; }

# --- scripts, hooks and dnsmasq config ---
install -m 755 picar-wifi-select /usr/local/sbin/picar-wifi-select
install -m 755 picar-wifi-ctl    /usr/local/sbin/picar-wifi-ctl
install -m 755 90-picar-wifi     /etc/NetworkManager/dispatcher.d/90-picar-wifi
install -m 644 picar-wifi.service picar-wifi.timer /etc/systemd/system/
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
install -m 644 picar-wifi.conf   /etc/NetworkManager/dnsmasq-shared.d/picar-wifi.conf

# --- sudoers: let the picar service user call picar-wifi-ctl as root ---
SERVICE_USER=$(grep '^User=' /etc/systemd/system/picar.service 2>/dev/null | cut -d= -f2 || true)
if [ -z "${SERVICE_USER:-}" ]; then
    echo "picar.service not found; run deploy/install-service.sh first." >&2
    exit 1
fi
cat > /etc/sudoers.d/picar-wifi <<EOF
# Allow the PiCar server to manage wlan1 without a password
$SERVICE_USER ALL=(root) NOPASSWD: /usr/local/sbin/picar-wifi-ctl
EOF
chmod 440 /etc/sudoers.d/picar-wifi
visudo -c -f /etc/sudoers.d/picar-wifi

# --- settings password (generated once; kept on reinstall) ---
mkdir -p /etc/picar
if [ ! -f /etc/picar/env ]; then
    PW=$(set +o pipefail; tr -dc 'a-z0-9' </dev/urandom | head -c 8)
    printf 'PICAR_WIFI_PASSWORD=%s\n' "$PW" > /etc/picar/env
    chmod 640 /etc/picar/env
    chown root:"$SERVICE_USER" /etc/picar/env
    printf '\nWi-Fi settings password: %s\n(saved to /etc/picar/env — note it down)\n\n' "$PW"
else
    echo "Keeping existing /etc/picar/env"
fi

# --- AP profile on wlan0 (idempotent) ---
if nmcli con show picar-ap >/dev/null 2>&1; then
    echo "AP profile 'picar-ap' already exists; leaving it unchanged."
else
    nmcli con add type wifi ifname wlan0 con-name picar-ap \
        ssid PiCar mode ap \
        802-11-wireless.band bg 802-11-wireless.channel 1 \
        ipv4.method shared ipv4.addresses 192.168.4.1/24 \
        ipv6.method disabled \
        connection.autoconnect yes connection.autoconnect-priority 10
    echo "Created AP: SSID 'PiCar', IP 192.168.4.1 (open network)"
fi

# Reload the AP so dnsmasq picks up the new conf file.
nmcli con down picar-ap 2>/dev/null || true
nmcli con up picar-ap && echo "AP is up."

# Apply the port 80 → 8000 redirect via nftables (always rebuild to avoid duplicates from the
# dispatcher racing during the AP cycle above; the dispatcher hook keeps it on future reboots).
nft delete table ip picar-nat 2>/dev/null || true
nft add table ip picar-nat
nft add chain ip picar-nat prerouting \
    '{ type nat hook prerouting priority -100; policy accept; }'
nft add rule ip picar-nat prerouting iifname "wlan0" tcp dport 80 redirect to :8000
echo "Port 80 → 8000 redirect active."

# --- systemd ---
systemctl daemon-reload
systemctl enable --now picar-wifi.timer
systemctl restart picar 2>/dev/null && echo "picar service restarted." || true

echo ""
echo "Done. Connect a phone to 'PiCar' (open network)."
echo "A sign-in prompt appears automatically; it opens the provisioning page."
echo "Direct URLs if the prompt does not appear:"
echo "  http://picar.local:8000/wifi.html"
echo "  http://192.168.4.1:8000/wifi.html"
