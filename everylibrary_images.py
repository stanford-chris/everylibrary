#!/usr/bin/env python3
"""
everylibrary_images.py — build a freely-licensed image manifest for the UK library corpus.

Sources, tried in order of confidence:

  1. Wikidata P18      A photo attached to the library's own Wikidata item. Highest
                       confidence: a human linked that picture to that building.
  2. Commons geosearch A Commons file within RADIUS_M metres whose title mentions a
                       library. This also catches the Geograph photos that have been
                       bulk-mirrored onto Commons, with their attribution already
                       normalised into Commons metadata.
  3. Geograph dump     Offline index for whatever the first two miss. Optional: needs
                       gridimage_base.tsv.gz (235 MB) from data.geograph.org.uk/dumps.

Every row carries photographer, licence name and a credit URL, because everything
here is CC BY-SA and attribution is a condition of use. No Google content is
touched: the Maps Platform terms forbid storing or resharing Street View imagery.

Resumable. State is written to STATE_PATH after each stage and periodically during
the geosearch sweep, so an interrupted run picks up where it left off.

Usage:
    python3 everylibrary_images.py                 # run all available stages
    python3 everylibrary_images.py --stage 1       # Wikidata only
    python3 everylibrary_images.py --reset         # discard saved state and restart
"""

import argparse
import csv
import difflib
import json
import math
import os
import re
import sys
import time

import requests

# ---------------------------------------------------------------- configuration

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

CORPUS_PATH = os.path.join(DATA, "uk_libraries_final.csv")
STATE_PATH = os.path.join(DATA, "image_state.json")
MANIFEST_PATH = os.path.join(DATA, "uk_libraries_images.csv")
GEOGRAPH_DUMP = os.path.join(DATA, "gridimage_base.tsv.gz")

# Wikimedia asks for a descriptive User-Agent with contact details.
USER_AGENT = "everylibrary-bot/0.1 (https://chris-stanford.com; stanfordc+claude@mac.com)"

RADIUS_M = 250          # geosearch radius; postcode-derived coords are ~45 m out
THUMB_WIDTH = 1600      # Commons renders this on demand; keeps blobs under Bluesky's cap
REQUEST_DELAY = 0.15    # politeness pause between API calls
BATCH_IMAGEINFO = 50    # titles per imageinfo call
BATCH_SPARQL = 400      # QIDs per SPARQL VALUES clause
SAVE_EVERY = 100        # checkpoint the state file this often during the sweep

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
SPARQL_API = "https://query.wikidata.org/sparql"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# ---------------------------------------------------------------------- helpers


def log(msg):
    print(msg, flush=True)


