#!/usr/bin/env bash
# Record the investor-demo scenes from the running DaaS deployment.
#
#   ./record_demo.sh                 record every scene in scenes/, in order
#   ./record_demo.sh 07_model3d      record one scene (or several)
#   KEEP_BROWSER=1 ./record_demo.sh  leave the browser session open afterwards
#
# Each scene is one file in scenes/ and produces
#   recordings/<scene>.webm   the raw 1920x1080 screencast
#   recordings/<scene>.json   named timestamps the editor cuts on
#
# Credentials come from .env.local (DAAS_DEMO_URL, DAAS_DEMO_USER,
# DAAS_DEMO_PASSWORD). The password is used once, for an API sign-in from this
# script; only the resulting session cookie reaches the browser, so the sign-in
# form, the password and the cookie never appear in a recording or a log.
#
# Read-only against the deployment: scenes open, look and measure. They do not
# create tasks, run plugins, install products or change settings.
set -euo pipefail
cd "$(dirname "$0")"

SESSION=${SESSION:-daas-demo}
OUT=recordings
WORK=.work
mkdir -p "$OUT" "$WORK"
chmod 700 "$WORK"

for tool in playwright-cli curl python3; do
  command -v "$tool" >/dev/null || { echo "missing required tool: $tool" >&2; exit 1; }
done
[ -f .env.local ] || { echo "missing .env.local (see README.md)" >&2; exit 1; }
set -a; . ./.env.local; set +a
: "${DAAS_DEMO_URL:?set in .env.local}" "${DAAS_DEMO_USER:?set in .env.local}" "${DAAS_DEMO_PASSWORD:?set in .env.local}"
BASE="$DAAS_DEMO_URL/assets/webodm_frontend/frontend"
HOST=$(python3 -c 'import sys,urllib.parse as u; print(u.urlparse(sys.argv[1]).hostname)' "$DAAS_DEMO_URL")

pw() { playwright-cli -s="$SESSION" "$@"; }

JAR="$WORK/session.cookies"
cleanup() {
  # End the API session this script opened, then forget its cookie.
  [ -f "$JAR" ] && curl -sk -X POST -b "$JAR" -o /dev/null "$DAAS_DEMO_URL/api/method/logout" || true
  rm -f "$JAR" "$WORK/_scene.js"
  [ -n "${KEEP_BROWSER:-}" ] || pw close >/dev/null 2>&1 || true
}
trap cleanup EXIT

# --- sign in (API, not the form) ---------------------------------------------
code=$(umask 077; curl -sk -c "$JAR" -o /dev/null -w '%{http_code}' \
  --data-urlencode "usr=$DAAS_DEMO_USER" --data-urlencode "pwd=$DAAS_DEMO_PASSWORD" \
  "$DAAS_DEMO_URL/api/method/login")
[ "$code" = 200 ] || { echo "sign-in failed (HTTP $code)" >&2; exit 1; }
SID=$(awk '$6=="sid"{print $7}' "$JAR")
[ -n "$SID" ] || { echo "sign-in returned no session" >&2; exit 1; }

unset DAAS_DEMO_PASSWORD

# --- browser -------------------------------------------------------------------
open_browser() {
  pw close >/dev/null 2>&1 || true
  pw open --config=playwright-cli.config.json "$DAAS_DEMO_URL/login" >/dev/null
  pw cookie-set sid "$SID" --domain="$HOST" --httpOnly --secure >/dev/null
}
# The browser can die under a heavy 3D scene; a take must never fail for that.
browser_alive() { [ "$(pw --raw eval '1+1' 2>/dev/null | tr -d '[:space:]')" = 2 ]; }
open_browser

# --- scenes --------------------------------------------------------------------
if [ $# -gt 0 ]; then
  scenes=("$@")
else
  scenes=()
  for f in scenes/[0-9]*.js; do scenes+=("$(basename "$f" .js)"); done
fi

failed=()
for scene in "${scenes[@]}"; do
  scene=${scene%.js}
  src="scenes/$scene.js"
  [ -f "$src" ] || { echo "no such scene: $scene" >&2; failed+=("$scene"); continue; }
  ok=
  for attempt in 1 2 3; do
    printf '%-18s take %s … ' "$scene" "$attempt"
    browser_alive || open_browser
    # `run-code` takes one function expression: wrap the helper library and
    # the scene into it.
    python3 - "$src" "$BASE" "$OUT" > "$WORK/_scene.js" <<'PY'
import re, sys
scene, base, out = sys.argv[1:4]
strip = lambda s: re.sub(r'\A(\s*//[^\n]*\n)+', '', s).strip()
lib = strip(open('scenes/_lib.js').read())
body = strip(open(scene).read())
print('async page => {')
print('  const cfg = { base: %r, out: %r, fps: 30, width: 1920, height: 1080, slow: 3 };' % (base, out))
print('  const h = (' + lib + ')(page, cfg);')
print('  try { return await (' + body + ')(page, h); }')
print('  catch (e) { await page.screencast.stop().catch(() => {}); throw e; }')
print('}')
PY
    log="$WORK/$scene.log"
    pw run-code --filename="$WORK/_scene.js" > "$log" 2>&1 || true
    if python3 - "$log" "$OUT/$scene.json" <<'PY'
import json, re, sys
log, dest = sys.argv[1:3]
text = open(log).read()
m = re.search(r'^### Result\n(.*?)\n(?=###|\Z)', text, re.S | re.M)
if not m or '### Error' in text:
    err = re.search(r'^### Error\n(.*?)(?:\n|$)', text, re.M)
    print('failed:', err.group(1)[:160] if err else 'no result')
    sys.exit(1)
marks = json.loads(m.group(1))
json.dump(marks, open(dest, 'w'), indent=2)
print('ok  %.1fs of footage' % marks.get('end', 0))
PY
    then ok=1; break; fi
    sleep 3
  done
  [ -n "$ok" ] || failed+=("$scene")
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "scenes that did not record: ${failed[*]}" >&2
  exit 1
fi
echo "done — recordings in $OUT/. Next: python3 build_video.py"
