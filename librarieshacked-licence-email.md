# Licence query to Libraries Hacked

**Sent 16 August 2026** to info@librarieshacked.org, the address published by the Libraries Hacked GitHub organisation. Awaiting a reply.

**Subject:** Licence for the api.librarydata.uk libraries data?

---

Hello,

My name is Chris Stanford, and I've built a small Bluesky bot, @everyuklibrary.bsky.social, which posts the names of U.K. libraries and their addresses, as well as a freely licensed photograph from Wikimedia Commons or Geograph.

The roster of libraries comes from api.librarydata.uk/libraries, which was, by far, the best source I found. It's the only one I came across that covers England, Northern Ireland, Scotland and Wales with coordinates already included, so nothing needed geocoding. Thanks for building and maintaining it.

Before the bot runs too long, I wanted to check the licence position. I looked at the API response headers, librarymap.co.uk, the library data blog, the librarydata.uk portal and librarieshacked.org, and couldn't find a statement covering the aggregated libraries data. I can see that individual dataset records on the portal do carry licences: Plymouth's library locations are recorded as OGL, for instance. So I assume the national roster simply hasn't had one attached, rather than there being any restriction intended.

For England I can trace the underlying data to the DCMS basic dataset registered on data.gov.uk under the Open Government Licence. It's the Scottish, Welsh and Northern Irish records I'm unsure about, which is about 400 libraries in my corpus.

Could you tell me what licence applies, or whether one could be stated? I'm happy to attribute however you'd prefer. Libraries Hacked is credited, with a link, in the bot's pinned post alongside DCMS and OpenStreetMap.

Thanks very much,

Chris Stanford
chris-stanford.com

---

## What to do with the answer

**If a permissive licence is stated** (OGL, ODbL, CC BY or similar): nothing changes. Add the licence name to the pinned post's `Libraries:` line via `everylibrary_post.py --pin`, and note it in the README's Corpus section.

**If the answer is restrictive, or none is forthcoming:** set `EXCLUDE_NATIONS = {'Scotland', 'Wales'}` in `everylibrary_post.py`. That pauses 384 libraries and drops the rotation from 2,188 to 1,804, needing no rebuild. Northern Ireland's 57 postable entries are not covered by that switch and would need adding to the set.

**Either way the photographs are unaffected.** They are separately licensed from Commons and Geograph, credited per post, and independent of the roster question.

**Worth knowing if he asks:** 86% of the Scottish and 83% of the Welsh entries also appear in OpenStreetMap under ODbL, so there is a licensed route to most of them even in the worst case. Not worth raising unprompted, since it reads as arguing rather than asking.
