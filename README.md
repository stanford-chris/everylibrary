# everylibrary

An Every Lot–style Bluesky bot for UK public libraries: one library per post, name, place and a photograph, working through the whole national network.

Modelled on Neil Freeman's [everylotbot](https://github.com/fitnr/everylotbot), but deliberately **not** using Google Street View. See “Why not Street View” below.

## The corpus

`data/uk_libraries_final.csv` — 3,750 open public libraries with coordinates.

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

Cross-check: the two datasets were built independently and agree closely. Scotland 478 vs 470 in OSM, Wales 261 vs 251, Northern Ireland 98 vs 107, and England's statutory count of 2,579 against 2,612 derived separately from the ACE workbook.

⚠️ **Unresolved:** the Libraries Hacked aggregate has no stated data licence. Checked the API headers, the map site, the blog, the portal and the Libraries Hacked homepage. The England-derived portion traces back to OGL, but the Scottish, Welsh and Northern Irish records (837 libraries) have no traceable upstream licence. Ask Dave Rowe before publishing anything covering those.

This previously said “the Scottish and Welsh records (739 libraries)”, which quietly dropped Northern Ireland's 98. They arrive through the same aggregate with the same unstated licence, and nothing here ever recorded a finding that they were separately covered. The enquiry as sent does ask about all four nations; it was only this scope line, and the fallback below, that were narrower than the question.

The roster was snapshotted on 16 August 2026. It is re-checked and re-applied monthly from 23 August 2026: see “The roster: detected monthly, applied monthly” below. It is still not rebuilt — additions are appended and closures removed, and no existing row is ever rewritten.

## The image pipeline

`everylibrary_images.py` — four stages, descending confidence, all output carrying photographer, licence and credit URL.

1. **Wikidata P18** — a photo attached to the library's own Wikidata item. Highest confidence: a human linked that picture to that building.
2. **Commons geosearch** — Commons files within 250 m whose title mentions a library, scored on title match then distance. Also catches Geograph photos already mirrored onto Commons.
3. **Wikidata neighbours** (stage `2b`) — for libraries still without a picture, the nearest Wikidata library item within 250 m that has a P18, whether or not the roster row links to it. Stage 1 needs the roster to carry the QID; this one reaches the same photographs by position. Items whose label or description reads as former or closed are dropped, and each item is claimed by whichever library is closest, so two libraries never post the same photograph.
4. **Geograph dump** — `gridimage_base.tsv.gz` (235 MB, keyless) filtered to the ~9,000 photos titled as libraries, matched by proximity. The live Geograph API needs a key; the dump does not.

```bash
python3 everylibrary_images.py              # all stages
python3 everylibrary_images.py --stage 2    # one stage
python3 everylibrary_images.py --reset      # discard state and restart

# stage 3 needs the dump:
curl -o data/gridimage_base.tsv.gz https://data.geograph.org.uk/dumps/gridimage_base.tsv.gz
```

Resumable: state checkpoints to `data/image_state.json` every 100 libraries. Output is `data/uk_libraries_images.csv`.

### Results, full run 16 August 2026, plus stage 2b on 18 August

**2,238 of 3,750 libraries have a usable, credited image (59.7%).** All 2,238 are `postable`; none are blocked for a missing credit.

| Stage | Found |
|---|---|
| Commons geosearch | 1,338 |
| Wikidata P18 | 747 |
| Geograph dump | 103 |
| Wikidata neighbours (2b) | 50 |

England 1,773/2,913 (60.9%) · Wales 175/261 (67.0%) · Northern Ireland 57/98 (58.2%) · Scotland 233/478 (48.7%).

Licences: 1,665 CC BY-SA 2.0, 218 CC BY 2.0, 169 CC BY-SA 4.0, 99 CC BY-SA 3.0, 44 CC0, 17 public domain, 21 CC BY 3.0/4.0, 5 others.

The first run reported 2,188 at 58.3%; stage 2b added the other 50 two days later. **The counts above are the manifest as it stands, not the run log** — earlier versions of this section quoted the 16 August figures for a fortnight after they stopped being true.

**Geograph supplies most of this project, but arrives via Commons.** 80% of the geosearch matches and 55% of the Wikidata ones are Geograph photographs mirrored onto Commons. That is why the direct Geograph stage adds only 103: by the time it runs, the other two stages have already harvested the mirrored material, with attribution normalised into Commons metadata on the way. Run stage 3 anyway — those 103 exist nowhere else — but expect it to be the tail, not the trunk.

