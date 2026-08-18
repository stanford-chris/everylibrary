#!/bin/bash
# everylibrary_monthly.sh — the 1st-of-the-month maintenance pass.
#
# Two jobs that share a cadence but not a contract:
#
#   1. everylibrary_roster_check.py   Read-only. Has the frozen roster drifted?
#                                     Writes a report only when it has.
#   2. everylibrary_urls.py --recheck Re-verifies every library website and
#                                     rewrites data/urls.json, then the manifest
#                                     is rebuilt so the poster sees the change.
#
# The recheck exists because council websites rot. 473 of 2,675 links were
# already dead when first swept on 18 August 2026, two days after the roster
# snapshot, clustered by authority: a council restructures its site and every
# one of its libraries breaks at once. Without this the bot would go on linking
# to 404s for years.
#
# ⚠️ Each step runs regardless of what the previous one returned, and `set -e`
# is deliberately NOT in force around them. The roster check signalling drift is
# a normal outcome, not a reason to skip the link sweep, and a failed link sweep
# is not a reason to skip rebuilding the manifest from whatever verdicts did
# land. The exit code is the worst of the three, so launchd still records a
# failure.

export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
HERE="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$HERE/data/uk_libraries_images.csv"

cd "$HERE" || exit 2
echo "=== everylibrary monthly pass $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
worst=0

# ⚠️ everylibrary_roster_check.py exits 1 when it FINDS DRIFT, which is a
# result and not a failure: it writes its report and notifies either way. Left
# unhandled that made the whole monthly job read as failing in launchd every
# time the upstream roster gained a library, which it usually has.
echo "--- roster drift check"
"$PY" "$HERE/everylibrary_roster_check.py"; rc=$?
if [ $rc -eq 1 ]; then
  echo "    (exit 1 = drift detected, see ~/Library/Logs/everylibrary-roster.md)"
elif [ $rc -gt $worst ]; then
  worst=$rc
fi

# Count working links before and after, so the log says what changed rather
# than just that something ran.
count_links() {
  # The url column by name, not by grepping the row for "http": every
  # illustrated row already carries an image_url, a licence_url and a credit
  # page, so the naive version reported 3,049 of 3,750 and moved by one.
  "$PY" - "$MANIFEST" <<'PYIN'
import csv, sys
with open(sys.argv[1]) as f:
    print(sum(1 for r in csv.DictReader(f) if r.get("url", "").strip()))
PYIN
}
before=$(count_links)

echo "--- link re-verification"
"$PY" "$HERE/everylibrary_urls.py" --recheck; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "--- manifest rebuild"
"$PY" "$HERE/everylibrary_images.py" --manifest-only; rc=$?
[ $rc -gt $worst ] && worst=$rc

after=$(count_links)
echo "--- rows carrying a link: $before -> $after"

echo "=== done, exit $worst ==="
exit $worst
