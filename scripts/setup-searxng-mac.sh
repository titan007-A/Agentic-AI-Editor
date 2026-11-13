#!/bin/bash
# Install SearXNG as a local search proxy on macOS (runs on 127.0.0.1:8787).
# After running: add SEARXNG_URL=http://127.0.0.1:8787 to your .env and restart the server.
set -e

UV=/opt/homebrew/bin/uv
VENV="$HOME/searxng-venv"
SRC="$HOME/searxng-src"
CFG="$HOME/.config/searxng/settings.yml"
PLIST="$HOME/Library/LaunchAgents/com.agentic-editor.searxng.plist"
LOGDIR="$HOME/Library/Logs/agentic-editor"
PORT=8787

echo "==> SearXNG setup"

if [ ! -f "$UV" ]; then
  echo "uv not found at $UV; install it first: brew install uv"
  exit 1
fi

# Clone or update
if [ -d "$SRC/.git" ]; then
  echo "==> Updating SearXNG source"
  git -C "$SRC" pull --ff-only -q
else
  echo "==> Cloning SearXNG"
  git clone --depth=1 https://github.com/searxng/searxng.git "$SRC"
fi

# Install into a dedicated venv
echo "==> Installing into $VENV"
"$UV" venv "$VENV" --python 3.12 -q
"$UV" pip install setuptools --python "$VENV/bin/python3" -q
"$UV" pip install -r "$SRC/requirements.txt" --python "$VENV/bin/python3" -q
"$UV" pip install -e "$SRC" --no-build-isolation --python "$VENV/bin/python3" -q

# Config
mkdir -p "$(dirname "$CFG")" "$LOGDIR"
cat > "$CFG" << YAML
use_default_settings: true

general:
  contact_url: false

server:
  port: $PORT
  bind_address: "127.0.0.1"
  secret_key: "agentic-editor-searxng-local-$(uuidgen | tr '[:upper:]' '[:lower:]')"
  public_instance: false
  image_proxy: false
  base_url: "http://127.0.0.1:$PORT/"
  limiter: false

search:
  safe_search: 0
  default_lang: "en"
  formats:
    - html
    - json
    - csv
    - rss

engines:
  # Measured from the Mac (2026-11-13). Yahoo and Yandex answer well and keep to "site:";
  # Bing answers unrelated pages and ignores "site:"; Google says "access denied";
  # DuckDuckGo asks for a CAPTCHA or times out (10 s lost a search). SearXNG ships Yahoo,
  # Yandex, Bing and Google switched off: naming them without "disabled: false" leaves
  # them off. websearch.py drops what is still unrelated.
  - name: yahoo
    disabled: false
    weight: 2
  - name: yandex
    disabled: false
    weight: 1
  - name: brave
    weight: 2
  - name: duckduckgo
    disabled: true
  - name: bing
    disabled: true
  - name: google
    disabled: true

outgoing:
  request_timeout: 10.0
  max_request_timeout: 15.0
  pool_connections: 20
  pool_maxsize: 20

ui:
  static_use_hash: false
YAML

# launchd plist
cat > "$PLIST" << XML
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agentic-editor.searxng</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV/bin/searxng-run</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>SEARXNG_SETTINGS_PATH</key>
        <string>$CFG</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOGDIR/searxng.log</string>
    <key>StandardErrorPath</key>
    <string>$LOGDIR/searxng.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
XML

# Load service
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "==> Service loaded; waiting for startup..."
sleep 8

# Verify
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/search?q=test&format=json")
if [ "$STATUS" = "200" ]; then
  echo "==> SearXNG is running at http://127.0.0.1:$PORT  (HTTP $STATUS)"
  echo ""
  echo "Next steps:"
  echo "  1. Add to .env:  SEARXNG_URL=http://127.0.0.1:$PORT"
  echo "  2. Restart the server: scripts/macos-service.sh restart"
else
  echo "ERROR: SearXNG returned HTTP $STATUS — check $LOGDIR/searxng.log"
  exit 1
fi