Note the sweep is slow from Seoul, roughly one second per Commons call, so a full stage 2 run takes about an hour.

## Licensing

Everything is Creative Commons and **attribution is a condition of use**, not a courtesy. The manifest carries `photographer`, `licence`, `licence_url` and `credit_page` per row, and an `attribution` string short enough to sit in a post.

**The `postable` column is the gate.** It is `yes` only when a row has both an image and a resolvable photographer. An uncredited CC BY-SA image is a licence breach, so the bot must post nothing where `postable` is `no`, even though an image exists.

Commons stores the credit as free-form wikitext and it arrives in three shapes, all handled in `clean_artist()`:

| Shape | Example | Handling |
|---|---|---|
| A plain name | `Roger Davies` | used as-is |
| Buried in prose | `No machine-readable author provided. Mcginnly assumed (based on copyright claims).` | extract the username; naive bracket-stripping leaves the nonsense string “Mcginnly assumed” |
| Empty | `` | fall back to the Commons uploader |

That second shape is the trap. The name is present, so the image looks credited until you read the output.

Geograph images resolve to the “stamped” variant, which has the credit burned into the image, so attribution survives a screenshot.

**Attribution lives in two places, by design.** Each post names its own photographer and licence, with the name linking to the file's source page: that is the CC obligation, it is per-image, and it is the only credit that survives a reshare. Platform-level credit for DCMS (OGL), Libraries Hacked and OpenStreetMap (ODbL) lives in the **pinned post**, because bios carry no link facets and both licences ask for a link where possible. The bio points at the pinned post rather than repeating it.

**The bio, as of 22 August 2026:**

> A 🤖 visiting every public library in the United Kingdom, one at a time. Sources and credits in the pinned post. Image descriptions are written by A.I. Not affiliated with any library service. Run by @stanfordc.bsky.social. 📚

Nothing in this repo sets it. It is edited by hand on the account, so this line is the only record of what it says and the only way a later session can notice drift. Note that the A.I. disclosure now sits here as well as at the head of every description. The in-post label is still the one that matters, for the reason given under “Alt text” below.

Re-run `everylibrary_post.py --pin` to update that note. It replaces its own previous version and refuses to delete a pinned post it does not recognise.

## Alt text

`everylibrary_describe.py` writes a description for every photograph, ahead of time, into `data/alt_text.json`. All 2,238 postable libraries have an entry, and 2,236 of those carry a description: two failed both reads and fall back to bare identification. Posting makes no model calls: it reads the stored text.

The bot's original alt text repeated the post — name, address, photographer, licence — all of which sits visibly above the image already, so a screen-reader user heard the caption twice and learned nothing about the building.

Two sources, kept separate so provenance stays honest:

| | Source | Count |
|---|---|---|
| **visual** | Generated from the image by `claude -p` (`claude-haiku-4-5`) | 2,236 |
| **context** | The human-written note on the Commons file page, kept only where it says more than the file title | 687 |

The model is never shown the context, so it cannot launder a human's claim into something it appears to have observed.

**Each source labels itself, at the head of its own section:**

> **A.I.-written description:** Single-storey brick building with hipped slate roof, white trim, and a central cupola… **Note from Wikimedia Commons:** Opposite a church, and next to a cluster of specialist NHS clinics, this small library is in a village south of Maidstone.

Labelling in place rather than in the bio is the same reasoning that puts the photographer in every post: it is the only signal that survives a reshare, and someone meeting a single reposted photograph otherwise has no way to know no human ever looked at it. A single closing note was tried first and abandoned — it said both sources were present but never where one stopped and the other started, so a model's observation and a human's research arrived as one paragraph. No model can tell from a photograph that the neighbours are NHS clinics.

The roster-assembled fallback carries no label, having never been near a model.

Two filters stand between the model and a screen reader, both because the naive version shipped first:

- **`not_a_description()`** rejects the model talking rather than describing — refusals, and the subtler case where the photograph is not what the prompt promised and the model corrects the brief instead of describing what it can see. That failure is fluent, confident prose, and one instance would have published a path on this Mac to Bluesky.
- **Two independent reads, kept only if they agree.** Auditing 20 descriptions against their photographs found no invention but five that misled, all over-specifying: eight windows where there are six, a timber fence called metal railings, knitted remembrance poppies on a memorial cross described as burgundy ivy. A disagreement drops the description rather than guessing. It doubles the generation cost, and only ever runs on entries that have no description yet, so nothing already written is at risk.

