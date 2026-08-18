#!/usr/bin/env python3
"""
everylibrary_urls.py — find a website for each library, and check it is still there.

The poster links a library's name to its own page. Two sources supply one, in
descending order of specificity:

  roster     The `url` column the Libraries Hacked aggregate ships, already in
             uk_libraries_final.csv and until now simply dropped on the floor
             by the image pipeline.
  wikidata   P856, the official website on the library's own Wikidata item.
             Only consulted where the roster has nothing.

Neither is trustworthy without checking. The roster is a frozen snapshot taken
16 August 2026 and nothing re-fetches it, and councils restructure their sites
constantly: a sample of 80 found roughly one link in seven returning a hard 404,
clustered by authority, which is what a site migration looks like.

Three things here are load-bearing, each because the obvious version is wrong:

  Only 404 and 410 count as dead. Council sites sit behind WAFs that refuse
  automated requests outright, and a browser User-Agent does not help because
  they fingerprint more than that. Verified by hand: kent.gov.uk returns 403 to
  curl and loads perfectly in a browser, while lancashire.gov.uk's Ormskirk page
  returns 404 and is genuinely blank. Treating 403 as dead would throw away
  hundreds of working links; treating a timeout as dead would throw away links
  on the strength of one bad moment.

  Bare domains are not linked. 228 roster entries are a council's front page
  with no path. Linking "Ormskirk Library" to lancashire.gov.uk promises a
  library page and delivers a county homepage.

  One host at a time. Kent alone accounts for 98 URLs. Hosts are swept in
  parallel but each host is walked sequentially with a pause, so no council
  sees a burst.

Output is data/urls.json, keyed the way the image manifest keys rows. Resumable:
per-URL verdicts are cached in data/url_state.json, so a second run re-checks
nothing.

Usage:
    python3 everylibrary_urls.py                 # resolve, verify, write
    python3 everylibrary_urls.py --recheck       # discard cached verdicts first
    python3 everylibrary_urls.py --stdout        # report only, write nothing
"""

import argparse
import csv
import json
import os
import queue
import sys
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

CORPUS_PATH = os.path.join(DATA, "uk_libraries_final.csv")
URLS_PATH = os.path.join(DATA, "urls.json")
STATE_PATH = os.path.join(DATA, "url_state.json")

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15")
SPARQL_API = "https://query.wikidata.org/sparql"
SPARQL_UA = "everylibrary-bot/0.1 (https://chris-stanford.com; stanfordc+claude@mac.com)"

HOST_WORKERS = 8        # distinct councils swept at once
PER_HOST_DELAY = 1.0    # seconds between two requests to the same host
TIMEOUT = 20

DEAD_CODES = {404, 410}

P856_QUERY = """
SELECT ?item ?site WHERE {
  ?item wdt:P31/wdt:P279* wd:Q28564 .
  ?item wdt:P17 wd:Q145 .
  ?item wdt:P856 ?site .
}
"""


def log(msg):
    print(msg, flush=True)


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def library_key(row):
    """The key build_manifest uses, so the two files join without a lookup table."""
    return row["osm_id"] or f"{float(row['lat']):.5f},{float(row['lon']):.5f}"


