"""Tests for the staleness guard in everylibrary_images.py.

Added 19 September 2026 after the Lichfield Library mismatch: a Geograph
photo titled "Lichfield College and Library" scored a fine match against
"Lichfield Library" on text and distance alike, and posted publicly on
10 September as the current Market Square building. The photo's own
free-text caption said the library "has recently opened in St Mary's
church on Market Square" -- a different building -- but nothing read that
caption, only the title.

STALE_RE went through two shapes before it reached this one. A bare
`former(ly)?` was tried first and dropped the same day it was written,
caught before it ever reached state by testing the Beaumaris Library fix
against it: "Former school, originally founded in 1603 ... has served as
a community centre and library" tripped it, and on the same reasoning it
had already wrongly flagged Hook Norton and Leyton in the corpus sweep --
both correctly the CURRENT library building, merely repurposed FROM
something else, which is exactly what a "library since <year>" post
caption already says honestly. Every real caption pulled from the corpus
during that sweep is pinned below, both the genuine mismatch and the false
positives it produced, so the regex cannot regress to either extreme.

Stdlib only, no network: resolve_source() is exercised against synthetic
state dicts, never the real image_state.json.
"""
import unittest

import everylibrary_images as m


class StaleReRealCaptions(unittest.TestCase):
    """Every caption actually pulled from the corpus during the 19 September
    2026 sweep of all 98 posted libraries, verbatim. One genuine mismatch,
    seven false positives the first version of this guard produced."""

    def test_lichfield_the_actual_bug(self):
        # The photo behind the 10 September mispost.
        self.assertTrue(m.STALE_RE.search(
            "I'm not sure of the use of these buildings now.  The map "
            "labels them as 'Council Offices' and the building, which is "
            "Grade II listed, is described as Lichfield College and "
            "Lichfield Library, though the library has recently opened "
            "in St Mary's church on Market Square.  Nevertheless it is a "
            "building of merit and stands on part of the site of the "
            "Franciscan Friary which was dissolved in 1538."))

    def test_lichfield_library_entrance_photo_is_the_correct_building(self):
        self.assertFalse(m.STALE_RE.search(
            "The main entrance to Lichfield Library, on the ground floor "
            "of the former St Mary's Church, Lichfield."))

    def test_lichfield_replacement_photo(self):
        self.assertFalse(m.STALE_RE.search(
            "Exterior shot of the former St Mary's Church, Lichfield, "
            "now a library and creative hub."))

    def test_hook_norton_false_positive(self):
        # "Formerly the National School" is the building's PAST use; the
        # title itself says this IS Hook Norton Library now.
        self.assertFalse(m.STALE_RE.search(
            "Hook Norton Library Formerly the National School, built in "
            "1855, with the schoolmaster's house to the left. Listed "
            "Grade II."))

    def test_leyton_false_positive(self):
        # "Outgrown ... and replaced by the new town hall" is the TOWN
        # HALL being replaced, decades before this building became a
        # library; "library" sits in a separate, later sentence.
        self.assertFalse(m.STALE_RE.search(
            "Leyton Library. Grade II Listed building. Designed by John "
            "Knight in an Italianate style and opened in 1882 as Leyton "
            "Town Hall. Outgrown by 1892 and replaced by the new town "
            "hall alongside. Later adapted to serve as a public library"))

    def test_beaumaris_replacement_photo_false_positive(self):
        # The case that caught the first version of this guard: "Former
        # school" precedes "library" by two dozen words, not the two or
        # three this guard's window allows.
        self.assertFalse(m.STALE_RE.search(
            "Former school, originally founded in 1603 and in use as "
            "such, in various guises, until 1962. Since 1971 it has "
            "served as a community centre and library. Grade II listed"))

    def test_beaumaris_original_photo_false_positive(self):
        # Weak framing (the library is "the low building at right
        # angles on the left", mostly out of frame) is a real quality
        # problem, but not the wrong-building class this guard exists
        # for -- it names the same complex, still housing the library.
        self.assertFalse(m.STALE_RE.search(
            "Canolfan Cymdeithasol David Hughes Community Centre The "
            "former David Hughes Free Grammar School, which was opened "
            "in 1603, was converted to a Community Centre in 1971. The "
            "low building at right angles on the left is the library. "
            "Beaumaris Castle is out of sight on the right. David Hughes "
            "is recorded in the Oxford Dictionary of National "
            "Biography."))

    def test_bangor_false_positive(self):
        # "have been demolished" is about the Seacourt Print Workshop and
        # Bassment Centre, not the library; the library itself is only
        # ever described as being extended on the same site.
        self.assertFalse(m.STALE_RE.search(
            "Bangor Library is currently (June 2007) closed for "
            "redevelopment. this is the view from Ward Park showing "
            "that the Seacourt Print Workshop and Bassment Centre have "
            "been demolished and removed. A new structure will be "
            "built onto the back of the existing Library over the "
            "coming months."))

    def test_bedwas_false_positive(self):
        # "former Workmen's Hall" is the NEIGHBOURING building, not the
        # one in the photo.
        self.assertFalse(m.STALE_RE.search(
            "Bedwas Library. The public library is located in a small, "
            "single-storey building on the west side of the former "
            "Workmen's Hall and Institute. Bedwas Library is part of "
            "Caerphilly County Borough Council's library service."))

    def test_taunton_false_positive(self):
        # "the former Carnegie - which is now a wine bar" names a
        # DIFFERENT, earlier library building; this photo is captioned
        # as "the new(-ish) library" itself.
        self.assertFalse(m.STALE_RE.search(
            "The new (-ish*) library in Taunton town centre last time I "
            "was there was to hunt down the former Carnegie - which is "
            "now a wine bar"))

    def test_builth_wells_false_positive(self):
        # The roster's own address names this exact building ("Antur
        # Gwy") as the library's location; "former primary school" is
        # its earlier use.
        self.assertFalse(m.STALE_RE.search(
            "Antur Gwy. The former primary school for Builth Wells. The "
            "playground, now the car park, was divided by a "
            "hooped-topped metal fence, with girls and infants on the "
            "left, and older boys on the right."))


