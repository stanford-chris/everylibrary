#!/bin/bash
# everylibrary_geograph_annual.sh — refresh the Geograph index, once a year.
#
# Separate from the monthly pass because it is the one expensive step here: a
# 235 MB download and an 8.3-million-row scan, for a stage that has supplied
# 103 photographs in total. Geograph's own material mostly reaches this project
# already, mirrored onto Commons and picked up by stages 2 and 2b, so the direct
# stage is the tail rather than the trunk. Those 103 exist nowhere else, though,
# which is why it runs at all.
#
# ⚠️ extract_geograph_index() returns the cached JSON whenever it exists, so a
# refresh means DELETING the index, not just re-downloading the dump. Without
# that this script would download 235 MB and change nothing.
#
# The dump is deleted afterwards. It is re-downloadable, it is gitignored, and
# leaving it costs a quarter of a gigabyte for a year.
#
# Deliberately no `set -e`: the same worst-exit pattern as
# everylibrary_monthly.sh, so a failed describe still leaves the newly matched
# photographs in the manifest rather than throwing the download away.

export PATH="/Library/Frameworks/Python.framework/Versions/3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
HERE="$(cd "$(dirname "$0")" && pwd)"
DUMP="$HERE/data/gridimage_base.tsv.gz"
INDEX="$HERE/data/geograph_libraries.json"
MANIFEST="$HERE/data/uk_libraries_images.csv"

cd "$HERE" || exit 2
echo "=== everylibrary Geograph refresh $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
worst=0

count_postable() {
  "$PY" - "$MANIFEST" <<'PYIN'
import csv, sys
with open(sys.argv[1]) as f:
    print(sum(1 for r in csv.DictReader(f) if r.get("postable") == "yes"))
PYIN
}
before=$(count_postable)

echo "--- download"
if ! curl -fSL --retry 3 --retry-delay 30 \
     -o "$DUMP.part" https://data.geograph.org.uk/dumps/gridimage_base.tsv.gz; then
  echo "!! download FAILED, leaving the existing index alone"
  rm -f "$DUMP.part"
  exit 2
fi
mv "$DUMP.part" "$DUMP"

# Only now that a good dump is on disk. Deleting the index first and then
# failing the download would leave stage 3 with nothing at all.
rm -f "$INDEX"

echo "--- stage 3"
"$PY" "$HERE/everylibrary_images.py" --stage 3; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "--- manifest rebuild"
"$PY" "$HERE/everylibrary_images.py" --manifest-only; rc=$?
[ $rc -gt $worst ] && worst=$rc

echo "--- describe the new photographs"
"$PY" "$HERE/everylibrary_describe.py"; rc=$?
[ $rc -gt $worst ] && worst=$rc

after=$(count_postable)
echo "--- postable: $before -> $after"

rm -f "$DUMP"
echo "=== done, exit $worst ==="
exit $worst
