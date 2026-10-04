# everylibrary

An Every Lot-style Bluesky bot for UK public libraries: one library per post, with name, place and a photograph, working through the whole national network.

Modelled on Neil Freeman's [everylotbot](https://github.com/fitnr/everylotbot), but deliberately **not** using Google Street View. See "Why not Street View" below.

This repo was split out of a private scripts repo on 19 September 2026 (history carried over with `git subtree split`). The scheduled jobs still invoke it as `~/Scripts/everylibrary`, which is a symlink to this checkout, so their paths did not change.

## The corpus

`data/uk_libraries_final.csv`: open public libraries with coordinates. As originally built (3,750 libraries; the monthly roster pass has added a few since):

| | Open | Statutory | Matched to OSM | With Wikidata |
|---|---|---|---|---|
| England | 2,913 | 2,579 | 2,435 | 1,521 |
| Scotland | 478 | 478 | 354 | 126 |
| Wales | 261 | 261 | 177 | 124 |
| Northern Ireland | 98 | 98 | 93 | 28 |
| **Total** | **3,750** | **3,416** | **3,059** | **1,799** |

Built from two sources, joined spatially at a 250 m radius:

- **Roster:** [Libraries Hacked](https://github.com/librarieshacked) `api.librarydata.uk/libraries`, which covers all four nations and ships coordinates. Its English portion derives from ACE's Basic Dataset for Libraries, registered on data.gov.uk by DCMS under the **Open Government Licence**.
- **Geometry and Wikidata links:** OpenStreetMap `amenity=library` via Overpass, **ODbL**.

The two datasets were built independently and agree closely: Scotland 478 vs 470 in OSM, Wales 261 vs 251, Northern Ireland 98 vs 107, and England's statutory count of 2,579 against 2,612 derived separately from the ACE workbook.

⚠️ **Unresolved:** the Libraries Hacked aggregate has no stated data licence (checked the API headers, the map site, the blog, the portal and the homepage). The England-derived portion traces back to OGL, but the Scottish, Welsh and Northern Irish records have no traceable upstream licence. See "Open threads".

The roster was snapshotted on 16 August 2026 and is re-checked and re-applied monthly (see "The roster" below). It is never rebuilt: additions are appended, closures removed and no existing row is rewritten.

## The image pipeline

`everylibrary_images.py`: four stages, descending confidence, all output carrying photographer, licence and credit URL.

1. **Wikidata P18:** a photo attached to the library's own Wikidata item. Highest confidence: a human linked that picture to that building.
2. **Commons geosearch:** Commons files within 250 m whose title mentions a library, scored on title match then distance. Also catches Geograph photos already mirrored onto Commons.
3. **Wikidata neighbours** (stage `2b`): for libraries still without a picture, the nearest Wikidata library item within 250 m that has a P18, whether or not the roster row links to it. Items whose label or description reads as former or closed are dropped, and each item is claimed by the closest library, so two libraries never post the same photograph.
4. **Geograph dump:** `gridimage_base.tsv.gz` (235 MB, keyless) filtered to the ~9,000 photos titled as libraries, matched by proximity. The live Geograph API needs a key; the dump does not.

```bash
python3 everylibrary_images.py              # all stages
python3 everylibrary_images.py --stage 2    # one stage
python3 everylibrary_images.py --reset      # discard state and restart

# stage 3 needs the dump:
curl -o data/gridimage_base.tsv.gz https://data.geograph.org.uk/dumps/gridimage_base.tsv.gz
```

Resumable: state checkpoints to `data/image_state.json` every 100 libraries. Output is `data/uk_libraries_images.csv`.

### Results

The manifest is the source of truth; count `postable == yes` rows in `data/uk_libraries_images.csv` for the current figure. As of the 1 October 2026 monthly pass, **2,246 of 3,752 libraries (60%) have a usable, credited image**, and every row with an image is postable.

| Stage | Found |
|---|---|
| Commons geosearch | 1,340 |
| Wikidata P18 | 750 |
| Geograph dump | 104 |
| Wikidata neighbours (2b) | 52 |

England 1,777/2,914 (61.0%) · Wales 176/262 (67.2%) · Northern Ireland 57/98 (58.2%) · Scotland 236/478 (49.4%).

Licences: 1,669 CC BY-SA 2.0, 218 CC BY 2.0, 171 CC BY-SA 4.0, 100 CC BY-SA 3.0, 44 CC0, 17 public domain, 22 CC BY 3.0/4.0, 5 others.

**Geograph supplies most of this project, but arrives via Commons.** At the original harvest, 80% of the geosearch matches and 55% of the Wikidata ones were Geograph photographs mirrored onto Commons, which is why the direct Geograph stage adds so few: the other stages have already harvested the mirrored material. Run stage 3 anyway, since its finds exist nowhere else.

The sweep is slow from Seoul, roughly one second per Commons call, so a full stage 2 run takes about an hour.

## Licensing

Everything is Creative Commons and **attribution is a condition of use**, not a courtesy. The manifest carries `photographer`, `licence`, `licence_url` and `credit_page` per row, and an `attribution` string short enough to sit in a post.

**The `postable` column is the gate.** It is `yes` only when a row has both an image and a resolvable photographer. An uncredited CC BY-SA image is a licence breach, so the bot posts nothing where `postable` is `no`, even though an image exists.

Commons stores the credit as free-form wikitext in three shapes, all handled in `clean_artist()`:

| Shape | Example | Handling |
|---|---|---|
| A plain name | `Roger Davies` | used as-is |
| Buried in prose | `No machine-readable author provided. Mcginnly assumed (based on copyright claims).` | extract the username; naive bracket-stripping leaves the nonsense string "Mcginnly assumed" |
| Empty | `` | fall back to the Commons uploader |

The second shape is the trap: the name is present, so the image looks credited until you read the output.

Geograph images resolve to the "stamped" variant, which has the credit burned into the image, so attribution survives a screenshot.

**Attribution lives in two places, by design.** Each post names its own photographer and licence, with the name linking to the file's source page: that is the per-image CC obligation and the only credit that survives a reshare. Platform-level credit for DCMS (OGL), Libraries Hacked and OpenStreetMap (ODbL) lives in the **pinned post**, because bios carry no link facets and both licences ask for a link where possible. The bio points at the pinned post rather than repeating it.

**The bio, as of 22 August 2026** (edited by hand on the account; nothing in this repo sets it):

> A 🤖 visiting every public library in the United Kingdom, one at a time. Sources and credits in the pinned post. Image descriptions are written by A.I. Not affiliated with any library service. Run by @stanfordc.bsky.social. 📚

Re-run `everylibrary_post.py --pin` to update the pinned note. It replaces its own previous version and refuses to delete a pinned post it does not recognise.

## Alt text

`everylibrary_describe.py` writes a description for every photograph ahead of time into `data/alt_text.json`. Posting makes no model calls: it reads the stored text. A handful of entries failed both reads and fall back to bare identification.

The alt text deliberately does not repeat the post (name, address, photographer, licence), which already sits visibly above the image.

Every `claude -p` call the describer makes, the verification pass included, runs `--restricted --tools Read` (`CONFINED`), with each image staged alone in its own directory used as the model's cwd (`_staged`). Unconfined, `claude -p` is an agent with a shell; confined, it can read the one image and nothing else. everycarnegie's `carnegie_describe.py` imports this module and is confined with it.

Two sources, kept separate so provenance stays honest:

| | Source |
|---|---|
| **visual** | Generated from the image by `claude -p` (`claude-haiku-4-5`) |
| **context** | The human-written note on the Commons file page, kept only where it says more than the file title (about 700 entries) |

The model is never shown the context, so it cannot launder a human's claim into something it appears to have observed.

**Each source labels itself, at the head of its own section:**

> **A.I.-written description:** Single-storey brick building with hipped slate roof, white trim, and a central cupola… **Note from Wikimedia Commons:** Opposite a church, and next to a cluster of specialist NHS clinics, this small library is in a village south of Maidstone.

Labelling in place rather than only in the bio is the same reasoning that puts the photographer in every post: it is the only signal that survives a reshare. The roster-assembled fallback carries no label, having never been near a model.

Two filters stand between the model and a screen reader:

- **`not_a_description()`** rejects the model talking rather than describing: refusals, and the subtler case where the photograph is not what the prompt promised and the model corrects the brief instead of describing what it sees.
- **Two independent reads, kept only if they agree.** An audit of 20 descriptions found no invention but five that over-specified (eight windows where there are six, a timber fence called metal railings). A disagreement drops the description rather than guessing. It doubles the generation cost and only runs on entries with no description yet.

A dropped description falls back to bare identification, which is honest about knowing nothing.

## The roster: detected monthly, applied monthly

`everylibrary_roster_check.py` detects drift from the live roster and `everylibrary_roster_apply.py` acts on it. **They are deliberately two scripts:** the check is a read-only record of what upstream did, written as a report a person reads; the apply is the action.

```bash
python3 everylibrary_roster_check.py           # report and notify
python3 everylibrary_roster_check.py --json     # machine-readable
```

Runs monthly on the 1st at 10:00 under `com.chrisstanford.everylibraryroster`, writing `~/Library/Logs/everylibrary-roster.md`. A clean run writes nothing and deletes any previous report, so the file existing means there is something to read.

The job runs `everylibrary_monthly.sh`, six steps under the worst-exit pattern (every step runs whatever the last returned, and the exit code is the worst of them):

```bash
everylibrary_roster_check.py               # report the drift
everylibrary_roster_apply.py --live        # add and remove
everylibrary_urls.py --recheck             # re-verify every council link
everylibrary_images.py --recheck-misses    # look again for photographs
everylibrary_images.py --manifest-only     # cheap rebuild, in case the sweep died
everylibrary_describe.py                   # alt text for whatever turned up
```

Three refusals, all tested, because every failure here is silent:

- The API accepts `?offset=` and **ignores it**, returning page 1 forever. Paginate with `?page=`. A sweep that trusted `offset` would produce a complete-looking 1,000-row roster and report mass closures.
- A short fetch is refused rather than reported as drift.
- A broken check exits 2, never 0.

**Disappearance from the endpoint is the closure signal.** The API's `Year closed` field was empty on every live record when checked, so it cannot be relied on. It is still reported, as a tripwire in case it starts being populated.

## What the applier will and will not do

`everylibrary_roster_apply.py` makes two changes to `data/uk_libraries_final.csv` and no others:

- **Adds** libraries the live roster carries and the corpus does not, with blank `osm_id`, `wikidata` and `match_m` (those come from the original Overpass join, which no script here re-runs). The image stages key on `osm_id or lat,lon`, so a blank OSM column costs nothing.
- **Removes** libraries that have vanished from the roster and were **never posted**. A posted library is never removed. Without `post_state.json` it cannot tell, so it removes nothing and says so.

**Append-only is the safety argument.** An existing row is never rewritten, so no `library_id` can move underneath the posted state.

Nation comes from the ONS authority-code prefix (`E`/`S`/`W`/`N`), which matched the corpus nation for every original record. An unrecognised prefix is refused rather than guessed, as is an unparseable coordinate, a nameless record and an addition whose `library_id` is already in use. A run touching more than 40 rows is **refused**: a bad read of the API arrives looking exactly like data.

### A new library usually arrives with no coordinates

New roster records often carry `null` in every latitude and longitude field, including the UPRN ones. Missing coordinates are resolved from the postcode through **postcodes.io** (ONS data, Open Government Licence, no key, one bulk call). Postcode-derived positions are about 45 m out against a 250 m search radius.

**An unresolvable postcode is refused, never guessed:** a wrong position attaches a library to a photograph of somewhere else, which is the one error a reader cannot detect.

## Looking again: the monthly re-sweep

Every image stage memoises its *misses*, so a plain re-run would never find a photograph uploaded later:

| Stage | Memo | Effect on a second run |
|---|---|---|
| 1 · Wikidata P18 | `state["p18"]` stores a miss as `null` | never re-asks that QID |
| 2 · Commons geosearch | `state["geosearch_done"]` | never re-sweeps that library, hit or miss |
| 2b · Wikidata neighbours | the whole item list cached in `state["wd_items"]` | re-matches against a frozen set of items |
| 3 · Geograph | `state["geograph"]` stores a miss as `None` (in an `else` branch, easy to overlook) | never re-examines that library |

`--recheck-misses` expires those memos, and the monthly pass runs it. Expect a small yield: the sweep can only find what somebody has photographed and uploaded, so the value is cumulative rather than a windfall.

### A library that already has a photograph is never looked at again

This is a correctness rule, not a saving. The sources are **ranked** (`wikidata-p18` beats `commons-geosearch`, which beats `geograph` and `wikidata-nearby`), and `alt_text.json` is keyed **by library, not by image**. A newly found higher-ranked photograph would replace the incumbent and keep the description written for the picture it displaced: a confident description of a building nobody is looking at, which `describe.py` would never revisit since it only fills empty entries.

Measured before this ran in production: re-asking Wikidata about every miss returned 140 new P18 photographs, of which only three belonged to a library that had none. The other 137 would each have swapped a described picture for an undescribed one.

So `expire_misses()` protects anything `resolve_source()` says already has a picture, and `resolve_source()` is the same function `build_manifest` uses, so the two cannot disagree about what "already has a picture" means. Two corollaries:

- **The sweep memo is keyed on whether the library ended up with a picture, not on whether the stage recorded a hit.** A geosearch hit whose Commons file has since been deleted would otherwise strand that library forever.
- **A better photograph for a library that already has one is out of scope**, deliberately: it would mean re-describing, and the description is the expensive part.

### What a grown corpus does to the queue

`next_library()` keeps the running order in `post_state.json` and appends anything new, shuffled among itself, to the end, so a grown corpus cannot reshuffle the queue or re-post anything. The consequence is that **a library found on a later sweep goes to the back** of a roughly two-year rotation.

### Geograph is annual, not monthly

Stage 3 needs the 235 MB dump re-downloaded and 8.3 million rows rescanned, and yields little because the other stages harvest the mirrored copies first. `everylibrary_geograph_annual.sh` runs it once a year on 16 August under `com.chrisstanford.everylibrarygeograph`. Two non-obvious requirements:

- `extract_geograph_index()` returns the cached JSON whenever it exists, so a refresh means **deleting the index**, and only after a good download has landed, or a failed fetch would leave stage 3 with nothing.
- It relies on a monthly `--recheck-misses` having cleared stage 3's memoised misses; without that the job is inert.

## Identity: `library_id`

`sha1(norm(name, postcode))`, twelve hex characters. It keys **both** `alt_text.json` and `post_state.json`, so changing it means migrating both, on every machine.

It deliberately excludes coordinates: an upstream coordinate correction would otherwise mint a new id and re-post the library. `norm()` folds case and whitespace and is defined in the poster and imported by the roster check, so the two cannot drift apart. Name and postcode alone are unique across the corpus; `test_everylibrary_roster_apply.py` re-checks that against the live data.

`everylibrary_migrate_ids.py` did the one-off re-keying from the old latitude-based id. It refuses on a collision or a file holding a mix of old and new ids, and is idempotent.

## Tests

```bash
python3 -m unittest
```

Stdlib only: `atproto` and `requests` are stubbed where not installed, and nothing reaches the network.

**The refusal cases are the point.** Every failure mode here is silent: a library placed at the wrong coordinates gets a photograph of somewhere else and reads as an ordinary post, a memo left unexpired makes the monthly sweep find nothing and report a clean run forever. So the suite asserts the refusals, and pins the source ranking that the protection above depends on.

## Why not Street View

The Google Maps Platform Terms of Service, §3.2.3(a) "No Scraping", prohibit exporting or scraping Maps Content for use outside the Services, and the enumerated examples explicitly include pre-fetching, storing, resharing or rehosting that content, and bulk downloading Street View images. A bot that downloads Street View images, stores them and reposts them to Bluesky does all of those things.

Related: §3.2.3(b) permits caching only where the service-specific terms allow, and the Street View policy exempts **panorama IDs only**, not imagery. §3.2.3(e) separately bars showing Street View imagery alongside a non-Google map, which would rule out pairing each post with an OSM locator.

The long-running everylot bots demonstrate non-enforcement, not permission.

## Posting

```bash
python3 everylibrary_post.py             # post one
python3 everylibrary_post.py --dry-run   # print it, post nothing, write no state
python3 everylibrary_post.py --count 3   # catch up
python3 everylibrary_post.py --pin       # rewrite the pinned credits note
```

**Three a day**, at 17:00, 21:00 and 01:00 Asia/Seoul under `com.chrisstanford.everyuklibrary`. The times are picked by London clock, because the audience is British: 09:00, 13:00 and 17:00 UK during BST, an hour earlier once GMT returns.

The rotation is the postable libraries only, so three a day runs about two years. Order is a fixed shuffle seeded on `SHUFFLE_SEED`, so the feed does not march through one county at a time. Posted ids live in `data/post_state.json`.

Each post carries the name, address, place, the photographer linked to the file's source page, and the licence in plain text. Where the roster has one, it reads **`library since 1820`** rather than `opened 1820`: the field records when the library began at that address, not when the building went up (Idea Store Bow says 2002 beside a red-brick Victorian building), and the field carries no definition anywhere findable.

`login_client()` retries the Bluesky login four times with linear backoff, to ride out brief network blips. **Only the login retries.** A timeout on `send_images` cannot distinguish a post that never landed from one that landed with the response lost, and retrying the second case posts the library twice.

## Implementation notes

- Uses `requests`, not `urllib`: `urllib` fails certificate verification on this Python install.
- Geograph filenames embed an unguessable hash, so stage 3 reads each URL from the photo page's `og:image` tag.
- `everylibrary_post.py` uses argparse, so a mistyped flag is rejected instead of silently posting live.
- ⚠️ **`save_alt()` writes the whole dict from memory**, so two concurrent `everylibrary_describe.py` runs end with whichever saves last winning, silently. Check nothing else is running before starting one.
- `data/_tmp/` exists because Claude Code's Read tool refuses paths outside its working directory, and its polite refusal ("I don't have a tool available to read image files") is exactly the shape of a sentence that could be stored as a description by mistake.

## Prior art on Bluesky

Checked August 2026: **no UK library bot existed.**

- The everylot family is entirely North American: Chicago, USPS, Milwaukee, Baltimore, Montréal, Richmond, Cleveland, Philadelphia, Detroit, St Louis, Houston, Charlottesville. Nothing British, nothing library-themed.
- `library-bot.bsky.social` posted random libraries worldwide sourced from Google Maps, with star ratings and `maps.google.com` links. Dormant since 16 January 2025 at 98 posts.
- `masslibraryproject.bsky.social` is people visiting Massachusetts town libraries, not a bot.
- `norfolklibrariesuk.bsky.social` and `publiclibraries.bsky.social` are library-sector accounts, not comparable projects.

## Open threads

- **Confirm the Libraries Hacked data licence**, which covers the roster's Scottish, Welsh and Northern Irish libraries (about a fifth of the rotation). The enquiry to Dave Rowe went out in August 2026 and asks about all four nations. The photographs are unaffected (separately licensed from Commons and Geograph); only names, addresses and coordinates are in question, and most of the Scottish and Welsh entries also exist in OpenStreetMap under ODbL.

  The decision is to keep all four nations meanwhile. If the answer is no, the fallback is `EXCLUDE_NATIONS = {'Scotland', 'Wales', 'Northern Ireland'}` in `everylibrary_post.py`, which drops them from the rotation with no rebuild.
- Decide whether to include the 71 independent community libraries, which sit outside the statutory service.
- Closure history is England-heavy: 470 English closures on record against 6 Scottish and 2 Welsh, yet SLIC has separately verified 53 Scottish closures between 2014 and 2024. Don't imply national coverage on the closure angle.