class StaleRePositive(unittest.TestCase):
    """Synthetic captions describing the genuine bad case: the library
    itself named as gone, moved, or replaced."""

    def check(self, text):
        self.assertTrue(m.STALE_RE.search(text), f"should flag: {text!r}")

    def test_former_library(self):
        self.check("Photograph of the former Bridgetown Library")

    def test_formerly_a_library(self):
        self.check("This building was formerly a library")

    def test_disused_library(self):
        self.check("The disused library on the high street")

    def test_library_was_demolished(self):
        self.check("The library was demolished in 2019")

    def test_no_longer_a_library(self):
        self.check("No longer a library, this building now houses a "
                    "dentist's surgery")

    def test_library_moved_to(self):
        self.check("The library moved to new premises in 2015")

    def test_library_relocated_to(self):
        self.check("The library relocated to new premises on the high "
                    "street")

    def test_library_closed_and_moved(self):
        self.check("This library closed in 2015 and the collection "
                    "moved to the new hub")

    def test_now_council_offices(self):
        self.check("The old library building is now council offices")

    def test_now_used_as_private_apartments(self):
        self.check("This building is now used as private apartments")

    def test_now_serves_as_private_flats(self):
        self.check("This building now serves as private flats")

    def test_used_to_be_the_town_library(self):
        self.check("This used to be the town library")


