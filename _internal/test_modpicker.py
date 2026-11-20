"""The mod picker (custom_build/package/modpicker_model.py: what is ticked and what applying does; modpicker.py: the pygame
window). The logic is tested on temporary folders, the window headlessly (SDL's dummy display), and one run of the whole
program in a child process with a key press standing in for the player."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import modloader

PACKAGE = Path(modloader.REPO_DIR) / "custom_build" / "package"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import modpicker_model as model  # noqa: E402

try:
    import pygame
    import modpicker
except Exception:                      # the window needs pygame; the logic below does not
    pygame = modpicker = None
needs_pygame = unittest.skipIf(pygame is None, "pygame is not importable here")

REAL_MODS = Path(modloader.REPO_DIR) / "mods"


def make_mods(root: Path, **mods) -> Path:
    """``root/mods_available`` holding the given mods: ``{name: {"requires": [...], "category": ..., "recommended": ...,
    "title": ..., "readme": text, "files": {name: text}}}``."""
    import json
    for name, spec in mods.items():
        folder = root / "mods_available" / name
        folder.mkdir(parents=True)
        meta = {"description": spec.get("description", f"does {name}"), "requires": spec.get("requires", []),
                "category": spec.get("category", "features"), "recommended": spec.get("recommended", False)}
        (folder / "mod.json").write_text(json.dumps(meta), encoding="utf-8")
        if "readme" in spec:
            (folder / "README.md").write_text(spec["readme"], encoding="utf-8")
        for fname, text in spec.get("files", {}).items():
            (folder / fname).write_text(text, encoding="utf-8")
    (root / "mods").mkdir(exist_ok=True)
    return root


class Temp(unittest.TestCase):
    def folder(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def catalogue(self, **extra) -> Path:
        """core (a library) <- a (recommended) <- b ; c alone; d (a recorder)."""
        mods = dict(core={"category": "library"}, a={"requires": ["core"], "recommended": True}, b={"requires": ["a"]},
                    c={"recommended": True}, d={"category": "recorders"})
        mods.update(extra)
        return make_mods(self.folder(), **mods)


class ReadmeTests(unittest.TestCase):
    TEXT = ("# Combat Log\n\nA window of **damage** numbers.\nSecond line of the summary.\n\n## What it does\n\n- one\n- two\n\n"
            "## Needs\n\n`damage_types`. `modctl add` copies what a mod needs for you; copying by hand, copy these folders too.\n\n"
            "Works better with: `ui_pages`.\n\n## Screenshots\n\n![The window](screenshots/window.png)\n\n## Under the hood\n\nx\n")

    def test_the_title_the_summary_and_the_sections_are_read(self):
        title, summary, sections = model.parse_readme(self.TEXT)
        self.assertEqual(title, "Combat Log")
        self.assertEqual(summary, "A window of **damage** numbers. Second line of the summary.")
        self.assertEqual([h for h, _ in sections], ["What it does", "Needs", "Screenshots", "Under the hood"])
        self.assertEqual(sections[0][1], ["- one", "- two"])                  # no blank lines at the ends

    def test_a_readme_without_a_title_still_gives_a_summary(self):
        self.assertEqual(model.parse_readme("just words\n\n## Using it\n\nx\n"), ("", "just words", [("Using it", ["x"])]))

    def test_the_window_says_that_ticking_does_what_modctl_does_and_drops_the_picture_links(self):
        _title, _summary, sections = model.parse_readme(self.TEXT)
        shown = dict(model.for_players(sections))
        self.assertEqual(shown["Needs"][0], "`damage_types` (added for you when you tick this mod).")
        self.assertNotIn("Screenshots", shown)                               # the pictures come from the folder instead
        self.assertEqual(shown["What it does"], ["- one", "- two"])

    def test_a_screenshots_section_with_words_in_it_stays(self):
        shown = dict(model.for_players([("Screenshots", ["![x](screenshots/x.png)", "Taken in Sol."])]))
        self.assertEqual(shown["Screenshots"], ["Taken in Sol."])

    def test_the_captions_come_from_the_picture_links(self):
        self.assertEqual(model.image_captions(self.TEXT), {"window.png": "The window"})


class CatalogTests(Temp):
    def test_a_mod_is_read_from_its_readme_its_mod_json_and_its_screenshots(self):
        root = make_mods(self.folder(), a={"requires": [], "category": "speed", "recommended": True,
                                           "readme": "# Fast A\n\nMakes A fast.\n\n## Using it\n\nNothing.\n"})
        shots = root / "mods_available" / "a" / "screenshots"
        shots.mkdir()
        for name in ("2.png", "1.PNG", "notes.txt"):
            (shots / name).write_bytes(b"x")
        entry = model.load_catalog(root / "mods_available").by_name["a"]
        self.assertEqual((entry.title, entry.summary, entry.category, entry.recommended), ("Fast A", "Makes A fast.", "speed", True))
        self.assertEqual([p.name for p in entry.screenshots], ["1.PNG", "2.png"])         # pictures only, in name order

    def test_a_mod_without_a_readme_or_category_is_listed_by_its_name_under_features(self):
        root = make_mods(self.folder(), plain={"description": "a plain mod"})
        (root / "mods_available" / "plain" / "mod.json").write_text('{"description": "a plain mod"}', encoding="utf-8")
        entry = model.load_catalog(root / "mods_available").by_name["plain"]
        self.assertEqual((entry.title, entry.summary, entry.category, entry.recommended),
                         ("plain", "a plain mod", "features", False))

    def test_libraries_are_not_listed_and_the_categories_come_in_a_fixed_order_by_title(self):
        catalog = model.load_catalog(self.catalogue(zed={"category": "speed"}) / "mods_available")
        self.assertEqual([(key, [e.name for e in members]) for key, _label, members in catalog.grouped()],
                         [("features", ["a", "b", "c"]), ("speed", ["zed"]), ("recorders", ["d"])])
        self.assertNotIn("core", [e.name for e in catalog.visible])
        self.assertIn("core", catalog.by_name)

    def test_an_unknown_category_is_listed_last_under_other(self):
        catalog = model.load_catalog(self.catalogue(odd={"category": "weird"}) / "mods_available")
        self.assertEqual(catalog.grouped()[-1][0:2], ("other", "Other"))

    def test_a_folder_without_mod_json_is_not_a_mod(self):
        root = self.catalogue()
        (root / "mods_available" / "notes").mkdir()
        (root / "mods_available" / "notes" / "x.txt").write_text("hi", encoding="utf-8")
        self.assertNotIn("notes", model.load_catalog(root / "mods_available").by_name)


class SelectionTests(Temp):
    def selection(self, *chosen):
        catalog = model.load_catalog(self.catalogue() / "mods_available")
        return model.Selection(catalog, chosen)

    def test_ticking_a_mod_brings_what_it_needs_all_the_way_down_but_only_the_tick_is_a_choice(self):
        s = self.selection()
        s.toggle("b")
        self.assertEqual((s.chosen, s.closure()), ({"b"}, {"a", "b", "core"}))
        self.assertEqual([s.state(n) for n in ("b", "a", "core", "c")], ["on", "auto", "auto", "off"])
        self.assertEqual(s.needed_by("a"), ["b"])
        s.toggle("b")
        self.assertEqual((s.chosen, s.closure()), (set(), set()))

    def test_a_mod_that_is_only_there_because_another_needs_it_can_be_ticked_for_itself(self):
        s = self.selection("b")
        s.toggle("a")                                         # now a real choice
        s.toggle("b")
        self.assertEqual((s.chosen, s.state("a")), ({"a"}, "on"))

    def test_the_text_for_an_untick_box_says_what_ticking_it_would_add(self):
        s = self.selection("c")
        self.assertEqual(s.adds("b"), ["a", "core"])
        s.toggle("a")
        self.assertEqual(s.adds("b"), [])

    def test_libraries_cannot_be_ticked_directly(self):
        s = self.selection("core")
        self.assertEqual(s.chosen, set())
        s.toggle("core")
        s.toggle("nope")
        self.assertEqual(s.chosen, set())

    def test_recommended_and_none(self):
        s = self.selection("d")
        s.recommended()
        self.assertEqual(s.chosen, {"a", "c"})
        s.none()
        self.assertEqual(s.chosen, set())


class ApplyTests(Temp):
    def setUp(self):
        self.root = self.catalogue()
        self.available, self.installed = str(self.root / "mods_available"), str(self.root / "mods")
        self.catalog = model.load_catalog(self.available)

    def apply(self, *chosen):
        return model.apply_selection(model.Selection(self.catalog, chosen), self.available, self.installed)

    def test_the_first_run_ticks_the_recommended_mods_until_anything_is_chosen(self):
        self.assertEqual(model.initial_selection(self.catalog, self.installed).chosen, {"a", "c"})
        self.apply()                                                       # chose none, on purpose
        self.assertEqual(model.initial_selection(self.catalog, self.installed).chosen, set())

    def test_the_window_opens_with_what_is_in_mods_when_there_is_something(self):
        self.apply("d")
        self.assertEqual(model.initial_selection(self.catalog, self.installed).chosen, {"d"})
        (Path(self.installed) / ".chosen").unlink()                        # still not "first run": mods are in use
        self.assertEqual(model.initial_selection(self.catalog, self.installed).chosen, {"d"})

    def test_apply_copies_what_is_ticked_with_what_it_needs_and_remembers_the_choice(self):
        report = self.apply("b", "d")
        self.assertEqual(report["added"], ["a", "b", "core", "d"])
        self.assertEqual(model.installed_names(self.installed), {"a", "b", "core", "d"})
        self.assertTrue((Path(self.installed) / model.MARKER).is_file())

    def test_unticking_removes_the_mod_and_what_only_it_needed(self):
        self.apply("b", "d")
        report = self.apply("d")
        self.assertEqual(report["removed"], ["a", "b", "core"])
        self.assertEqual(model.installed_names(self.installed), {"d"})

    def test_a_mod_still_needed_by_a_ticked_one_stays_when_it_is_unticked(self):
        self.apply("a", "b")
        self.apply("b")                                                   # a is no longer a choice, b still needs it
        self.assertEqual(model.installed_names(self.installed), {"a", "b", "core"})

    def test_changed_copies_are_refreshed_and_nothing_else_is_touched(self):
        self.apply("c", "d")
        (Path(self.available) / "c" / "new.py").write_text("X = 1\n", encoding="utf-8")
        report = self.apply("c", "d")
        self.assertEqual((report["updated"], report["added"], report["removed"]), (["c"], [], []))
        self.assertTrue((Path(self.installed) / "c" / "new.py").is_file())

    def test_the_players_own_mods_and_what_they_need_are_left_alone(self):
        own = Path(self.installed) / "mine"
        own.mkdir()
        (own / "mod.json").write_text('{"description": "mine", "requires": ["a"]}', encoding="utf-8")
        (own / "mine.py").write_text("X = 1\n", encoding="utf-8")
        report = self.apply("d")                                          # a (and core) were not ticked, but mine needs them
        self.assertEqual(model.installed_names(self.installed), {"mine", "a", "core", "d"})
        self.assertEqual(report["kept_own"], ["mine"])
        self.assertEqual((own / "mine.py").read_text(encoding="utf-8"), "X = 1\n")
        self.apply()                                                      # even "none" keeps them
        self.assertEqual(model.installed_names(self.installed), {"mine", "a", "core"})

    def test_a_mods_readme_and_screenshots_are_not_copied_into_mods(self):
        root = make_mods(self.folder(), shot={"readme": "# S\n\nx\n"})
        (root / "mods_available" / "shot" / "screenshots").mkdir()
        (root / "mods_available" / "shot" / "screenshots" / "a.png").write_bytes(b"x")
        catalog = model.load_catalog(root / "mods_available")
        model.apply_selection(model.Selection(catalog, ["shot"]), root / "mods_available", root / "mods")
        self.assertEqual(sorted(p.name for p in (root / "mods" / "shot").iterdir()), ["mod.json"])


class FirstRunTests(Temp):
    def test_it_is_needed_only_when_mods_are_on_offer_none_is_in_use_and_nothing_was_ever_chosen(self):
        root = self.folder()
        self.assertFalse(model.first_run_needed(root), "no mods_available")
        make_mods(root, a={})
        self.assertTrue(model.first_run_needed(root))
        (root / "mods" / "a").mkdir()
        self.assertFalse(model.first_run_needed(root), "a mod is in use")
        (root / "mods" / "a").rmdir()
        model.mark_chosen(root / "mods")
        self.assertFalse(model.first_run_needed(root), "already chosen once")

    def test_the_window_is_run_and_waited_for_and_only_cancelling_stops_the_game(self):
        root = make_mods(self.folder(), a={})
        calls = []

        def fake(code):
            def run(command, **kw):
                calls.append(command)
                return subprocess.CompletedProcess(command, code)
            return run
        self.assertTrue(model.choose_on_first_run(root, run=fake(0), python="py"))
        self.assertEqual(calls[0][0], "py")
        self.assertEqual(calls[0][2:], ["--play", "--root", str(root)])
        self.assertTrue(calls[0][1].endswith("modpicker.py"))
        self.assertTrue(model.choose_on_first_run(root, run=fake(1)), "the window crashed: play anyway")
        self.assertFalse(model.choose_on_first_run(root, run=fake(model.EXIT_CANCELLED)), "closed the window: do not start")

    def test_a_window_that_cannot_be_started_does_not_stop_the_game(self):
        root = make_mods(self.folder(), a={})

        def broken(command, **kw):
            raise OSError("no such program")
        self.assertTrue(model.choose_on_first_run(root, run=broken))

    def test_nothing_is_run_when_it_is_not_the_first_time(self):
        root = self.folder()                                               # no mods on offer

        def never(command, **kw):
            raise AssertionError("the window must not open")
        self.assertTrue(model.choose_on_first_run(root, run=never))


class RealModsTests(unittest.TestCase):
    """The shipped mods: what the window lists must make sense."""

    @classmethod
    def setUpClass(cls):
        cls.catalog = model.load_catalog(REAL_MODS)

    def test_every_mod_has_a_known_category_and_a_true_or_false_recommendation(self):
        import json
        known = {key for key, _label in model.CATEGORIES} | {model.LIBRARY}
        for entry in self.catalog.entries:
            meta = json.loads((REAL_MODS / entry.name / "mod.json").read_text(encoding="utf-8-sig"))
            with self.subTest(mod=entry.name):
                self.assertIn(meta.get("category"), known)
                self.assertIsInstance(meta.get("recommended"), bool)

    def test_a_library_is_never_recommended_and_what_is_recommended_needs_nothing_that_is_not_ok_to_add(self):
        for entry in self.catalog.entries:
            with self.subTest(mod=entry.name):
                if entry.category == model.LIBRARY:
                    self.assertFalse(entry.recommended)
                if entry.recommended:
                    for needed in entry.requires:
                        self.assertTrue(self.catalog.by_name[needed].recommended or self.catalog.by_name[needed].category == model.LIBRARY,
                                        f"{entry.name} is recommended but needs {needed}, which is neither a library nor recommended")

    def test_what_records_data_or_acts_for_the_player_is_not_recommended(self):
        for entry in self.catalog.entries:
            if entry.category in ("recorders", "diagnostics", "advanced"):
                self.assertFalse(entry.recommended, entry.name)

    def test_every_mod_lists_with_a_title_and_a_summary_and_nothing_a_player_cannot_use(self):
        for entry in self.catalog.entries:
            with self.subTest(mod=entry.name):
                self.assertTrue(entry.title and entry.summary)
                self.assertTrue(entry.sections)
                text = "\n".join(line for _h, body in entry.sections for line in body)
                self.assertNotIn("modctl", " ".join(dict(entry.sections).get("Needs", [])))
                self.assertNotIn("![", text)

    def test_the_first_run_ticks_a_sensible_set(self):
        s = model.Selection(self.catalog)
        s.recommended()
        self.assertTrue(s.chosen)
        self.assertLess(len(s.chosen), len(self.catalog.visible))
        self.assertNotIn("scan_capture", s.closure())                      # recorders are never switched on for the player

    def test_applying_the_recommended_set_to_an_empty_mods_folder_gives_mods_the_loader_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "mods"
            s = model.Selection(self.catalog)
            s.recommended()
            model.apply_selection(s, REAL_MODS, installed)
            mods = modloader.load_mods(installed)
            self.assertEqual({m.name for m in mods}, s.closure())
            self.assertEqual(set(modloader.requirement_closure(mods, [m.name for m in mods])), {m.name for m in mods},
                             "everything a copied mod requires was copied too")


@needs_pygame
class WindowTests(Temp):
    def setUp(self):
        pygame.init()
        self.screen = pygame.Surface((1120, 720))
        self.root = self.folder()

    def picker(self, play=True, real=True, scale=1.0, **mods):
        if real:
            return modpicker.Picker(root=self.root, play=play, scale=scale, available=str(REAL_MODS),
                                    installed=str(self.root / "mods"))
        make_mods(self.root, **mods)
        return modpicker.Picker(root=self.root, play=play, scale=scale)

    def key(self, picker, lay, key):
        picker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key), lay)

    def click(self, picker, lay, pos):
        picker.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos), lay)

    def first_row(self, picker, lay, name=None):
        return next(rect for kind, key, rect, _e in picker.rows(lay) if kind == "mod" and (name is None or key == name))

    # -- drawing ---------------------------------------------------------------------------------------------
    def test_it_draws_every_mod_at_several_window_sizes_and_scales(self):
        for size, scale in (((1120, 720), 1.0), ((800, 500), 1.0), ((1920, 1080), 1.5), ((640, 400), 1.0)):
            picker = self.picker(scale=scale)
            surface = pygame.Surface(size)
            for name in picker.order:
                picker.selected = name
                picker.detail_scroll = 0
                picker.draw(surface)                                       # must not raise, whatever the mod's README holds
            self.assertEqual(tuple(surface.get_at((3, 3)))[:3], modpicker.BG)

    def test_the_buttons_fit_in_the_window_and_do_not_overlap(self):
        for size in ((1120, 720), (800, 500)):
            picker = self.picker()
            lay = picker.draw(pygame.Surface(size))
            window = pygame.Rect(0, 0, *size)
            for button in picker.buttons:
                self.assertTrue(window.contains(button.rect), (button.label, button.rect, size))
            for i, a in enumerate(picker.buttons):
                for b in picker.buttons[i + 1:]:
                    self.assertFalse(a.rect.colliderect(b.rect), (a.label, b.label))

    def test_text_wraps_inside_its_width_and_a_long_word_overflows_instead_of_being_cut(self):
        fonts = modpicker.Fonts(1.0)
        runs = modpicker.inline_runs("A **bold** word and `some_code` and then a plain tail that goes on for a while")
        for width in (120, 200, 400):
            lines = modpicker.wrap_runs(runs, fonts, "body", width)
            self.assertGreater(len(lines), 1)
            for line in lines:
                self.assertLessEqual(sum(fonts.by["body", s].size(t)[0] for t, s in line), width, line)
                self.assertNotEqual(line[0][0], " ")
                self.assertNotEqual(line[-1][0], " ")
        long = modpicker.wrap_runs([("x" * 200, "normal")], fonts, "body", 100)
        self.assertEqual("".join(t for t, _s in long[0]), "x" * 200)

    def test_the_markup_in_a_summary_is_stripped_for_the_list_and_styled_for_the_detail(self):
        self.assertEqual(modpicker.plain("Use **/ap** and `zlib3` now"), "Use /ap and zlib3 now")
        self.assertEqual(modpicker.inline_runs("a **b** `c` d"), [("a ", "normal"), ("b", "bold"), (" ", "normal"), ("c", "code"),
                                                                   (" d", "normal")])

    def test_a_screenshot_is_shown_under_the_summary_scaled_to_the_panel_with_its_caption(self):
        make_mods(self.root, a={"readme": "# Pic\n\nSummary.\n\n## Screenshots\n\n![The big window](screenshots/big.png)\n"})
        shots = self.root / "mods_available" / "a" / "screenshots"
        shots.mkdir()
        pygame.image.save(pygame.Surface((1600, 900)), str(shots / "big.png"))
        (shots / "broken.png").write_bytes(b"not a picture")
        picker = modpicker.Picker(root=self.root, play=True)                         # reload with the pictures there
        lay = picker.draw(self.screen)
        ops, height = picker.detail(lay)
        images = [op for op in ops if op[0] == "image"]
        self.assertEqual(len(images), 1)
        self.assertLessEqual(images[0][3].get_width(), lay["scroll"].w)
        self.assertLess(images[0][3].get_width(), 1600)                            # scaled down, in proportion
        self.assertAlmostEqual(images[0][3].get_width() / images[0][3].get_height(), 1600 / 900, places=1)
        texts = [op[3] for op in ops if op[0] == "text"]
        joined = "".join(texts)                                                    # one op per word
        self.assertIn("The big window", joined)                                   # the README's words, not the file name
        self.assertIn("broken.png", joined, "a picture that cannot be loaded is named, not fatal")
        self.assertNotIn("Screenshots", texts, "the README's own Screenshots section is left out")

    def test_with_no_mods_to_choose_from_it_says_so_and_still_draws(self):
        picker = self.picker(real=False)                                          # mods_available/ does not exist
        picker.draw(self.screen)
        self.assertEqual((picker.groups, picker.selected), ([], None))
        self.key(picker, picker.draw(self.screen), pygame.K_DOWN)                  # nothing to move to: no crash
        self.key(picker, picker.draw(self.screen), pygame.K_SPACE)

    # -- the player's actions -----------------------------------------------------------------------------------
    def test_a_first_run_opens_with_the_recommended_mods_ticked(self):
        picker = self.picker()
        recommended = {e.name for e in picker.catalog.visible if e.recommended}
        self.assertEqual(picker.selection.chosen, recommended)

    def test_clicking_the_box_ticks_the_mod_and_clicking_the_name_only_reads_about_it(self):
        picker = self.picker()
        lay = picker.draw(self.screen)
        name = picker.order[0]
        row = self.first_row(picker, lay, name)
        before = name in picker.selection.chosen
        self.click(picker, lay, (row.x + 300, row.centery))                         # on the words
        self.assertEqual(name in picker.selection.chosen, before)
        other = picker.order[2]
        row = self.first_row(picker, picker.draw(self.screen), other)
        self.click(picker, lay, (row.x + 300, row.centery))
        self.assertEqual(picker.selected, other)
        row = self.first_row(picker, picker.draw(self.screen), other)
        box = picker.checkbox_rect(row)
        was = other in picker.selection.chosen
        self.click(picker, lay, box.center)
        self.assertEqual(other in picker.selection.chosen, not was)

    def test_the_keyboard_moves_ticks_and_chooses(self):
        picker = self.picker()
        lay = picker.draw(self.screen)
        first = picker.order[0]
        self.key(picker, lay, pygame.K_DOWN)
        self.assertEqual(picker.selected, picker.order[1])
        self.key(picker, lay, pygame.K_UP)
        self.key(picker, lay, pygame.K_UP)                                         # stops at the top
        self.assertEqual(picker.selected, first)
        was = first in picker.selection.chosen
        self.key(picker, lay, pygame.K_SPACE)
        self.assertEqual(first in picker.selection.chosen, not was)
        self.key(picker, lay, pygame.K_END)
        self.assertEqual(picker.selected, picker.order[-1])
        lay = picker.draw(self.screen)
        self.assertTrue(lay["list"].colliderect(self.first_row(picker, lay, picker.order[-1])), "the list scrolled to show it")
        self.key(picker, lay, pygame.K_HOME)
        self.assertEqual(picker.selected, first)
        self.key(picker, lay, pygame.K_RETURN)
        self.assertEqual(picker.result, "apply")

    def test_escape_and_closing_the_window_cancel(self):
        for event in (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE), pygame.event.Event(pygame.QUIT)):
            picker = self.picker()
            picker.handle_event(event, picker.draw(self.screen))
            self.assertEqual(picker.result, "cancel")

    def test_the_buttons_do_what_they_say(self):
        picker = self.picker()
        lay = picker.draw(self.screen)
        self.click(picker, lay, picker.none_button.rect.center)
        self.assertEqual(picker.selection.chosen, set())
        self.click(picker, lay, picker.recommended_button.rect.center)
        self.assertTrue(picker.selection.chosen)
        self.click(picker, lay, picker.secondary.rect.center)
        self.assertEqual(picker.result, "skip")                                      # "Play without mods"
        again = self.picker(play=False)
        again.draw(self.screen)
        self.assertEqual((again.primary.label, again.secondary.label), ("Apply", "Cancel"))
        self.click(again, again.draw(self.screen), again.secondary.rect.center)
        self.assertEqual(again.result, "cancel")

    def test_the_wheel_scrolls_the_panel_under_the_mouse_and_stops_at_the_ends(self):
        picker = self.picker()
        lay = picker.draw(pygame.Surface((800, 500)))                              # small enough that a README must scroll
        picker.mouse = lay["list"].center
        for _ in range(200):
            picker.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-1), lay)
        self.assertEqual(picker.list_scroll, picker.list_height() - lay["list"].h)
        self.assertEqual(picker.detail_scroll, 0)
        for _ in range(400):
            picker.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1), lay)
        self.assertEqual(picker.list_scroll, 0)
        picker.mouse = lay["detail"].center
        picker.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-3), lay)
        self.assertGreater(picker.detail_scroll, 0)
        self.assertEqual(picker.list_scroll, 0)

    def test_choosing_another_mod_shows_it_from_the_top(self):
        picker = self.picker()
        lay = picker.draw(self.screen)
        picker.detail_scroll = 80
        self.key(picker, lay, pygame.K_DOWN)
        self.assertEqual(picker.detail_scroll, 0)

    # -- the outcome --------------------------------------------------------------------------------------------
    def small(self):
        """A picker over two mods in the temporary root: ``a`` (recommended, needs the library ``core``) and ``b``."""
        return self.picker(real=False, core={"category": "library"}, a={"requires": ["core"], "recommended": True}, b={})

    def test_apply_fills_mods_and_the_code_says_carry_on(self):
        picker = self.small()
        picker.result = "apply"
        self.assertEqual(picker.finish(), model.EXIT_CONTINUE)
        self.assertEqual(model.installed_names(self.root / "mods"), {"a", "core"})
        self.assertFalse(model.first_run_needed(self.root))

    def test_playing_without_mods_leaves_mods_alone_and_remembers_the_answer(self):
        picker = self.small()
        self.assertTrue(model.first_run_needed(self.root))
        picker.result = "skip"
        self.assertEqual(picker.finish(), model.EXIT_CONTINUE)
        self.assertEqual(model.installed_names(self.root / "mods"), set())
        self.assertFalse(model.first_run_needed(self.root), "it must not ask again on every start")

    def test_cancelling_changes_nothing_and_the_code_says_stop(self):
        picker = self.small()
        picker.result = "cancel"
        self.assertEqual(picker.finish(), model.EXIT_CANCELLED)
        self.assertEqual([n for n in os.listdir(self.root / "mods")], [])
        self.assertTrue(model.first_run_needed(self.root), "it asks again the next time")


@needs_pygame
class ProgramTests(Temp):
    """The program itself, started the way the launch file starts it, with a key press standing in for the player."""

    CODE = r"""
