#!/usr/bin/env bash
# The TikTok gateway's tunnel, on the live Mac. TikTok is blocked from India, so the editor
# reaches it through a second Mac on a VPN (set up with scripts/setup-tiktok-gateway.sh,
# nothing else installed there). This keeps an SSH tunnel to that Mac over Tailscale as a
# launchd agent: a SOCKS proxy on 127.0.0.1 that only TikTok's links use (TIKTOK_PROXY in
# .env). Safe to run again. See docs/tiktok-gateway.md.
#   scripts/macos-tiktok-gateway.sh key                # 1. the key, and what to run on the gateway
#   scripts/macos-tiktok-gateway.sh install USER@HOST  # 2. tunnel on, tested, TIKTOK_PROXY set
#   scripts/macos-tiktok-gateway.sh start | stop | restart | status | test | logs | uninstall | plist
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL=com.agentic-editor.tiktok-gateway
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGS="$HOME/Library/Logs/agentic-editor"
DOMAIN="gui/$(id -u)"
KEY="$HOME/.ssh/aiedit_tiktok_gateway"
KNOWN="$HOME/.ssh/aiedit_tiktok_gateway_known_hosts"
PORT="${GATEWAY_PORT:-1080}"
PROXY="socks5h://127.0.0.1:$PORT"
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale
SETUP_URL="https://raw.githubusercontent.com/titan007-A/Agentic-AI-Editor/${BRANCH:-main}/scripts/setup-tiktok-gateway.sh"
CHECK=https://www.tiktok.com/robots.txt

# The tunnel's ssh: its own key and known hosts, no ~/.ssh/config, never a question (it runs
# under launchd), and it ends when the link to the gateway is gone (45 s of no answer) or
# the port is taken, so launchd starts it again.
SSH_OPTIONS=(
    -F /dev/null -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes
    -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$KNOWN"
)

plist() {
    local target="$1" option
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string>
    <string>-N</string>
    <string>-D</string>
    <string>127.0.0.1:$PORT</string>
EOF
    for option in "${SSH_OPTIONS[@]}"; do
        printf '    <string>%s</string>\n' "$option"
    done
    cat <<EOF
    <string>$target</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOGS/tiktok-gateway.log</string>
  <key>StandardErrorPath</key>
  <string>$LOGS/tiktok-gateway.log</string>
</dict>
</plist>
EOF
}