def normalise(raw):
    """Return a tidy https-preferring URL, or None if it is not worth linking."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    if not p.path.strip("/") and not p.query:
        return None                      # a bare council homepage, not a library page
    return urlunparse(p)


def fetch_p856():
    """Official websites from Wikidata, for libraries the roster leaves blank."""
    r = requests.get(SPARQL_API, params={"query": P856_QUERY, "format": "json"},
                     headers={"User-Agent": SPARQL_UA, "Accept": "application/sparql-results+json"},
                     timeout=90)
    r.raise_for_status()
    out = {}
    for b in r.json()["results"]["bindings"]:
        out[b["item"]["value"].rsplit("/", 1)[-1]] = b["site"]["value"]
    return out


def check(session, url):
    """(status, final_url). status 0 means the request never completed, which is
    explicitly NOT treated as dead."""
    for method in ("head", "get"):
        try:
            r = getattr(session, method)(url, timeout=TIMEOUT, allow_redirects=True)
            if method == "head" and r.status_code in (403, 405, 501):
                continue                 # some servers only answer GET
            return r.status_code, r.url
        except requests.RequestException:
            continue
    return 0, url


def sweep(urls, state):
    """Check every URL, one host at a time, several hosts at once."""
    todo = [u for u in urls if u not in state]
    if not todo:
        log(f"verify   {len(urls)} urls, all cached")
        return
    by_host = {}
    for u in todo:
        by_host.setdefault(urlparse(u).netloc, []).append(u)
    log(f"verify   {len(todo)} urls across {len(by_host)} hosts, "
        f"{HOST_WORKERS} hosts at a time")

    work = queue.Queue()
    for host, group in sorted(by_host.items(), key=lambda kv: -len(kv[1])):
        work.put(group)
    lock = threading.Lock()
    done = [0]

    def worker():
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        while True:
            try:
                group = work.get_nowait()
            except queue.Empty:
                return
            for i, u in enumerate(group):
                if i:
                    time.sleep(PER_HOST_DELAY)
                status, final = check(session, u)
                with lock:
                    state[u] = {"status": status, "final": final}
                    done[0] += 1
                    if done[0] % 200 == 0:
                        save_json(STATE_PATH, state)
                        log(f"         {done[0]:>5}/{len(todo)}")

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(HOST_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    save_json(STATE_PATH, state)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recheck", action="store_true", help="discard cached verdicts first")
    ap.add_argument("--stdout", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    if args.recheck and os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        log("cached verdicts discarded")

    with open(CORPUS_PATH) as f:
        rows = list(csv.DictReader(f))
    log(f"corpus: {len(rows)} libraries")

    p856 = fetch_p856()
    log(f"wikidata P856: {len(p856)} UK public libraries with an official website")

    # Roster first, Wikidata only where it is blank.
    candidates = {}
    counts = {"roster": 0, "wikidata": 0, "bare": 0, "none": 0}
    for r in rows:
        key = library_key(r)
        url = normalise(r.get("url"))
        source = "roster"
        if not url:
            if (r.get("url") or "").strip():
                counts["bare"] += 1
            qid = (r.get("wikidata") or "").strip()
            url = normalise(p856.get(qid)) if qid else None
            source = "wikidata"
        if not url:
            counts["none"] += 1
            continue
        counts[source] += 1
        candidates[key] = {"url": url, "source": source}

    log(f"candidates: {counts['roster']} roster + {counts['wikidata']} wikidata "
        f"= {len(candidates)}; {counts['bare']} rejected as bare domains, "
        f"{counts['none']} with nothing")

    state = load_json(STATE_PATH, {})
    sweep(sorted({c["url"] for c in candidates.values()}), state)

    out, dead, unknown = {}, 0, 0
    for key, c in candidates.items():
        v = state.get(c["url"], {})
        status = v.get("status", 0)
        if status in DEAD_CODES:
            dead += 1
            continue
        if status == 0:
            unknown += 1
        final = v.get("final") or c["url"]
        # Keep the redirect target only when it is still a real page, not a
        # bounce to the council's front door.
        out[key] = {"url": final if normalise(final) else c["url"],
                    "source": c["source"], "status": status}

    log("")
    log(f"  linkable               {len(out):>5}")
    log(f"  dropped, 404 or 410    {dead:>5}")
    log(f"  kept but unverified    {unknown:>5}  (request never completed)")
    by_status = {}
    for v in out.values():
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    log("  status of what is kept:")
    for k, v in sorted(by_status.items(), key=lambda t: -t[1]):
        log(f"      {k or 'no response':<14} {v:>5}")

    if args.stdout:
        log("\n--stdout: nothing written")
        return
    save_json(URLS_PATH, out)
    log(f"\nwritten to {URLS_PATH}")


if __name__ == "__main__":
    main()
