#!/usr/bin/env bash
# Runs a Cloudflare Tunnel to the editor as a launchd agent, so the web app has a public
# address on your own domain. Much quicker from phones than Tailscale Funnel's relays.
# Once, before installing (the domain's nameservers must be Cloudflare's):
#   brew install cloudflared
#   cloudflared tunnel login                        # pick the domain in the browser
#   scripts/macos-tunnel.sh setup NAME HOSTNAME      # e.g. ai-editor editor.example.com
#   scripts/macos-tunnel.sh install | uninstall | restart | status | logs
set -euo pipefail

cd "$(dirname "$0")/.."
LABEL=com.agentic-editor.tunnel
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/agentic-editor"
DOMAIN="gui/$(id -u)"
CONFIG="$HOME/.cloudflared/config.yml"
CLOUDFLARED="$(command -v cloudflared || echo /opt/homebrew/bin/cloudflared)"
PORT="${PORT:-8000}"

plist() {
    # Homebrew updates cloudflared, so it doesn't update itself.
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CLOUDFLARED</string>
    <string>--no-autoupdate</string>
    <string>--config</string>
    <string>$CONFIG</string>
    <string>tunnel</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOGS/tunnel.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/tunnel.log</string>
</dict>
</plist>
EOF
}

loaded() {
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

case "${1:-}" in
    setup)
        name="${2:?usage: $0 setup NAME HOSTNAME}"
        host="${3:?usage: $0 setup NAME HOSTNAME}"
        "$CLOUDFLARED" tunnel info "$name" >/dev/null 2>&1 || "$CLOUDFLARED" tunnel create "$name"
        id="$("$CLOUDFLARED" tunnel list -o json -n "$name" | /usr/bin/python3 -c \
            'import json, sys; print(json.load(sys.stdin)[0]["id"])')"
        cat >"$CONFIG" <<EOF
tunnel: $id
credentials-file: $HOME/.cloudflared/$id.json
ingress:
  - hostname: $host
    service: http://127.0.0.1:$PORT
  - service: http_status:404
EOF
        "$CLOUDFLARED" tunnel ingress validate --config "$CONFIG"
        "$CLOUDFLARED" tunnel route dns "$name" "$host"
        echo "set up; now run: $0 install"
        ;;
    install)
        [ -f "$CONFIG" ] || {
            echo "no $CONFIG yet: run $0 setup NAME HOSTNAME first"
            exit 1
        }
        mkdir -p "$(dirname "$PLIST")" "$LOGS"
        plist >"$PLIST"
        plutil -lint "$PLIST" >/dev/null
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        launchctl bootstrap "$DOMAIN" "$PLIST"
        echo "installed; the tunnel runs now and at every login (logs: $LOGS/tunnel.log)"
        ;;
    uninstall)
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        rm -f "$PLIST"
        echo "removed; the tunnel no longer runs"
        ;;
    restart)
        launchctl kickstart -k "$DOMAIN/$LABEL"
        ;;
    status)
        if loaded; then
            launchctl print "$DOMAIN/$LABEL" | grep -E "^\s+(state|pid|last exit code) ="
        else
            echo "not installed"
        fi
        ;;
    logs)
        tail -n 100 -f "$LOGS/tunnel.log"
        ;;
    *)
        echo "usage: $0 setup NAME HOSTNAME | install | uninstall | restart | status | logs"
        exit 2
        ;;
esac
