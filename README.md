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

⚠️ **Unresolved:** the Libraries Hacked aggregate has no stated data licence. Checked the API headers, the map site, the blog, the portal and the Libraries Hacked homepage. The England-derived portion traces back to OGL, but the Scottish and Welsh records (739 libraries) have no traceable upstream licence. Ask Dave Rowe before publishing anything covering those.

## The image pipeline

`everylibrary_images.py` — three stages, descending confidence, all output carrying photographer, licence and credit URL.

1. **Wikidata P18** — a photo attached to the library's own Wikidata item. Highest confidence: a human linked that picture to that building.
2. **Commons geosearch** — Commons files within 250 m whose title mentions a library, scored on title match then distance. Also catches Geograph photos already mirrored onto Commons.
3. **Geograph dump** — `gridimage_base.tsv.gz` (235 MB, keyless) filtered to the ~9,000 photos titled as libraries, matched by proximity. The live Geograph API needs a key; the dump does not.

```bash
python3 everylibrary_images.py              # all stages
python3 everylibrary_images.py --stage 2    # one stage
python3 everylibrary_images.py --reset      # discard state and restart

# stage 3 needs the dump:
curl -o data/gridimage_base.tsv.gz https://data.geograph.org.uk/dumps/gridimage_base.tsv.gz
```

Resumable: state checkpoints to `data/image_state.json` every 100 libraries. Output is `data/uk_libraries_images.csv`.

### Results, full run 16 August 2026

**2,188 of 3,750 libraries have a usable, credited image (58.3%).** All 2,188 are `postable`; none are blocked for a missing credit.

| Stage | Found |
|---|---|
| Commons geosearch | 1,338 |
| Wikidata P18 | 747 |
| Geograph dump | 103 |

England 1,747/2,913 (60.0%) · Wales 158/261 (60.5%) · Northern Ireland 57/98 (58.2%) · Scotland 226/478 (47.3%).

Licences: 1,639 CC BY-SA 2.0, 216 CC BY 2.0, 162 CC BY-SA 4.0, 91 CC BY-SA 3.0, 41 CC0, 16 public domain, 19 CC BY 3.0/4.0.

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

Credit in the bot bio: DCMS, OpenStreetMap contributors, Libraries Hacked, and Wikimedia Commons / Geograph photographers.

## Why not Street View

The Google Maps Platform Terms of Service, §3.2.3(a) “No Scraping”, prohibit exporting or scraping Maps Content for use outside the Services, and the enumerated examples explicitly include pre-fetching, storing, resharing or rehosting that content, and bulk downloading Street View images. A bot that downloads Street View images, stores them and reposts them to Bluesky does all of those things.

Related: §3.2.3(b) permits caching only where the service-specific terms allow, and the Street View policy exempts **panorama IDs only**, not imagery. §3.2.3(e) separately bars showing Street View imagery alongside a non-Google map, which would rule out pairing each post with an OSM locator.

The long-running everylot bots demonstrate non-enforcement, not permission.

## Implementation notes

- Uses `requests`, not `urllib`: `urllib` fails certificate verification on this Python install.
- Geograph filenames embed an unguessable hash, so stage 3 reads each URL from the photo page's `og:image` tag.
- Postcode geocoding, where needed, went through [postcodes.io](https://postcodes.io) (98.9% resolution).

## Cadence

3,750 libraries. Two a day runs a little over five years, three a day about three years and five months.

## Prior art on Bluesky

Checked August 2026: **no UK library bot exists, and the niche is clear.**

- The everylot family is entirely North American: Chicago, USPS, Milwaukee, Baltimore, Montréal, Richmond, Cleveland, Philadelphia, Detroit, St Louis, Houston, Charlottesville. Nothing British, nothing library-themed.
- `library-bot.bsky.social` posted random libraries worldwide sourced from Google Maps, with star ratings and `maps.google.com` links. Dormant since 16 January 2025 at 98 posts.
- `masslibraryproject.bsky.social` is people visiting Massachusetts town libraries, not a bot.
- `norfolklibrariesuk.bsky.social` and `publiclibraries.bsky.social` are library-sector accounts, not comparable projects.

## Open threads

- **Confirm the Libraries Hacked data licence**, which covers the roster's 384 postable Scottish and Welsh libraries (18% of the corpus). The photographs are unaffected: they are separately licensed from Commons and Geograph. Only names, addresses and coordinates are in question, and 86% of the Scottish and 83% of the Welsh entries also exist in OpenStreetMap under ODbL, so Libraries Hacked is not the only possible source for most of them.

  If the answer comes back no, set `EXCLUDE_NATIONS = {'Scotland', 'Wales'}` in `everylibrary_post.py`. That drops the rotation from 2,188 to 1,804 and needs no rebuild.
- Decide whether to include the 71 independent community libraries, which sit outside the statutory service.
- Closure history is England-heavy: 470 English closures on record against 6 Scottish and 2 Welsh, yet SLIC has separately verified 53 Scottish closures between 2014 and 2024. Don't imply national coverage on the closure angle.
