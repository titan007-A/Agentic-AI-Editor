#!/usr/bin/env bash
# Runs the editor's server as a launchd agent: started at login and restarted if it stops.
# The server itself keeps the Mac awake while it is on power, so overnight autopilot runs
# finish.
#   scripts/macos-service.sh install | uninstall | restart | status | logs | plist
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL=com.agentic-editor.server
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/agentic-editor"
DOMAIN="gui/$(id -u)"

plist() {
    # The environment's own aiedit, not uv run, so launchd's stop signal reaches the server
    # and it can finish its shutdown. Interactive keeps macOS from slowing it down as a
    # background process.
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/.venv/bin/aiedit</string>
    <string>serve</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ExitTimeOut</key>
  <integer>45</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardOutPath</key>
  <string>$LOGS/server.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/server.log</string>
</dict>
</plist>
EOF
}

loaded() {
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

case "${1:-}" in
    install)
        [ -x "$REPO/.venv/bin/aiedit" ] || {
            echo "run scripts/setup-macos.sh first"
            exit 1
        }
        mkdir -p "$(dirname "$PLIST")" "$LOGS"
        plist >"$PLIST"
        plutil -lint "$PLIST" >/dev/null
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        launchctl bootstrap "$DOMAIN" "$PLIST"
        echo "installed; the editor starts now and at every login (logs: $LOGS/server.log)"
        ;;
    uninstall)
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        rm -f "$PLIST"
        echo "removed; the editor no longer starts at login"
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
        tail -n 100 -f "$LOGS/server.log"
        ;;
    plist)
        plist
        ;;
    *)
        echo "usage: $0 install | uninstall | restart | status | logs | plist"
        exit 2
        ;;
esac