class StaleReNegative(unittest.TestCase):
    """Ordinary captions that must NOT be caught -- the failure mode to
    fear is as much a good photo dropped as a bad one kept."""

    def check(self, text):
        self.assertFalse(m.STALE_RE.search(text), f"should not flag: {text!r}")

    def test_empty(self):
        self.check("")

    def test_plain_building_description(self):
        self.check("A red brick Victorian library building with a clock "
                    "tower and pitched gables.")

    def test_church_caption_no_library_mention(self):
        self.check("Guild Church of St Mary, Lichfield, and the "
                    "traditional market in the square")

    def test_grade_ii_listed(self):
        self.check("Grade II listed building")

    def test_free_library_and_museum_as_a_name(self):
        # "Free Library and Museum" is a real Victorian building name in
        # several UK towns, not a claim that the library is free of a
        # museum or vice versa. Must not trip on "museum" alone.
        self.check("A photograph of the Free Library and Museum, Lichfield")

    def test_memorial_library(self):
        self.check("Joseph Rowntree Memorial Library, York")

    def test_library_since_year(self):
        self.check("Idea Store Bow has served as a library since 2002")

    def test_unrelated_building_reuse(self):
        self.check("Now a Wetherspoons pub, this was once a bank")

    def test_unrelated_demolition_far_from_any_library_mention(self):
        # The Bangor lesson in isolation: a demolished NEIGHBOURING
        # building must not borrow a nearby "library" as its own subject.
        self.check("The building was demolished in 2019, unrelated to "
                    "the library next door")


class UsableGate(unittest.TestCase):
    """_usable() is the single choke point resolve_source() checks at every
    tier -- these pin its two failure-independent conditions."""

    def test_none_meta_is_unusable(self):
        self.assertFalse(m._usable(None))

    def test_meta_with_no_description_is_usable(self):
        self.assertTrue(m._usable({"description": "", "url": "x"}))

    def test_meta_missing_description_key_is_usable(self):
        # Most photos have no caption at all; the key may be entirely
        # absent for a wikidata-nearby/p18 entry fetched before this field
        # existed. Absence must read as "nothing to flag", not a KeyError.
        self.assertTrue(m._usable({"url": "x"}))

    def test_stale_description_is_unusable(self):
        self.assertFalse(m._usable({
            "description": "The library has recently opened elsewhere",
            "url": "x",
        }))