import os, sys
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, {package!r})
import pygame
pygame.event.wait = lambda timeout=0: pygame.event.Event(pygame.KEYDOWN, key={key})
import modpicker
sys.exit(modpicker.main(["--root", {root!r}, "--scale", "1"] + {flags!r}))
"""

    def run_program(self, root, key, flags):
        code = self.CODE.format(package=str(PACKAGE), key=key, root=str(root), flags=flags)
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)

    def test_enter_on_a_first_run_applies_the_recommended_mods_and_exits_0(self):
        root = make_mods(self.folder(), a={"recommended": True}, b={})
        done = self.run_program(root, "pygame.K_RETURN", ["--play"])
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertEqual(model.installed_names(root / "mods"), {"a"})
        self.assertFalse(model.first_run_needed(root))

    def test_escape_exits_with_the_cancel_code_and_changes_nothing(self):
        root = make_mods(self.folder(), a={"recommended": True})
        done = self.run_program(root, "pygame.K_ESCAPE", ["--play"])
        self.assertEqual(done.returncode, model.EXIT_CANCELLED, done.stderr[-800:])
        self.assertEqual(model.installed_names(root / "mods"), set())

    def test_the_launchers_own_call_sees_the_exit_codes_of_the_real_program(self):
        """choose_on_first_run runs modpicker.py itself: here with a key press in place of the player (its own program
        arguments are the real ones, the display is SDL's dummy one)."""
        root = make_mods(self.folder(), a={"recommended": True})
        real_run = subprocess.run

        def run_with_a_key(command, **kw):
            code = self.CODE.format(package=str(PACKAGE), key="pygame.K_ESCAPE", root=str(root), flags=command[2:3])
            return real_run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertFalse(model.choose_on_first_run(root, run=run_with_a_key))
        self.assertEqual(model.installed_names(root / "mods"), set())


if __name__ == "__main__":
    unittest.main()
