#!/usr/bin/env bash
# The TikTok gateway: run on the second Mac, the one on a VPN, so the editor's live Mac can
# reach TikTok through it (TikTok is blocked from India). Nothing of the editor is installed
# here: the live Mac keeps an SSH tunnel to this Mac over Tailscale
# (scripts/macos-tiktok-gateway.sh), and its key is let in for that tunnel only. It checks
# everything the gateway needs and says what to fix. Safe to run again.
# See docs/tiktok-gateway.md.
#   bash setup-tiktok-gateway.sh --from LIVE_MAC_IP 'ssh-ed25519 AAAA... aiedit-tiktok-gateway'
#   bash setup-tiktok-gateway.sh                  # only the checks
set -uo pipefail

TS_APP=/Applications/Tailscale.app/Contents/MacOS/Tailscale
KEYS="$HOME/.ssh/authorized_keys"
NAME=aiedit-tiktok-gateway
# Tailscale's addresses: the key works only from inside the tailnet.
TAILNET="100.64.0.0/10,fd7a:115c:a1e0::/48"
PROBLEMS=0

ok() { printf '  [ok]  %s\n' "$*"; }
note() { printf '        %s\n' "$*"; }
bad() {
    printf '  [!!]  %s\n' "$*"
    PROBLEMS=$((PROBLEMS + 1))
}
step() { printf '\n%s\n' "$*"; }

usage() {
    echo "usage: bash $0 [--from LIVE_MAC_TAILSCALE_IP] ['ssh-ed25519 AAAA... $NAME']"
    exit 2
}

# The authorized_keys line for the live Mac's key: SSH forwarding to web ports only (what
# the SOCKS tunnel needs), never a shell, a command or any other forwarding, and only from
# `from` (the live Mac, or any Tailscale address).
key_entry() {
    local key="$1" from="$2" kind body
    read -r kind body _ <<<"$key"
    printf 'restrict,port-forwarding,permitopen="*:80",permitopen="*:443",'
    printf 'command="/usr/bin/false",from="%s" %s %s %s\n' "$from" "$kind" "$body" "$NAME"
}

valid_key() {
    [[ "$1" =~ ^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa)\ [A-Za-z0-9+/]+=*(\ .*)?$ ]]
}

check_mac() {
    step "1. This Mac"
    if [ "$(uname -s)" != Darwin ]; then
        bad "This is not a Mac. Run this script on the gateway Mac (the M1 on the VPN)."
        exit 1
    fi
    if [ "$(uname -m)" = arm64 ]; then
        ok "macOS $(sw_vers -productVersion) on Apple Silicon."
    else
        ok "macOS $(sw_vers -productVersion) (an Intel Mac: that works as a gateway too)."
    fi
}

tailscale_cli() {
    if [ -x "$TS_APP" ]; then
        echo "$TS_APP"
    elif command -v tailscale >/dev/null; then
        command -v tailscale
    fi
}

check_tailscale() {
    step "2. Tailscale"
    TS="$(tailscale_cli)"
    if [ -z "$TS" ]; then
        bad "Tailscale is not installed."
        note "Install it from the App Store (or tailscale.com/download), sign in with the"
        note "same account as the live Mac, then run this script again."
        exit 1
    fi
    local state
    state="$("$TS" status --json 2>/dev/null | grep -o '"BackendState": *"[A-Za-z]*"' | grep -o '[A-Za-z]*"$' | tr -d '"')"
    if [ "$state" != Running ]; then
        bad "Tailscale is installed but not connected (it says: ${state:-nothing})."
        note "Open Tailscale from the menu bar, sign in with the same account as the live Mac,"
        note "and switch it on. Then run this script again."
        exit 1
    fi
    TS_IP="$("$TS" ip -4 2>/dev/null | head -n 1)"
    if [ -z "$TS_IP" ]; then
        bad "Tailscale is on but this Mac has no Tailscale address yet. Wait a minute, run again."
        exit 1
    fi
    ok "Tailscale is on. This Mac's Tailscale address: $TS_IP"
}

remote_login_on() {
    nc -z -G 3 127.0.0.1 22 >/dev/null 2>&1
}