A dropped description falls back to bare identification. That is honest about knowing nothing, which a fabrication is not — and a wrong name is the one error a listener cannot detect.

## The roster: detected monthly, applied monthly

`data/uk_libraries_final.csv` was a frozen snapshot until 23 August 2026. Over a two-year rotation that meant the bot would eventually post a library that had closed, and would never post one that opened afterwards.

`everylibrary_roster_check.py` detects the drift and `everylibrary_roster_apply.py` acts on it. **They are deliberately two scripts.** The check is the record of what upstream did and writes a report a person reads; the apply is the action. Folded together, the only account of a closure would be the fact that a row had gone. The check remains strictly read-only: it never writes to the corpus, the manifest, the post state or the alt text.

```bash
python3 everylibrary_roster_check.py           # report and notify
python3 everylibrary_roster_check.py --json     # machine-readable
```

Runs monthly on the 1st at 10:00 under `com.chrisstanford.everylibraryroster`, writing `~/Library/Logs/everylibrary-roster.md`. A clean run writes nothing and deletes any previous report, so the file existing means there is something to read.

`everylibrary_monthly.sh` is what that job actually runs, and it is six steps under the worst-exit pattern — every step runs whatever the last one returned, and the exit code is the worst of them:

```bash
everylibrary_roster_check.py               # report the drift
everylibrary_roster_apply.py --live        # add and remove
everylibrary_urls.py --recheck             # re-verify every council link
everylibrary_images.py --recheck-misses    # look again for photographs
everylibrary_images.py --manifest-only     # cheap rebuild, in case the sweep died
everylibrary_describe.py                   # alt text for whatever turned up
update_colophon_library_count.py --dry-run # report the site's figures drifting
```

⚠️ **The colophon step reports and never writes.** `update_colophon_library_count.py` commits and pushes to the site repo by default, and an unattended job must not: the change would sit committed and undeployed until someone ran `site_deploy.sh`. Until the monthly pass existed the colophon could only go stale when a harvest was run by hand; now it drifts every month, so the drift is at least named in the log.

**Detecting rather than refreshing was deliberate, and the reason has since been removed.** `library_id` was `sha1(name|postcode|lat)`, so an upstream coordinate correction minted a new id, the library read as never-posted and would have gone out twice. That was fixed on 23 August 2026: the latitude is gone from the key and `library_id` and the check's match key are now one function, `norm()`, imported from the poster. See “What the applier will and will not do” below for what replaced the refresh — which is still not a rebuild.

Three refusals, all tested, because every failure here is silent:

- The API accepts `?offset=` and **ignores it**, returning page 1 forever. Paginate with `?page=`. A sweep that trusted `offset` would produce a confident, complete-looking 1,000-row roster and report 1,188 mass closures.
- A short fetch is refused rather than reported as drift.
- A broken check exits 2, never 0. A check that could not run is not a pass.

**Disappearance from the endpoint is the closure signal.** The API carries a `Year closed` field, but it is empty on all 3,753 live records, so it cannot be relied on to mean anything. It is still reported, as a tripwire for it starting to be populated.

First run, 17 August 2026: three additions (Stratford-upon-Avon, Dafen, Pontyates), no closures.

## What the applier will and will not do

`everylibrary_roster_apply.py` makes two changes to `data/uk_libraries_final.csv` and no others:

- **Adds** libraries the live roster carries and the corpus does not, with blank `osm_id`, `wikidata` and `match_m`. Those come from the Overpass spatial join that built the original corpus, and no script here re-runs it. The image stages key on `osm_id or lat,lon`, so a blank OSM column costs nothing, and stage 2b reaches Wikidata photographs by position rather than by link.
- **Removes** libraries that have vanished from the roster and were **never posted**. Disappearance is the closure signal. A posted library is never removed: the post exists, and dropping the row would only lose the record of it. Without `post_state.json` it cannot tell, so it removes nothing and says so.

**Append-only is the whole safety argument.** An existing row is never rewritten, so no `library_id` can move underneath the posted state — verified on the first live run: all 3,750 existing rows came back byte-identical. A full rebuild would re-derive every row to gain three, and needs a script that does not exist.

Nation comes from the ONS authority-code prefix. Verified 23 August 2026 against all 3,750 matched records: `E`/`S`/`W`/`N` predicts the corpus nation with no exceptions. An unrecognised prefix is refused rather than guessed at, as is an unparseable coordinate, a nameless record, and an addition whose `library_id` is already in use.