class ResolveSourceCascade(unittest.TestCase):
    """resolve_source() must treat a stale match exactly like a miss at
    every tier, cascading to the next one down rather than either accepting
    the bad photo or giving up outright when a better one is available."""

    def base_state(self):
        return {"p18": {}, "geo": {}, "geograph": {}, "wdnear": {},
                "imageinfo": {}}

    def row(self, **kw):
        r = {"osm_id": "12345", "wikidata": "", "name": "Test Library",
             "lat": 51.0, "lon": -1.0}
        r.update(kw)
        return r

    def test_p18_stale_cascades_to_geo(self):
        state = self.base_state()
        state["p18"]["Q1"] = "StaleBuilding.jpg"
        state["geo"]["12345"] = {"title": "File:GoodBuilding.jpg", "dist": 40}
        state["imageinfo"]["File:StaleBuilding.jpg"] = {
            "description": "The library has recently opened elsewhere.",
            "url": "x", "licence": "CC BY-SA 4.0", "artist": "A",
        }
        state["imageinfo"]["File:GoodBuilding.jpg"] = {
            "description": "A library building.", "url": "y",
            "licence": "CC BY-SA 4.0", "artist": "B",
        }
        src, title, dist, meta = m.resolve_source(self.row(wikidata="Q1"), state)
        self.assertEqual(src, "commons-geosearch")
        self.assertEqual(title, "File:GoodBuilding.jpg")

    def test_geo_stale_cascades_to_geograph(self):
        state = self.base_state()
        state["geo"]["12345"] = {"title": "File:StaleBuilding.jpg", "dist": 40}
        state["imageinfo"]["File:StaleBuilding.jpg"] = {
            "description": "The library moved to new premises in 2015.",
            "url": "x", "licence": "CC BY-SA 4.0", "artist": "A",
        }
        state["geograph"]["12345"] = {
            "id": "999", "title": "Test Library", "url": "z",
            "photographer": "C", "dist": 90, "description": "",
        }
        src, title, dist, meta = m.resolve_source(self.row(), state)
        self.assertEqual(src, "geograph")
        self.assertEqual(dist, 90)

    def test_geograph_stale_falls_through_to_no_image(self):
        state = self.base_state()
        state["geograph"]["12345"] = {
            "id": "999", "title": "Old College and Library", "url": "z",
            "photographer": "C", "dist": 90,
            "description": "the library has recently opened elsewhere",
        }
        src, title, dist, meta = m.resolve_source(self.row(), state)
        self.assertIsNone(src)
        self.assertIsNone(meta)

    def test_all_tiers_stale_gives_no_image_not_a_crash(self):
        state = self.base_state()
        state["p18"]["Q1"] = "A.jpg"
        state["geo"]["12345"] = {"title": "File:B.jpg", "dist": 10}
        state["geograph"]["12345"] = {
            "id": "1", "title": "C", "url": "u", "photographer": "p",
            "dist": 5, "description": "the library was formerly here",
        }
        state["wdnear"]["12345"] = {"qid": "Q2", "title": "File:D.jpg", "dist": 20}
        for t in ("A.jpg", "File:B.jpg", "File:D.jpg"):
            state["imageinfo"][t] = {
                "description": "The library has since closed.",
                "url": "x", "artist": "z"}
        src, title, dist, meta = m.resolve_source(self.row(wikidata="Q1"), state)
        self.assertIsNone(src)
        self.assertIsNone(title)
        self.assertIsNone(meta)

    def test_no_description_at_all_is_the_ordinary_case(self):
        # The overwhelming majority of matched photos carry no caption.
        # This must resolve exactly as it did before the guard existed.
        state = self.base_state()
        state["p18"]["Q1"] = "Ordinary.jpg"
        state["imageinfo"]["File:Ordinary.jpg"] = {
            "url": "x", "licence": "CC BY-SA 4.0", "artist": "A",
        }
        src, title, dist, meta = m.resolve_source(self.row(wikidata="Q1"), state)
        self.assertEqual(src, "wikidata-p18")
        self.assertEqual(title, "File:Ordinary.jpg")

    def test_ordinary_repurposed_building_caption_is_not_cascaded(self):
        # The Hook Norton/Leyton shape reproduced through resolve_source:
        # a building with a real prior use, correctly the current library,
        # must resolve at its own tier rather than being pushed down.
        state = self.base_state()
        state["p18"]["Q1"] = "HookNorton.jpg"
        state["imageinfo"]["File:HookNorton.jpg"] = {
            "description": "Hook Norton Library Formerly the National "
                            "School, built in 1855. Listed Grade II.",
            "url": "x", "licence": "CC BY-SA 2.0", "artist": "A",
        }
        src, title, dist, meta = m.resolve_source(self.row(wikidata="Q1"), state)
        self.assertEqual(src, "wikidata-p18")

    def test_missing_file_still_cascades_as_before(self):
        # A title recorded in state but absent from imageinfo (the file
        # vanished or the fetch failed) must still fall through, exactly as
        # it did before this change -- the staleness guard must not disturb
        # that pre-existing behaviour.
        state = self.base_state()
        state["p18"]["Q1"] = "Vanished.jpg"
        state["geo"]["12345"] = {"title": "File:GoodBuilding.jpg", "dist": 40}
        state["imageinfo"]["File:GoodBuilding.jpg"] = {
            "description": "", "url": "y", "artist": "B",
        }
        # File:Vanished.jpg deliberately absent from imageinfo.
        src, title, dist, meta = m.resolve_source(self.row(wikidata="Q1"), state)
        self.assertEqual(src, "commons-geosearch")


if __name__ == "__main__":
    unittest.main()