check_remote_login() {
    step "3. Remote Login (SSH), so the live Mac can connect"
    if remote_login_on; then
        ok "Remote Login is already on."
    else
        note "Turning Remote Login on (your Mac password may be asked)..."
        # systemsetup wants Full Disk Access on newer macOS, and may say nothing when it
        # fails for want of it: launchd is asked next.
        sudo systemsetup -setremotelogin on >/dev/null 2>&1
        sleep 2
        if ! remote_login_on; then
            sudo launchctl enable system/com.openssh.sshd >/dev/null 2>&1
            sudo launchctl bootstrap system /System/Library/LaunchDaemons/ssh.plist >/dev/null 2>&1 ||
                sudo launchctl load -w /System/Library/LaunchDaemons/ssh.plist >/dev/null 2>&1
            sleep 2
        fi
        if remote_login_on; then
            ok "Remote Login is on now."
        else
            bad "Remote Login could not be turned on from here."
            note "Turn it on by hand: System Settings > General > Sharing > Remote Login: on."
            note "Then run this script again."
        fi
    fi
    # "Allow access for: Only these users" keeps everyone else out.
    if dscl . -read /Groups/com.apple.access_ssh >/dev/null 2>&1 &&
        ! dseditgroup -o checkmember -m "$USER" com.apple.access_ssh >/dev/null 2>&1; then
        bad "Remote Login lets in only some users, and not you ($USER)."
        note "System Settings > General > Sharing > Remote Login (i): add $USER, or choose All users."
    fi
    if /usr/libexec/ApplicationFirewall/socketfilterfw --getblockall 2>/dev/null | grep -qi enabled; then
        bad "The firewall blocks all incoming connections, so the live Mac can't connect."
        note "System Settings > Network > Firewall > Options: turn off 'Block all incoming connections'."
    fi
}

check_sleep() {
    step "4. No sleeping (the gateway must stay on)"
    if pmset -g custom | awk '$1 == "sleep" && $2 != "0" { found = 1 } END { exit !found }'; then
        note "Stopping sleep (your Mac password may be asked)..."
        if sudo pmset -a sleep 0 && sudo pmset -a disksleep 0; then
            ok "This Mac doesn't sleep now (the screen may still turn off: that's fine)."
        else
            bad "Couldn't stop sleep. System Settings > Energy (or Battery > Options): never sleep."
        fi
    else
        ok "This Mac already never sleeps."
    fi
    # Wake for network access, and start again by itself after a power cut where it can.
    sudo pmset -a womp 1 >/dev/null 2>&1 || true
    sudo pmset -a autorestart 1 >/dev/null 2>&1 || true
    if pmset -g batt 2>/dev/null | grep -q InternalBattery; then
        note "This is a MacBook: keep it on the charger, with the lid open."
        note "(A closed lid makes a MacBook sleep anyway.)"
    fi
}

add_key() {
    step "5. The live Mac's key"
    if [ -z "$KEY" ]; then
        note "No key given: skipped. On the live Mac, 'scripts/macos-tiktok-gateway.sh key'"
        note "prints the full line to run here, with the key."
        return
    fi
    if ! valid_key "$KEY"; then
        bad "That doesn't look like an SSH key (it should start with ssh-ed25519)."
        note "Copy the whole line the live Mac printed, inside the quotes."
        return
    fi
    local body entry before=""
    body="$(awk '{ print $2 }' <<<"$KEY")"
    entry="$(key_entry "$KEY" "${FROM:-$TAILNET}")"
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    touch "$KEYS" && chmod 600 "$KEYS"
    if grep -qF -- "$body" "$KEYS"; then
        before=yes
    fi
    if grep -qxF -- "$entry" "$KEYS"; then
        ok "The key is already let in, for the tunnel only."
        return
    fi
    # The key once, with the restrictions as they are now.
    local kept
    kept="$(grep -vF -- "$body" "$KEYS" || true)"
    { [ -n "$kept" ] && printf '%s\n' "$kept"; printf '%s\n' "$entry"; } >"$KEYS"
    if [ -n "$before" ]; then
        ok "The key was already there; its restrictions are updated."
    else
        ok "The key is let in, for the tunnel only (no shell, only web ports)."
    fi
    if [ -n "$FROM" ]; then
        note "It works only from the live Mac ($FROM)."
    else
        note "It works only from Tailscale addresses."
    fi
}

