#!/usr/bin/env bash
# Copies the editor's data from this machine to a Mac set up with scripts/setup-macos.sh:
# the jobs database, finished videos, downloaded sources, uploads, presets, assets, fonts
# and the login settings, then fixes the stored paths there. Caches, the job queue and
# model weights are left behind: the Mac uses its own builds of the models.
#   scripts/move-to-mac.sh you@your-mac.local [path of the repository on the Mac]
set -euo pipefail

cd "$(dirname "$0")/.."
HOST="${1:?usage: $0 user@mac [repository path on the Mac, default Agentic-AI-Editor]}"
REMOTE="${2:-Agentic-AI-Editor}"
SSH="${MOVE_SSH:-ssh}"
remote() {
    $SSH "$HOST" "cd $REMOTE && $1"
}

remote "test -x .venv/bin/aiedit" || {
    echo "no set-up editor at $HOST:$REMOTE; run scripts/setup-macos.sh there first"
    exit 1
}
if remote "test -s media/app.db" && [ "${FORCE:-}" != 1 ]; then
    echo "the Mac already has a jobs database; FORCE=1 replaces it with this machine's"
    exit 1
fi
if remote ".venv/bin/python -c 'import socket; from agentic_editor.settings import get_settings
raise SystemExit(socket.socket().connect_ex((\"127.0.0.1\", get_settings().port)) != 0)'"; then
    echo "the editor is running on the Mac; stop it first: scripts/macos-service.sh uninstall"
    exit 1
fi

# A consistent copy of the database even if this machine's server is still running.
snapshot="$(mktemp -d)"
trap 'rm -rf "$snapshot"' EXIT
uv run python - "$snapshot/app.db" <<'EOF'
import sqlite3, sys
from agentic_editor.settings import get_settings
source = sqlite3.connect(get_settings().database)
source.backup(sqlite3.connect(sys.argv[1]))
EOF

remote "mkdir -p media"
copy() {
    rsync -a --info=progress2 -e "$SSH" "$1" "$HOST:$REMOTE/$2"
}
copy "$snapshot/app.db" media/app.db
for dir in media/outputs media/sources media/uploads presets assets fonts; do
    if [ -d "$dir" ]; then
        echo "copying $dir"
        copy "$dir/" "$dir/"
    fi
done

# The same password and sessions as here.
grep -E "^(PASSWORD_HASH|SECRET_KEY)=" .env | remote ".venv/bin/python -c '
import re, sys
from pathlib import Path
env = Path(\".env\")
text = env.read_text()
for line in sys.stdin.read().splitlines():
    key = line.split(\"=\", 1)[0]
    pattern = re.compile(rf\"^{key}=.*$\", re.M)
    text = pattern.sub(lambda m: line, text) if pattern.search(text) else text + line + \"\\n\"
env.write_text(text)
'"

remote ".venv/bin/aiedit relocate '$(pwd)'"
echo "done; on the Mac start the editor with: scripts/macos-service.sh install"
