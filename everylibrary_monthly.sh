#!/bin/bash
# everylibrary_monthly.sh — the 1st-of-the-month maintenance pass.
#
# Six steps that share a cadence but not a contract:
#
#   1. everylibrary_roster_check.py     Read-only. Has the frozen roster drifted?
#                                       Writes a report only when it has.
#   2. everylibrary_roster_apply.py     Adds libraries the roster gained, removes
#                                       ones it lost that were never posted.
#   3. everylibrary_urls.py --recheck   Re-verifies every library website and
#                                       rewrites data/urls.json.
#   4. everylibrary_images.py           Forgets the "nothing found" memos and
#      --recheck-misses                 looks again for photographs.
#   5. everylibrary_images.py           Cheap rebuild, so a failure in step 4
#      --manifest-only                  still lets step 3's work reach the poster.
#   6. everylibrary_describe.py         Alt text for whatever step 4 found.
#
# ⚠️ Steps 1 and 2 are deliberately separate. The check is the record of what
# upstream did and writes a report a person reads; the apply is the action. A
# combined script would mean the only account of a closure was the fact that a
# row had gone.
#
# ⚠️ Step 4 is the whole reason this pass grew. Until 23 August 2026 the monthly
# job ran --manifest-only alone, which fetches nothing, so no job had ever
# looked for a photograph that did not exist on 16 August. Every stage memoises
# its misses, permanently, so a plain re-run found nothing either: --recheck-
# misses is what expires those memos. Roughly 1,500 Commons calls, about 25
# minutes from Seoul.
#
# ⚠️ Each step runs regardless of what the previous one returned, and `set -e`
# is deliberately NOT in force around them. The roster check signalling drift is
# a normal outcome, not a reason to skip the link sweep, and a failed link sweep
# is not a reason to skip rebuilding the manifest from whatever verdicts did
# land. The exit code is the worst of the six, so launchd still records a
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

# Refuses on its own sanity bound rather than applying a mass change, and
# removes nothing at all if it cannot read the posted state.
echo "--- roster apply"
"$PY" "$HERE/everylibrary_roster_apply.py" --live; rc=$?
[ $rc -gt $worst ] && worst=$rc

# Count working links and postable rows before and after, so the log says what
# changed rather than just that something ran.
counts() {
  # The url column by name, not by grepping the row for "http": every
  # illustrated row already carries an image_url, a licence_url and a credit
  # page, so the naive version reported 3,049 of 3,750 and moved by one.
  "$PY" - "$MANIFEST" <<'PYIN'
import csv, sys
with open(sys.argv[1]) as f:
    rows = list(csv.DictReader(f))
print(f'{len(rows)} rows, '
      f'{sum(1 for r in rows if r.get("url", "").strip())} with a link, '
      f'{sum(1 for r in rows if r.get("postable") == "yes")} postable')
PYIN
}
before=$(counts)

echo "--- link re-verification"
"$PY" "$HERE/everylibrary_urls.py" --recheck; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "--- image re-sweep"
"$PY" "$HERE/everylibrary_images.py" --recheck-misses; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "--- manifest rebuild"
"$PY" "$HERE/everylibrary_images.py" --manifest-only; rc=$?
[ $rc -gt $worst ] && worst=$rc

# Only libraries with no description are touched, so this costs nothing in a
# month that found no new photographs.
echo "--- describe"
"$PY" "$HERE/everylibrary_describe.py"; rc=$?
[ $rc -gt $worst ] && worst=$rc

after=$(counts)
echo "--- before: $before"
echo "--- after:  $after"

# ⚠️ Reports only. update_colophon_library_count.py commits and pushes to the
# site repo by default, and this job must not do that unattended: the change
# would sit committed but undeployed until someone ran site_deploy.sh, and an
# automated push to a public repo is not this script's call to make. Until the
# monthly pass existed the colophon could only go stale when someone re-ran the
# harvest by hand; now it drifts every month, so the drift is at least named.
echo "--- colophon figures"
"$PY" "$HOME/Scripts/update_colophon_library_count.py" --dry-run; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "=== done, exit $worst ==="
exit $worst