def get_json(url, params, retries=3):
    """GET with linear backoff. Returns None rather than raising, so one bad
    response never kills a sweep of thousands."""
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except (requests.RequestException, ValueError):
            time.sleep(1.5 * (attempt + 1))
    return None


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def normalise_name(s):
    """Reduce a library name to its distinctive part, so 'Bungay Library' and
    'Bungay Community Library' compare as equal."""
    s = (s or "").lower()
    s = re.sub(r"\b(library|libraries|the|public|community|hub|centre|center|and|of)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p = math.radians
    dlat, dlon = p(lat2 - lat1), p(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"p18": {}, "geo": {}, "imageinfo": {}, "geosearch_done": []}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def load_corpus():
    with open(CORPUS_PATH) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["lat"] = float(r["lat"])
        r["lon"] = float(r["lon"])
    return rows


# ------------------------------------------------------------ stage 1: Wikidata


def stage1_wikidata(rows, state):
    """Fetch P18 (image) for every library that has a Wikidata item."""
    qids = sorted({r["wikidata"] for r in rows if r["wikidata"]})
    todo = [q for q in qids if q not in state["p18"]]
    log(f"stage 1  Wikidata P18: {len(qids)} items, {len(todo)} still to fetch")
    if not todo:
        return

    for i in range(0, len(todo), BATCH_SPARQL):
        chunk = todo[i:i + BATCH_SPARQL]
        values = " ".join(f"wd:{q}" for q in chunk)
        query = f"SELECT ?item ?img WHERE {{ VALUES ?item {{ {values} }} ?item wdt:P18 ?img }}"
        d = get_json(SPARQL_API, {"query": query, "format": "json"})
        if d is None:
            log(f"         chunk at {i} failed, skipping")
            continue
        for q in chunk:                       # record misses too, so we don't refetch
            state["p18"].setdefault(q, None)
        for b in d["results"]["bindings"]:
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            fname = requests.utils.unquote(b["img"]["value"].rsplit("/", 1)[-1])
            state["p18"][qid] = fname
        save_state(state)
        got = sum(1 for v in state['p18'].values() if v)
        log(f"         {min(i + BATCH_SPARQL, len(todo)):>5}/{len(todo)}  with image: {got}")
        time.sleep(REQUEST_DELAY)


# --------------------------------------------------------- stage 2: geosearch


def stage2_geosearch(rows, state):
    """For libraries with no P18, look for a nearby Commons file whose title
    mentions a library. Picks the best candidate by title match, then distance."""
    pending = []
    for r in rows:
        key = r["osm_id"] or f"{r['lat']:.5f},{r['lon']:.5f}"
        qid = r["wikidata"]
        if qid and state["p18"].get(qid):
            continue                                  # already covered by stage 1
        if key in state["geosearch_done"]:
            continue
        pending.append((key, r))

    log(f"stage 2  Commons geosearch: {len(pending)} libraries to sweep")
    done_set = set(state["geosearch_done"])

    for n, (key, r) in enumerate(pending, 1):
        d = get_json(COMMONS_API, {
            "action": "query", "format": "json", "list": "geosearch",
            "gsnamespace": 6, "gscoord": f"{r['lat']}|{r['lon']}",
            "gsradius": RADIUS_M, "gslimit": 50,
        })
        hits = (d or {}).get("query", {}).get("geosearch", []) or []
        target = normalise_name(r["name"])

        def score(h):
            title = h["title"].lower()
            s = 0.0
            if "librar" in title:
                s += 10.0                              # must look like a library
            s += 2.0 * difflib.SequenceMatcher(
                None, target, normalise_name(h["title"].replace("File:", ""))).ratio()
            s -= h.get("dist", RADIUS_M) / RADIUS_M    # prefer closer
            return s

        candidates = [h for h in hits if "librar" in h["title"].lower()]
        if candidates:
            best = max(candidates, key=score)
            state["geo"][key] = {"title": best["title"], "dist": round(best.get("dist", 0))}

        done_set.add(key)
        if n % SAVE_EVERY == 0 or n == len(pending):
            state["geosearch_done"] = sorted(done_set)
            save_state(state)
            log(f"         {n:>5}/{len(pending)}  matched so far: {len(state['geo'])}")
        time.sleep(REQUEST_DELAY)

    state["geosearch_done"] = sorted(done_set)
    save_state(state)


# ------------------------------------------------- resolve files to attribution


def fetch_imageinfo(titles, state):
    """Batch-resolve Commons file titles to URL, size, photographer and licence."""
    todo = [t for t in titles if t not in state["imageinfo"]]
    log(f"resolve  imageinfo: {len(titles)} files, {len(todo)} still to fetch")

    for i in range(0, len(todo), BATCH_IMAGEINFO):
        chunk = todo[i:i + BATCH_IMAGEINFO]
        d = get_json(COMMONS_API, {
            "action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|user", "iiurlwidth": THUMB_WIDTH,
            "titles": "|".join(chunk),
        })
        if d is None:
            log(f"         chunk at {i} failed, skipping")
            continue
        # Commons normalises titles (underscores, capitalisation); map them back
        norm = {n["to"]: n["from"] for n in d.get("query", {}).get("normalized", [])}
        for page in d.get("query", {}).get("pages", {}).values():
            title = page.get("title")
            requested = norm.get(title, title)
            info = (page.get("imageinfo") or [{}])[0]
            if not info:
                state["imageinfo"][requested] = None
                continue
            em = info.get("extmetadata", {})
            state["imageinfo"][requested] = {
                "url": info.get("thumburl") or info.get("url"),
                "full_url": info.get("url"),
                "width": info.get("thumbwidth") or info.get("width"),
                "height": info.get("thumbheight") or info.get("height"),
                "page": info.get("descriptionurl"),
                "artist": strip_html(em.get("Artist", {}).get("value")),
                "uploader": info.get("user"),
                "licence": em.get("LicenseShortName", {}).get("value"),
                "licence_url": em.get("LicenseUrl", {}).get("value"),
            }
        for t in chunk:
            state["imageinfo"].setdefault(t, None)
        save_state(state)
        log(f"         {min(i + BATCH_IMAGEINFO, len(todo)):>5}/{len(todo)}")
        time.sleep(REQUEST_DELAY)


# ---------------------------------------------------------- stage 3: Geograph


GEOGRAPH_INDEX = os.path.join(DATA, "geograph_libraries.json")
GEOGRAPH_LICENCE = "CC BY-SA 2.0"
GEOGRAPH_LICENCE_URL = "https://creativecommons.org/licenses/by-sa/2.0/"


def extract_geograph_index():
    """Pull every photo whose title mentions a library out of the bulk dump.
    ~8.3 M rows in, ~9 k rows out, so this is worth caching to disk."""
    import gzip

    if os.path.exists(GEOGRAPH_INDEX):
        with open(GEOGRAPH_INDEX) as f:
            return json.load(f)

    csv.field_size_limit(10 ** 7)
    out = []
    with gzip.open(GEOGRAPH_DUMP, "rt", encoding="utf-8", errors="replace", newline="") as f:
        rdr = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        idx = {h: i for i, h in enumerate(next(rdr))}
        for r in rdr:
            if len(r) <= idx["wgs84_long"]:
                continue
            if "librar" not in r[idx["title"]].lower():
                continue
            try:
                lat, lon = float(r[idx["wgs84_lat"]]), float(r[idx["wgs84_long"]])
            except ValueError:
                continue
            if lat == 0 and lon == 0:
                continue
            out.append({"id": r[idx["gridimage_id"]], "title": r[idx["title"]],
                        "photographer": r[idx["realname"]], "lat": lat, "lon": lon,
                        "taken": r[idx["imagetaken"]]})
    with open(GEOGRAPH_INDEX, "w") as f:
        json.dump(out, f)
    return out


def geograph_image_url(photo_id):
    """Geograph filenames embed an unguessable hash, so read it off the photo
    page. The 'stamped' variant carries the credit burned into the image."""
    try:
        r = SESSION.get(f"https://www.geograph.org.uk/photo/{photo_id}", timeout=45)
        if r.status_code != 200:
            return None
        m = re.search(r'property="og:image"\s+content="([^"]+)"', r.text)
        return m.group(1) if m else None
    except requests.RequestException:
        return None


def stage3_geograph(rows, state):
    """Fill gaps from Geograph for libraries the first two stages missed."""
    # The extracted index is enough on its own; the 235 MB dump is only needed
    # to rebuild it, and is deliberately not kept in the repo.
    if not (os.path.exists(GEOGRAPH_INDEX) or os.path.exists(GEOGRAPH_DUMP)):
        log("stage 3  skipped: neither geograph_libraries.json nor the dump is present")
        return

    photos = extract_geograph_index()
    log(f"stage 3  Geograph: {len(photos)} library-titled photos in the index")

    grid = {}
    for p in photos:
        grid.setdefault((round(p["lat"], 2), round(p["lon"], 2)), []).append(p)

    pending = []
    for r in rows:
        key = r["osm_id"] or f"{r['lat']:.5f},{r['lon']:.5f}"
        qid = r["wikidata"]
        if (qid and state["p18"].get(qid)) or key in state["geo"]:
            continue
        if key in state.setdefault("geograph", {}):
            continue
        pending.append((key, r))
    log(f"         {len(pending)} libraries still without an image")

    taken = {v["id"] for v in state["geograph"].values() if v}
    for n, (key, r) in enumerate(pending, 1):
        best, best_score = None, -99.0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for p in grid.get((round(r["lat"] + dy * 0.01, 2),
                                   round(r["lon"] + dx * 0.01, 2)), []):
                    if p["id"] in taken:
                        continue
                    d = haversine_m(r["lat"], r["lon"], p["lat"], p["lon"])
                    if d > RADIUS_M:
                        continue
                    s = 2.0 * difflib.SequenceMatcher(
                        None, normalise_name(r["name"]), normalise_name(p["title"])
                    ).ratio() - d / RADIUS_M
                    if s > best_score:
                        best, best_score = p, s
        if best:
            url = geograph_image_url(best["id"])
            if url:
                taken.add(best["id"])
                state["geograph"][key] = {
                    "id": best["id"], "title": best["title"], "url": url,
                    "photographer": best["photographer"],
                    "dist": round(haversine_m(r["lat"], r["lon"], best["lat"], best["lon"])),
                }
            else:
                state["geograph"][key] = None
            time.sleep(REQUEST_DELAY)
        else:
            state["geograph"][key] = None

        if n % SAVE_EVERY == 0 or n == len(pending):
            save_state(state)
            got = sum(1 for v in state["geograph"].values() if v)
            log(f"         {n:>5}/{len(pending)}  matched: {got}")
    save_state(state)


def repair_uploaders(state):
    """Backfill the uploader for cached entries that have no Artist and were
    fetched before `user` was added to the imageinfo request."""
    need = [t for t, m in state["imageinfo"].items()
            if m and not (m.get("artist") or "").strip() and not m.get("uploader")]
    if not need:
        return
    log(f"repair   {len(need)} images have no Artist; fetching the Commons uploader")
    for i in range(0, len(need), BATCH_IMAGEINFO):
        chunk = need[i:i + BATCH_IMAGEINFO]
        d = get_json(COMMONS_API, {
            "action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "user", "titles": "|".join(chunk),
        })
        if d is None:
            continue
        norm = {n["to"]: n["from"] for n in d.get("query", {}).get("normalized", [])}
        for page in d.get("query", {}).get("pages", {}).values():
            requested = norm.get(page.get("title"), page.get("title"))
            info = (page.get("imageinfo") or [{}])[0]
            if info and requested in state["imageinfo"] and state["imageinfo"][requested]:
                state["imageinfo"][requested]["uploader"] = info.get("user")
        time.sleep(REQUEST_DELAY)
    save_state(state)


def clean_artist(meta):
    """Resolve a usable photographer credit from Commons metadata.

    Commons' Artist field is free-form wikitext and comes in three awkward shapes:

      "Roger Davies"                     already fine
      "No machine-readable author        the uploader IS the credit, buried in prose;
       provided. Mcginnly assumed        naive bracket-stripping leaves "Mcginnly assumed"
       (based on copyright claims)."
      ""                                 empty, so fall back to the Commons uploader

    Never returns a bare placeholder: an uncredited CC BY-SA image is a licence
    breach, so a post with no resolvable name must not go out.
    """
    if not meta:
        return ""
    raw = (meta.get("artist") or "").strip()

    m = re.search(r"No machine-readable author provided\.?\s*(.+?)\s+assumed\b", raw, re.I)
    if m:
        return m.group(1).strip()

    raw = re.sub(r"\s*\(based on copyright claims\)\.?", "", raw, flags=re.I)
    raw = re.sub(r"\s*\((?:talk|contribs?)[^)]*\)", "", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip(" .,;")

    if not raw:
        raw = (meta.get("uploader") or "").strip()

    if len(raw) > 60:
        raw = raw[:57].rstrip() + "..."
    return raw


def attribution_line(meta):
    """CC BY-SA requires the photographer and the licence. Short enough to sit
    inside a Bluesky post."""
    if not meta:
        return ""
    who = clean_artist(meta)
    if not who:
        return ""
    lic = meta.get("licence") or "CC BY-SA"
    return f"Photo: {who} / {lic}"


# ------------------------------------------------------------------- manifest


def build_manifest(rows, state):
    out = []
    for r in rows:
        key = r["osm_id"] or f"{r['lat']:.5f},{r['lon']:.5f}"
        source = title = dist = None

        qid = r["wikidata"]
        if qid and state["p18"].get(qid):
            source, title = "wikidata-p18", "File:" + state["p18"][qid]
        elif key in state["geo"]:
            source = "commons-geosearch"
            title = state["geo"][key]["title"]
            dist = state["geo"][key]["dist"]

        meta = state["imageinfo"].get(title) if title else None
        if title and not meta:
            source = None                     # file vanished or failed to resolve

        if source is None:
            g = state.get("geograph", {}).get(key)
            if g:
                source, dist = "geograph", g["dist"]
                title = g["title"]
                meta = {"url": g["url"], "full_url": g["url"],
                        "width": "", "height": "",
                        "page": f"https://www.geograph.org.uk/photo/{g['id']}",
                        "artist": g["photographer"],
                        "licence": GEOGRAPH_LICENCE, "licence_url": GEOGRAPH_LICENCE_URL}

        out.append({
            "name": r["name"], "authority": r["authority"], "nation": r["nation"],
            "address": r.get("address", ""), "postcode": r["postcode"],
            "lat": r["lat"], "lon": r["lon"],
            "type": r["type"], "statutory": r["statutory"], "year_opened": r["year_opened"],
            "wikidata": qid,
            "image_source": source or "",
            "image_title": title if source else "",
            "image_url": (meta or {}).get("url", "") if source else "",
            "image_width": (meta or {}).get("width", "") if source else "",
            "image_height": (meta or {}).get("height", "") if source else "",
            "photographer": clean_artist(meta) if source else "",
            "licence": (meta or {}).get("licence", "") if source else "",
            "licence_url": (meta or {}).get("licence_url", "") if source else "",
            "credit_page": (meta or {}).get("page", "") if source else "",
            "attribution": attribution_line(meta) if source else "",
            "match_m": dist if dist is not None else "",
            "alt_text": f"Photograph of {r['name']}, {r['authority']}." if source else "",
            # An uncredited CC BY-SA image breaches the licence, so a row is only
            # safe to post when it has both an image and a resolvable photographer.
            "postable": "yes" if (source and clean_artist(meta)) else "no",
        })

    with open(MANIFEST_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    return out


def report(out):
    total = len(out)
    have = [r for r in out if r["image_source"]]
    log("")
    log(f"MANIFEST: {MANIFEST_PATH}")
    log(f"  libraries              {total:>5}")
    log(f"  with a usable image    {len(have):>5}  ({len(have) / total * 100:.1f}%)")
    by_source = {}
    by_lic = {}
    for r in have:
        by_source[r["image_source"]] = by_source.get(r["image_source"], 0) + 1
        by_lic[r["licence"] or "(unstated)"] = by_lic.get(r["licence"] or "(unstated)", 0) + 1
    for k, v in sorted(by_source.items(), key=lambda t: -t[1]):
        log(f"      {k:<20} {v:>5}")
    log("  licences:")
    for k, v in sorted(by_lic.items(), key=lambda t: -t[1])[:8]:
        log(f"      {k:<20} {v:>5}")
    log("  by nation:")
    for n in ["England", "Scotland", "Wales", "Northern Ireland"]:
        tot = sum(1 for r in out if r["nation"] == n)
        got = sum(1 for r in have if r["nation"] == n)
        if tot:
            log(f"      {n:<20} {got:>5}/{tot:<5} ({got / tot * 100:.1f}%)")
    postable = [r for r in out if r["postable"] == "yes"]
    missing_credit = [r for r in have if not r["photographer"]]
    log(f"  safe to post           {len(postable):>5}  ({len(postable) / total * 100:.1f}%)")
    if missing_credit:
        log(f"  BLOCKED, no credit resolvable: {len(missing_credit)}")
        for r in missing_credit[:10]:
            log(f"      {r['name'][:40]:42} {r['image_title'][:50]}")


# ------------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", type=int, choices=[1, 2, 3], help="run a single stage")
    ap.add_argument("--reset", action="store_true", help="discard saved state first")
    args = ap.parse_args()

    if args.reset and os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        log("state discarded")

    if not os.path.exists(CORPUS_PATH):
        sys.exit(f"corpus not found: {CORPUS_PATH}")

    rows = load_corpus()
    state = load_state()
    log(f"corpus: {len(rows)} libraries")

    if args.stage in (None, 1):
        stage1_wikidata(rows, state)
    if args.stage in (None, 2):
        stage2_geosearch(rows, state)

    titles = {"File:" + v for v in state["p18"].values() if v}
    titles |= {g["title"] for g in state["geo"].values()}
    fetch_imageinfo(sorted(titles), state)
    repair_uploaders(state)

    if args.stage in (None, 3):
        stage3_geograph(rows, state)

    report(build_manifest(rows, state))

    if not (os.path.exists(GEOGRAPH_INDEX) or os.path.exists(GEOGRAPH_DUMP)):
        log("")
        log("stage 3 needs the Geograph index. Rebuild it from the dump:")
        log("  curl -o data/gridimage_base.tsv.gz \\")
        log("       https://data.geograph.org.uk/dumps/gridimage_base.tsv.gz   # 235 MB")


if __name__ == "__main__":
    main()
