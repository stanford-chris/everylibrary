# Draft: licence query to Libraries Hacked

**To:** info@librarieshacked.org
**Alternative:** @librarieshacked.bsky.social on Bluesky
**Subject:** Licence for the api.librarydata.uk libraries data?

---

Hello,

I've built a small Bluesky bot, [@everyuklibrary.bsky.social](https://bsky.app/profile/everyuklibrary.bsky.social), which posts one UK public library at a time: its name, its address, and a freely licensed photograph from Wikimedia Commons or Geograph.

The roster comes from `api.librarydata.uk/libraries`, which was by a distance the best source I found. It's the only one I came across covering all four nations with coordinates already in the payload, and it saved me a great deal of work. Thank you for building and maintaining it.

Before the bot runs too long, I wanted to check the licence position. I looked at the API response headers, librarymap.co.uk, the library data blog, the librarydata.uk portal and librarieshacked.org, and couldn't find a statement covering the aggregated libraries data. I can see that individual dataset records on the portal do carry licences: Plymouth's library locations are recorded as OGL, for instance. So I assume the national roster simply hasn't had one attached, rather than there being any restriction intended.

For England I can trace the underlying data to the DCMS basic dataset registered on data.gov.uk under the Open Government Licence. It's the Scottish, Welsh and Northern Irish records I'm unsure about, which is about 400 libraries in my corpus.

Could you tell me what licence applies, or whether one could be stated? I'm happy to attribute however you'd prefer. Libraries Hacked is credited, with a link, in the bot's pinned post alongside DCMS and OpenStreetMap.

With thanks,

Chris Stanford
chris-stanford.com

---

## Notes before sending

- The bot is live and currently includes the Scottish and Welsh records. If the answer comes back restrictive, `EXCLUDE_NATIONS = {'Scotland', 'Wales'}` in `everylibrary_post.py` pauses those 384 libraries immediately.
- The photographs are unaffected either way: those are separately licensed CC BY-SA and similar, from Commons and Geograph.
- Worth mentioning if he asks: 86% of the Scottish and 83% of the Welsh entries also appear in OpenStreetMap under ODbL, so there is a licensed fallback route for most of them.