loaded() {
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

listening() {
    nc -z -G 2 127.0.0.1 "$PORT" >/dev/null 2>&1
}

need_mac() {
    if [ "$(uname -s)" != Darwin ]; then
        echo "This part is for the live Mac (macOS)."
        exit 1
    fi
}

# KEY=VALUE in .env, replacing the line that was there; the rest of the file as it was.
set_env() {
    local name="$1" value="$2" kept
    [ -f .env ] || {
        echo "No .env here ($REPO): run scripts/setup-macos.sh first."
        exit 1
    }
    kept="$(grep -v "^$name=" .env || true)"
    { [ -n "$kept" ] && printf '%s\n' "$kept"; printf '%s=%s\n' "$name" "$value"; } >.env.tmp
    cat .env.tmp >.env && rm -f .env.tmp
}

# Whether TikTok answers through the tunnel; prints what it saw.
tiktok_through() {
    local code
    code="$(curl -s -o /dev/null -m 20 -w '%{http_code}' --socks5-hostname "127.0.0.1:$PORT" "$CHECK" 2>/dev/null)"
    if [ -n "$code" ] && [ "$code" != 000 ]; then
        echo "  [ok]  TikTok answers through the tunnel (HTTP $code)."
        return 0
    fi
    echo "  [!!]  TikTok doesn't answer through the tunnel."
    echo "        The tunnel is up, so the VPN on the gateway Mac is most likely off."
    echo "        Turn it on there (and run setup-tiktok-gateway.sh again to check), then: $0 test"
    return 1
}

# The editor's own check, as the System page shows it (what the downloads go through).
app_check() {
    if [ -x "$REPO/.venv/bin/aiedit" ]; then
        echo "The editor's own check:"
        "$REPO/.venv/bin/aiedit" tiktok-gateway --proxy "$PROXY" | sed 's/^/  /' || return 1
    fi
}

test_tunnel() {
    if ! listening; then
        echo "  [!!]  The tunnel isn't up: nothing answers on 127.0.0.1:$PORT."
        echo "        See why: $0 logs   (the gateway Mac may be off, asleep or off Tailscale)"
        return 1
    fi
    echo "  [ok]  The tunnel is up on 127.0.0.1:$PORT."
    tiktok_through && app_check
}

case "${1:-}" in
    key)
        if [ -f "$KEY" ]; then
            echo "The gateway key is already there: $KEY"
        else
            mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
            ssh-keygen -q -t ed25519 -N "" -C aiedit-tiktok-gateway -f "$KEY"
            echo "Made the gateway key: $KEY"
        fi
        from=""
        if [ -x "$TS" ] && here="$("$TS" ip -4 2>/dev/null | head -n 1)" && [ -n "$here" ]; then
            from="--from $here "
        fi
        echo
        echo "On the gateway Mac (the M1 on the VPN), open Terminal and run these two lines:"
        echo
        echo "  curl -fsSLO $SETUP_URL"
        echo "  bash setup-tiktok-gateway.sh $from'$(cat "$KEY.pub")'"
        echo
        echo "At the end it prints a line like 'Gateway: name@100.x.y.z'. Then, here:"
        echo
        echo "  $0 install name@100.x.y.z"
        ;;
    install)
        need_mac
        target="${2:-}"
        [[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.:-]+$ ]] || {
            echo "usage: $0 install USER@HOST   (the 'Gateway:' line the gateway Mac printed)"
            exit 2
        }
        [ -f "$KEY" ] || {
            echo "No gateway key yet: run $0 key first."
            exit 1
        }
        echo "Connecting to the gateway Mac ($target)..."
        # Its key may only forward: asked for a shell, the gateway runs /usr/bin/false (exit
        # 1). 255 is ssh's own failure: no connection, or the key isn't let in.
        status=0
        said="$(ssh "${SSH_OPTIONS[@]}" -T "$target" 2>&1)" || status=$?
        if [ "$status" = 255 ]; then
            echo "  [!!]  Couldn't connect: $said"
            echo "        Check on the gateway Mac: Tailscale on, Remote Login on, and"
            echo "        setup-tiktok-gateway.sh run with this Mac's key ($0 key prints it)."
            exit 1
        fi
        echo "  [ok]  The gateway Mac lets this Mac's key in."
        if listening && ! loaded; then
            echo "  [!!]  Something else already uses port $PORT here. Choose another:"
            echo "        GATEWAY_PORT=1081 $0 install $target"
            exit 1
        fi
        mkdir -p "$(dirname "$PLIST")" "$LOGS"
        plist "$target" >"$PLIST"
        plutil -lint "$PLIST" >/dev/null
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        launchctl bootstrap "$DOMAIN" "$PLIST"
        for _ in $(seq 20); do
            listening && break
            sleep 1
        done
        test_tunnel || echo "(The tunnel stays installed; it keeps trying by itself.)"
        set_env TIKTOK_PROXY "$PROXY"
        echo
        echo "Installed: the tunnel runs now and at every login (logs: $LOGS/tiktok-gateway.log)."
        echo "TIKTOK_PROXY=$PROXY is in .env. Restart the editor so it uses it, when no job is"
        echo "running: scripts/macos-service.sh restart"
        ;;
    start)
        need_mac
        [ -f "$PLIST" ] || {
            echo "Not installed: run $0 install USER@HOST"
            exit 1
        }
        loaded || launchctl bootstrap "$DOMAIN" "$PLIST"
        echo "Started."
        ;;
    stop)
        need_mac
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        echo "Stopped (until '$0 start' or the next login). TikTok links fail meanwhile."
        ;;
    restart)
        need_mac
        launchctl kickstart -k "$DOMAIN/$LABEL"
        ;;
    status)
        need_mac
        if loaded; then
            launchctl print "$DOMAIN/$LABEL" | grep -E "^\s+(state|pid|last exit code) ="
            test_tunnel || true
        else
            echo "not running (installed: $([ -f "$PLIST" ] && echo yes || echo no))"
        fi
        grep "^TIKTOK_PROXY=" .env 2>/dev/null || echo "TIKTOK_PROXY isn't in .env"
        ;;
    test)
        test_tunnel
        ;;
    logs)
        tail -n 100 -f "$LOGS/tiktok-gateway.log"
        ;;
    uninstall)
        need_mac
        if loaded; then launchctl bootout "$DOMAIN/$LABEL"; fi
        rm -f "$PLIST"
        if [ -f .env ] && grep -q "^TIKTOK_PROXY=" .env; then set_env TIKTOK_PROXY ""; fi
        echo "Removed: no tunnel, and TIKTOK_PROXY is empty in .env (restart the editor:"
        echo "scripts/macos-service.sh restart). The key stays in $KEY for a later install."
        ;;
    plist)
        plist "${2:-USER@HOST}"
        ;;
    *)
        echo "usage: $0 key | install USER@HOST | start | stop | restart | status | test | logs | uninstall | plist"
        exit 2
        ;;
esac