A run touching more than 40 rows is **refused**, not applied. That is the `?offset=` lesson in another place: a bad read of the API arrives looking exactly like data.

### ⚠️ A new library usually arrives with no coordinates

All three additions the first check found carry `null` in **every** latitude and longitude field the API has, including the two UPRN ones. A library with no position cannot be matched to a photograph by any stage here, so without a fix this step would add nothing at all, ever.

Missing coordinates are therefore resolved from the postcode through **postcodes.io** (ONS data, Open Government Licence, no key, one bulk call however many arrive). That is the same kind of figure the roster already ships: `images.py`'s own radius comment puts postcode-derived coordinates about 45 m out against a 250 m search radius.

It recovers **one of the three**. Dafen and Pontyates are refused because their postcodes are malformed rather than merely absent — `SA15 5SC` uses a letter the final pair excludes, and neither exists in ONS data or in the terminated-postcode list. **An unresolvable postcode is refused, never guessed:** a wrong position attaches a library to a photograph of somewhere else, which is the one error a reader cannot detect.

## Looking again: the monthly re-sweep

Until 23 August 2026 **nothing ever looked twice.** The image stages had run on 16 and 18 August and no job re-ran them; the monthly pass called `--manifest-only`, which fetches nothing by design. A photograph uploaded to Commons in September could never reach the bot.

⚠️ **A plain re-run would not have helped either, and that is the part worth knowing.** Every stage memoises its *misses*, permanently:

| Stage | Memo | Effect on a second run |
|---|---|---|
| 1 · Wikidata P18 | `state["p18"]` stores a miss as `null` — “record misses too, so we don't refetch” | never re-asks that QID |
| 2 · Commons geosearch | `state["geosearch_done"]` | never re-sweeps that library, hit or miss |
| 2b · Wikidata neighbours | the whole item list cached in `state["wd_items"]` | re-matches against a frozen set of Wikidata items |
| 3 · Geograph | `state["geograph"]` stores a miss as `None` | never re-examines that library |

`--recheck-misses` expires those memos. It runs monthly, and it is the reason the pass exists in its current form.

⚠️ **Stage 3's memo was missed on the first pass at this**, and the shape of the mistake is worth keeping: its miss is written as an explicit `None` in an `else` branch, so reading the function top-down it looks like only hits are stored. This README asserted the opposite for several hours. The consequence would have been the annual Geograph job re-downloading 235 MB, rescanning 8.3 million rows and skipping all 1,561 libraries it had already looked at — the exact failure the monthly sweep exists to fix, left in the one stage that runs least often and would therefore have taken a year to notice. The tell was in `stage3_geograph` itself: `taken = {v["id"] for v in state["geograph"].values() if v}` has an `if v` guard, which only makes sense if falsy values are stored.

### What the first full sweep actually returned

Run against real state on 23 August 2026, seven days after the original harvest:

| | |
|---|---|
| Postable before | 2,238 |
| Postable after | **2,243** |
| Photographs **swapped** | **0** |
| Libraries that gained one | 4 (3 Wikidata P18, 1 Wikidata neighbour) |
| New Commons geosearch hits, from 1,510 re-swept | 1 |
| New rows from the roster | 1 (Stratford-upon-Avon, which then matched a Commons photograph by Lewis Clarke) |

**Be realistic about the yield.** Five libraries in a week. Almost nobody photographs a UK library and uploads it in seven days, and that is the honest expectation for month one — the sweep can only find what somebody has published. The value is cumulative: this is the difference between a rotation frozen at 2,238 for two years and one that grows, not a windfall. The 1,508 libraries still without a picture are still without a picture.

### ⚠️ A library that already has a photograph is never looked at again

Not a saving: a correctness rule, and the sharpest thing in this pipeline.

The sources are **ranked**. `wikidata-p18` beats `commons-geosearch`, which beats `geograph` and `wikidata-nearby`. And `alt_text.json` is keyed **by library, not by image**. So a newly discovered higher-ranked photograph replaces the incumbent and keeps the description written for the picture it displaced — a fluent, confident description of a building nobody is looking at, served to screen-reader users, with nothing downstream able to notice. `describe.py` only fills entries that have no text at all.

`build_manifest` already refused to rank stage 2b above Geograph for exactly this reason. Expiring the misses reintroduced the same hazard through the front door.

**Measured against the real state file, before any of it ran in production:** re-asking Wikidata about every miss returned **140 new P18 photographs, of which three belonged to a library that had none.** The other 137 would each have swapped a described picture for an undescribed one.

