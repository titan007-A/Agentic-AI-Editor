#!/usr/bin/env bash
# Keeps a Tailscale Funnel link to the editor answering from the internet. After the Mac's
# network changes, Funnel can stay on while Tailscale's public relays no longer reach it:
# TLS handshakes end at once (ERR_SSL_PROTOCOL_ERROR on a phone). Turning Funnel off and
# on again registers it afresh. Run every 5 minutes by launchd once installed.
#   scripts/macos-funnel-watch.sh install | uninstall | check
set -uo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL=com.agentic-editor.funnel-watch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/agentic-editor"
DOMAIN="gui/$(id -u)"
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
PORT="${PORT:-8000}"

reachable() {
    # Over the public internet, as a visitor comes: the tailnet would take a shortcut.
    curl -s -m 20 --resolve "$1:443:$2" -o /dev/null "https://$1/"
}

check() {
    "$TS" funnel status 2>/dev/null | grep -q "Funnel on" || return 0  # not in use
    local host ip
    host="$("$TS" status --self --json | /usr/bin/python3 -c \
        'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    ip="$(curl -s -m 10 -H 'accept: application/dns-json' \
        "https://cloudflare-dns.com/dns-query?name=$host&type=A" | /usr/bin/python3 -c \
        'import json, sys; a = [x["data"] for x in json.load(sys.stdin).get("Answer", []) if x["type"] == 1]; print(a[0] if a else "")')"
    [ -n "$ip" ] || return 0  # offline, or DNS is down: nothing Funnel can fix
    reachable "$host" "$ip" && return 0
    sleep 30
    reachable "$host" "$ip" && return 0
    echo "$(date '+%F %T') $host does not answer from the internet; restarting Funnel"
    "$TS" funnel --https=443 off
    "$TS" funnel --bg "$PORT"
}

plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/scripts/macos-funnel-watch.sh</string>
    <string>check</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOGS/funnel-watch.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/funnel-watch.log</string>
</dict>
</plist>
EOF
}

case "${1:-}" in
    install)
        [ -x "$TS" ] || {
            echo "Tailscale is not installed at $TS"
            exit 1
        }
        mkdir -p "$(dirname "$PLIST")" "$LOGS"
        plist >"$PLIST"
        plutil -lint "$PLIST" >/dev/null
        launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
        launchctl bootstrap "$DOMAIN" "$PLIST"
        echo "installed; the Funnel link is checked every 5 minutes (log: $LOGS/funnel-watch.log)"
        ;;
    uninstall)
        launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
        rm -f "$PLIST"
        echo "uninstalled"
        ;;
    check) check ;;
    *)
        echo "usage: $0 install | uninstall | check"
        exit 2
        ;;
esac