check_tiktok() {
    step "6. TikTok from this Mac (the VPN must be on)"
    local code where
    where="$(curl -s -m 10 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | awk -F= '$1 == "loc" { print $2 }')"
    if [ -n "$where" ]; then
        note "The internet sees this Mac in: $where"
    fi
    code="$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://www.tiktok.com/robots.txt 2>/dev/null)"
    if [ -n "$code" ] && [ "$code" != 000 ]; then
        ok "TikTok answers (HTTP $code)."
    elif [ "$where" = IN ]; then
        bad "TikTok doesn't answer, and the internet sees this Mac in India: the VPN is off."
        note "Turn the VPN on (a country where TikTok works), then run this script again."
    else
        bad "TikTok doesn't answer. Is the VPN on? Try another VPN country, then run again."
    fi
}

check_tailscale_with_vpn() {
    step "7. Tailscale with the VPN on"
    local tsif routeif probe="${FROM:-100.100.100.100}"
    tsif="$(ifconfig | awk -v ip="$TS_IP" '/^[a-z]/ { name = $1 } $1 == "inet" && $2 == ip { sub(":", "", name); print name }')"
    routeif="$(route -n get "$probe" 2>/dev/null | awk '$1 == "interface:" { print $2 }')"
    if [ -n "$tsif" ] && [ -n "$routeif" ] && [ "$routeif" != "$tsif" ]; then
        bad "The VPN has taken over Tailscale's addresses (they go to $routeif, not Tailscale's $tsif)."
        explain_vpn
        return
    fi
    if [ -z "$FROM" ]; then
        ok "Tailscale's addresses still go through Tailscale."
        note "(Run with --from LIVE_MAC_IP to also check the live Mac answers.)"
        return
    fi
    local said
    # Any answer counts: through a relay too (by default it fails without a direct path).
    if said="$("$TS" ping --until-direct=false -c 3 --timeout 5s "$FROM" 2>&1)"; then
        ok "The live Mac answers over Tailscale: $(tail -n 1 <<<"$said")"
        if grep -q DERP <<<"$said"; then
            note "(Through a Tailscale relay: that works, a little slower.)"
        fi
    else
        bad "The live Mac ($FROM) doesn't answer over Tailscale with the VPN on."
        note "Check that the live Mac is on and on Tailscale. If it is, the VPN is in the way:"
        explain_vpn
    fi
}

explain_vpn() {
    note "Many VPN apps send ALL traffic through the VPN, Tailscale's too. In the VPN app:"
    note "- turn on 'Split tunnelling' and leave Tailscale out of the VPN, or"
    note "- turn on 'Allow LAN' / 'Local network access' / 'Allow Tailscale'."
    note "Then turn the VPN off and on, and run this script again."
}

summary() {
    step "Result"
    if [ "$PROBLEMS" -gt 0 ]; then
        echo "  $PROBLEMS thing(s) to fix above (marked [!!]). Fix them and run this script again."
        exit 1
    fi
    if [ -z "$KEY" ]; then
        echo "  All checks passed. Now add the live Mac's key: run this script again with it."
        exit 0
    fi
    echo "  The gateway is ready. Send this line to whoever sets up the live Mac:"
    echo
    echo "      Gateway: $USER@$TS_IP"
    echo
    echo "  On the live Mac they run: scripts/macos-tiktok-gateway.sh install $USER@$TS_IP"
}

main() {
    KEY="" FROM=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --from)
                FROM="${2:-}"
                [[ "$FROM" =~ ^[0-9a-fA-F.:]+$ ]] || usage
                shift 2
                ;;
            -h | --help) usage ;;
            *)
                KEY="$1"
                shift
                ;;
        esac
    done
    echo "Setting up this Mac as the TikTok gateway."
    check_mac
    check_tailscale
    check_remote_login
    check_sleep
    add_key
    check_tiktok
    check_tailscale_with_vpn
    summary
}

# Sourced (the tests do, for key_entry), it only defines the functions.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    main "$@"
fi