So `expire_misses()` protects anything `resolve_source()` says already has a picture — and `resolve_source()` is the same function `build_manifest` uses, extracted so the two cannot drift into disagreeing about what “already has a picture” means. The re-run confirmed it: 2,238 libraries left alone, and stage 1 returned exactly the three.

Two corollaries:
- **The sweep memo is keyed on whether the library ended up with a picture, not on whether the stage recorded a hit.** A geosearch hit whose Commons file has since been deleted leaves `state["geo"]` populated and `resolve_source` returning nothing; keying on the hit would strand that library with no photograph for ever. A test caught this.
- **A better photograph for a library that already has one is out of scope**, deliberately. Getting one means re-describing, and the description is the expensive part.

### What a grown corpus does to the queue

`next_library()` keeps the running order in `post_state.json` and appends anything new, shuffled among itself, to the end, so a grown corpus cannot reshuffle the queue or re-post anything already out. The consequence to weigh is that **a library found on a later sweep goes to the back**: a picture uploaded next month posts in 2028.

### Geograph is annual, not monthly

Stage 3 needs a 235 MB dump re-downloaded and 8.3 million rows rescanned, and it has supplied 103 photographs in total, because the other stages harvest the mirrored copies first. `everylibrary_geograph_annual.sh` runs it once a year under `com.chrisstanford.everylibrarygeograph`, on 16 August, the anniversary of the original harvest. Two things it must do that are not obvious:
- ⚠️ `extract_geograph_index()` returns the cached JSON whenever it exists, so a refresh means **deleting the index**, not just re-downloading the dump — and the index is deleted only after a good download has landed, or a failed fetch would leave stage 3 with nothing at all.
- ⚠️ It runs after a monthly `--recheck-misses` has cleared stage 3's 1,561 memoised misses. Without that the whole job is inert.

## Identity: `library_id`

`sha1(norm(name, postcode))`, twelve hex characters. It keys **both** `alt_text.json` and `post_state.json`, so changing it means migrating both, on every machine.

⚠️ **It hashed the latitude until 23 August 2026**, which made identity depend on a number the upstream roster corrects: a coordinate refined by a metre minted a new id, the library read as never-posted, and it would have gone out a second time. That single fact is why the roster was left frozen for a week. `everylibrary_roster_check.py` had always matched on name and postcode instead, with the reason in its docstring; the two are now one function, `norm()`, defined in the poster and imported by the check, so they cannot drift apart. `norm()` also folds case and whitespace, which the old id did not, so an upstream tidy of a name no longer re-keys a library either.

Verified against all 3,750 rows: name and postcode alone are unique, normalised or not. `test_everylibrary_roster_apply.py` re-checks that against the live corpus, because it is now a property of the data rather than of the hash.

`everylibrary_migrate_ids.py` does the re-keying. It refuses on a collision and on a file holding a mix of old and new ids, and it is idempotent. Run 23 August 2026: 2,238 alt-text entries here, and 21 posted plus 2,238 ordered on the Mini. Done at 21 posts because that is a migration you can check by eye.

## Tests

```bash
python3 test_everylibrary_roster_apply.py     # 44 tests, stdlib only
```

`atproto` and `requests` are stubbed at import, so this runs on either machine. Nothing reaches the network.

**The refusal cases are the point.** Every failure mode in this pipeline is silent: a library placed at the wrong coordinates gets a photograph of somewhere else and reads as an ordinary post, a memo left unexpired makes the monthly sweep find nothing and report a clean run for ever, an invented `osm_id` fabricates a match nobody made. So the suite asserts the refusals, and the source ranking is pinned because the protection above is pointless if a lower-ranked source cannot in fact be displaced.

Verified by mutation, not by a green run. Eleven deliberate breakages were each confirmed to fail the suite and to pass again on restore — and two of them exposed real gaps rather than confirming coverage: removing the P18 preference passed until a ranking test existed, and a geosearch hit whose Commons file has since been deleted was being treated as an illustrated library and could never be re-swept.

## Why not Street View

The Google Maps Platform Terms of Service, §3.2.3(a) “No Scraping”, prohibit exporting or scraping Maps Content for use outside the Services, and the enumerated examples explicitly include pre-fetching, storing, resharing or rehosting that content, and bulk downloading Street View images. A bot that downloads Street View images, stores them and reposts them to Bluesky does all of those things.

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

