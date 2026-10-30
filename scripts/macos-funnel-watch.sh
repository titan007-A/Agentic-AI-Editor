#!/usr/bin/env bash
# Keeps the editor's public links answering from the internet. Run every 5 minutes by
# launchd once installed.
# - Tailscale Funnel: after the Mac's network changes, Funnel can stay on while Tailscale's
#   public relays no longer reach it: TLS handshakes end at once (ERR_SSL_PROTOCOL_ERROR on
#   a phone). Turning Funnel off and on again registers it afresh.
# - Cloudflare Tunnel (scripts/macos-tunnel.sh): after a network drop cloudflared can stop
#   retrying and stay up with no connection, so the domain shows Cloudflare's error 530
#   while the editor runs fine. Restarting it connects it again within seconds.
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
TUNNEL=com.agentic-editor.tunnel
TUNNEL_CONFIG="$HOME/.cloudflared/config.yml"

reachable() {
    # Over the public internet, as a visitor comes: the tailnet would take a shortcut.
    curl -s -m 20 --resolve "$1:443:$2" -o /dev/null "https://$1/"
}

check() {
    check_funnel
    check_tunnel
}

answer() {
    # The HTTP status a visitor gets, 000 when nothing answers.
    curl -s -m 20 -o /dev/null -w '%{http_code}' "$1" || true
}

check_tunnel() {
    launchctl print "$DOMAIN/$TUNNEL" >/dev/null 2>&1 || return 0  # not in use
    local host code
    host="$(sed -n 's/^[[:space:]-]*hostname:[[:space:]]*//p' "$TUNNEL_CONFIG" 2>/dev/null | head -1)"
    [ -n "$host" ] || return 0
    # With the editor itself down, restarting the tunnel can't help.
    [ "$(answer "http://127.0.0.1:$PORT/login/")" = 200 ] || return 0
    case "$(answer "https://$host/login/")" in
        502 | 52? | 530 | 000) ;;
        *) return 0 ;;
    esac
    sleep 30
    code="$(answer "https://$host/login/")"
    case "$code" in
        502 | 52? | 530 | 000) ;;
        *) return 0 ;;
    esac
    echo "$(date '+%F %T') https://$host answers $code while the editor runs; restarting the tunnel"
    launchctl kickstart -k "$DOMAIN/$TUNNEL"
}

check_funnel() {
    # Read whole first: with pipefail, grep -q quitting early fails the pipe (SIGPIPE).
    local status
    status="$("$TS" funnel status 2>/dev/null)"
    grep -q "Funnel on" <<<"$status" || return 0  # not in use
    local host ips ip down=()
    host="$("$TS" status --self --json | /usr/bin/python3 -c \
        'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    # Every address the name has: a visitor's browser may be given any of them, and after a
    # network change one relay can stay stuck while the other works.
    ips="$(curl -s -m 10 -H 'accept: application/dns-json' \
        "https://cloudflare-dns.com/dns-query?name=$host&type=A" | /usr/bin/python3 -c \
        'import json, sys; print(" ".join(x["data"] for x in json.load(sys.stdin).get("Answer", []) if x["type"] == 1))')"
    [ -n "$ips" ] || return 0  # offline, or DNS is down: nothing Funnel can fix
    for ip in $ips; do
        reachable "$host" "$ip" || down+=("$ip")
    done
    [ ${#down[@]} -eq 0 ] && return 0
    sleep 30
    local still=()
    for ip in "${down[@]}"; do
        reachable "$host" "$ip" || still+=("$ip")
    done
    [ ${#still[@]} -eq 0 ] && return 0
    echo "$(date '+%F %T') $host does not answer from the internet via ${still[*]}; restarting Funnel"
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