The rotation is the 2,238 postable libraries, not the full 3,750, so three a day runs **about two years**; two a day ran three. Order is a fixed shuffle seeded on `SHUFFLE_SEED`, so the feed does not march through one county at a time but the sequence survives a corpus rebuild. Posted ids live in `data/post_state.json`.

Each post carries the name, address, place, the photographer linked to the file's source page, and the licence in plain text. Where the roster has one, it reads **`library since 1820`** rather than `opened 1820`: the field records when the library began at that address, not when the building went up, and 32 of the 191 entries dated 2000 or later are described in plainly period terms. Idea Store Bow says 2002 beside a red-brick Victorian building. “Library since” is true under either reading, which matters because the field carries no definition anywhere findable.

`login_client()` retries the Bluesky login four times with linear backoff. The first scheduled run, on 17 August, built its post, fetched the image and then died without posting when the `getProfile` leg inside `login()` timed out — the same brief network blip that has cost the other bots here posts. **Only the login retries.** A timeout on `send_images` cannot distinguish a post that never landed from one that landed with the response lost, and retrying the second case posts the library twice.

## Implementation notes

- Uses `requests`, not `urllib`: `urllib` fails certificate verification on this Python install.
- Geograph filenames embed an unguessable hash, so stage 3 reads each URL from the photo page's `og:image` tag.
- Postcode geocoding, where needed, went through [postcodes.io](https://postcodes.io) (98.9% resolution).
- `everylibrary_post.py` uses argparse rather than the `'--dry-run' in sys.argv` test the older bots here use, so a mistyped flag is rejected instead of silently posting live.
- ⚠️ **`save_alt()` writes the whole dict from memory**, so two concurrent `everylibrary_describe.py` runs end with whichever saves last winning, silently and with no error. This nearly bit on 17 August when two sessions overlapped. Check nothing else is running before starting one.
- `data/_tmp/` exists because Claude Code's Read tool refuses paths outside its working directory. A tempfile in `/var/folders` returns exit 0 and a courteous “I don't have a tool available to read image files”, which is exactly the shape of a sentence that could be stored as a description by mistake.

## Prior art on Bluesky

Checked August 2026: **no UK library bot exists, and the niche is clear.**

- The everylot family is entirely North American: Chicago, USPS, Milwaukee, Baltimore, Montréal, Richmond, Cleveland, Philadelphia, Detroit, St Louis, Houston, Charlottesville. Nothing British, nothing library-themed.
- `library-bot.bsky.social` posted random libraries worldwide sourced from Google Maps, with star ratings and `maps.google.com` links. Dormant since 16 January 2025 at 98 posts.
- `masslibraryproject.bsky.social` is people visiting Massachusetts town libraries, not a bot.
- `norfolklibrariesuk.bsky.social` and `publiclibraries.bsky.social` are library-sector accounts, not comparable projects.

## Open threads

- **Confirm the Libraries Hacked data licence**, which covers the roster's 465 postable Scottish, Welsh and Northern Irish libraries (21% of the rotation). The photographs are unaffected: they are separately licensed from Commons and Geograph. Only names, addresses and coordinates are in question, and 86% of the Scottish and 83% of the Welsh entries also exist in OpenStreetMap under ODbL, so Libraries Hacked is not the only possible source for most of them.

  If the answer comes back no, the fallback is `EXCLUDE_NATIONS = {'Scotland', 'Wales', 'Northern Ireland'}` in `everylibrary_post.py`, dropping the rotation from 2,238 to 1,773 with no rebuild. **The decision as of 17 August 2026 is to keep all four nations**, so this is a contingency and not a plan.

  Note the fallback previously read `{'Scotland', 'Wales'}`, which would have left 57 Northern Irish libraries posting under precisely the licence being reacted to.

  ⏳ **No reply as of 23 August 2026**, a week after the enquiry went out. The bot has been posting Scottish, Welsh and Northern Irish libraries throughout: 21 posts in, that is a handful of records, but the count grows by three a day and the decision to keep all four nations was taken before the question had been outstanding this long.
- Decide whether to include the 71 independent community libraries, which sit outside the statutory service.
- ✅ **Resolved 23 August 2026: the image sweep runs monthly.** See “Looking again”. The open part of it is the 1,512 libraries still outside the rotation for want of a picture — the sweep can only find what someone has photographed and uploaded.
- Closure history is England-heavy: 470 English closures on record against 6 Scottish and 2 Welsh, yet SLIC has separately verified 53 Scottish closures between 2014 and 2024. Don't imply national coverage on the closure angle.
