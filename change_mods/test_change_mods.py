"""Tests for change_mods.py: when the login screen offers the button, where it sits, what a click does, what each way of closing the mod
picker does, and the hooks on the game's real LoginScreen."""

from __future__ import annotations
import modloader

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

import change_mods as cm

pygame.init()
WINDOW = (1200, 720)


def make_root(parent: Path, picker: bool = True, offered=("alpha", "beta"), installed=("alpha",)) -> Path:
    root = parent / "pkg"
    (root / "modloader").mkdir(parents=True)
    if picker:
        (root / "modloader" / "modpicker.py").write_text("", encoding="utf-8")
    (root / "mods_available").mkdir()
    for name in offered:
        (root / "mods_available" / name).mkdir()
        (root / "mods_available" / name / "mod.json").write_text("{}", encoding="utf-8")
    (root / "mods").mkdir()
    for name in installed:
        (root / "mods" / name).mkdir()
        (root / "mods" / name / "mod.json").write_text("{}", encoding="utf-8")
        (root / "mods" / name / "thing.py").write_text("x = 1\n", encoding="utf-8")
    return root


class FakeProc:
    """A picker process: ``poll`` answers None until ``finish(code)``."""

    def __init__(self):
        self.code = None

    def poll(self):
        return self.code

    def finish(self, code):
        self.code = code


class FakeFont:
    def __init__(self, log):
        self.log = log

    def render(self, text, antialias, colour):
        self.log.append(text)
        return pygame.Surface((max(1, 7 * len(text)), 14), pygame.SRCALPHA)


def click(pos, button=1, kind=pygame.MOUSEBUTTONDOWN):
    return pygame.event.Event(kind, pos=pos, button=button)


def quiet_scan(win, root):
    """What the first frame starts, without the thread: the comparison with the last start has nothing to say."""
    cm._state(win)["scan"] = {"done": True, "lines": []}


def finish_scans():
    """Let every comparison thread end before a test's folder goes."""
    import threading
    for thread in threading.enumerate():
        if thread.name == "change_mods-scan":
            thread.join(10)


class Base(unittest.TestCase):
    real_scan = False                      # True: the first frame really starts the comparison thread

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        cm.reset_for_tests()
        self.addCleanup(cm.reset_for_tests)
        self.addCleanup(finish_scans)
        self.root = make_root(self.tmp)
        patches = [mock.patch.object(cm, "_root", lambda: self.root), mock.patch.object(cm, "mods_in_effect", lambda: None)]
        if not self.real_scan:
            patches.append(mock.patch.object(cm, "_begin_scan", quiet_scan))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.win = SimpleNamespace()
        self.text: list = []
        self.fonts = (FakeFont(self.text), FakeFont(self.text), FakeFont(self.text))
        self.screen = pygame.Surface(WINDOW)

    def draw(self, mouse=(0, 0)):
        self.text.clear()
        with mock.patch("pygame.mouse.get_pos", return_value=mouse):
            cm.draw_button(self.win, self.screen, self.fonts)
        return self.win._cm_state["rect"]

    def start(self, popen=None):
        """Click the button; returns the fake picker process."""
        rect = self.draw()
        proc = FakeProc()
        with mock.patch.object(cm.subprocess, "Popen", popen or mock.Mock(return_value=proc)) as made:
            self.assertTrue(cm.handle_click(self.win, click(rect.center)))
        self.popen = made
        return proc

    def status(self):
        s = self.win._cm_state["status"]
        return s[0] if s else None


class PickerCommandTests(Base):
    def test_there_is_a_command_when_the_picker_and_mods_to_pick_from_are_there(self):
        command = cm.picker_command(self.root)
        self.assertEqual(command[1:], [str(self.root / "modloader" / "modpicker.py"), "--root", str(self.root)])

    def test_without_the_picker_there_is_no_button(self):
        (self.root / "modloader" / "modpicker.py").unlink()
        self.assertIsNone(cm.picker_command(self.root))

    def test_without_mods_to_pick_from_there_is_no_button(self):
        for path in (self.root / "mods_available").iterdir():
            (path / "mod.json").unlink()
            path.rmdir()
        self.assertIsNone(cm.picker_command(self.root))
        (self.root / "mods_available" / ".hidden").mkdir()
        self.assertIsNone(cm.picker_command(self.root))

    def test_the_dev_folder_has_neither(self):
        self.assertIsNone(cm.picker_command(self.tmp))


class FingerprintTests(Base):
    def prints(self):
        return cm.fingerprint(self.root / "mods")

    def test_nothing_changed_is_the_same(self):
        self.assertEqual(self.prints(), self.prints())

    def test_a_mod_added_or_removed_changes_it(self):
        before = self.prints()
        (self.root / "mods" / "beta").mkdir()
        (self.root / "mods" / "beta" / "mod.json").write_text("{}", encoding="utf-8")
        added = self.prints()
        self.assertNotEqual(before, added)
        (self.root / "mods" / "beta" / "mod.json").unlink()
        (self.root / "mods" / "beta").rmdir()
        self.assertEqual(self.prints(), before)
        for path in (self.root / "mods" / "alpha").iterdir():
            path.unlink()
        (self.root / "mods" / "alpha").rmdir()
        self.assertNotEqual(self.prints(), before)

    def test_a_changed_or_new_file_in_a_mod_changes_it(self):
        before = self.prints()
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2222\n", encoding="utf-8")
        self.assertNotEqual(self.prints(), before)
        again = self.prints()
        (self.root / "mods" / "alpha" / "more.patch").write_text("@@ a @@\n", encoding="utf-8")
        self.assertNotEqual(self.prints(), again)

    def test_a_changed_file_with_the_same_time_changes_it(self):
        path = self.root / "mods" / "alpha" / "thing.py"
        stat = path.stat()
        before = self.prints()
        path.write_text("x = 22222222\n", encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(self.prints(), before)

    def test_a_touched_file_with_the_same_size_changes_it(self):
        path = self.root / "mods" / "alpha" / "thing.py"
        before = self.prints()
        later = time.time() + 50
        os.utime(path, (later, later))
        self.assertNotEqual(self.prints(), before)

    def test_what_does_not_matter_does_not_count(self):
        before = self.prints()
        mod = self.root / "mods" / "alpha"
        (mod / "README.md").write_text("# a\n", encoding="utf-8")
        (mod / "__pycache__").mkdir()
        (mod / "__pycache__" / "thing.cpython-314.pyc").write_bytes(b"x")
        (mod / "thing.pyc").write_bytes(b"x")
        (mod / "screenshots").mkdir()
        (mod / "screenshots" / "a.png").write_bytes(b"x")
        (self.root / "mods" / ".replaced").mkdir()
        (self.root / "mods" / ".replaced" / "old.py").write_text("x", encoding="utf-8")
        (self.root / "mods" / "stray.txt").write_text("a file, not a mod", encoding="utf-8")
        self.assertEqual(self.prints(), before)

    def test_a_missing_folder_is_empty(self):
        self.assertEqual(cm.fingerprint(self.tmp / "nowhere"), ())


class ButtonRectTests(unittest.TestCase):
    def test_it_is_centred_under_the_card_in_the_normal_window(self):
        rect = cm.button_rect(*WINDOW)
        self.assertEqual(rect.centerx, WINDOW[0] // 2)
        self.assertGreater(rect.top, (WINDOW[1] + 510) // 2)                      # below the card (even the taller LOGIN one)
        self.assertLessEqual(rect.bottom, WINDOW[1])
        self.assertEqual((rect.w, rect.h), (cm.BUTTON_W, cm.BUTTON_H))

    def test_it_stays_inside_windows_of_other_sizes_or_is_left_out(self):
        for size in ((1920, 1080), (1280, 720), (1000, 700), (800, 640), (480, 700), (300, 700)):
            rect = cm.button_rect(*size)
            if rect is not None:
                self.assertTrue(pygame.Rect(0, 0, *size).contains(rect), size)
                self.assertGreaterEqual(rect.top, (size[1] + 510) // 2 + 4, size)

    def test_a_window_with_no_room_under_the_card_has_no_button(self):
        self.assertIsNone(cm.button_rect(1200, 560))
        self.assertIsNone(cm.button_rect(70, 720))

    def test_a_narrow_window_gets_a_narrower_button(self):
        rect = cm.button_rect(200, 720)
        self.assertEqual(rect.w, 180)


class DrawTests(Base):
    def test_the_button_is_drawn_and_remembered_where_it_is(self):
        rect = self.draw()
        self.assertEqual(rect, cm.button_rect(*WINDOW))
        self.assertIn("CHANGE MODS", self.text)
        self.assertEqual(self.screen.get_at((rect.x + 10, rect.y + 4))[:3], (8, 12, 30))               # the button's fill, inside its rounded corner

    def test_the_label_says_how_many_mods_are_on(self):
        with mock.patch.object(cm, "mods_in_effect", lambda: 22):
            self.draw()
        self.assertIn("CHANGE MODS (22 on)", self.text)

    def test_nothing_is_drawn_where_there_is_no_picker(self):
        (self.root / "modloader" / "modpicker.py").unlink()
        self.assertIsNone(self.draw())
        self.assertEqual(self.text, [])
        self.assertFalse(cm.handle_click(self.win, click((600, 640))))

    def test_while_the_picker_is_open_the_button_says_so(self):
        self.start()
        self.draw()
        self.assertIn("CHOOSING MODS...", self.text)
        self.assertNotIn("CHANGE MODS", self.text)

    def test_a_note_is_shown_under_the_button_and_goes_away(self):
        self.draw()
        cm._say(self.win, "Hello there.")
        self.draw()
        self.assertIn("Hello there.", self.text)
        with mock.patch.object(cm.time, "monotonic", return_value=time.monotonic() + cm.STATUS_S + 1):
            self.draw()
        self.assertNotIn("Hello there.", self.text)
        self.assertIsNone(self.status())


class ClickTests(Base):
    def test_a_click_on_the_button_opens_the_picker_for_this_folder(self):
        self.start()
        command = self.popen.call_args.args[0]
        self.assertEqual(command[1:], [str(self.root / "modloader" / "modpicker.py"), "--root", str(self.root)])
        self.assertEqual(self.popen.call_args.kwargs["cwd"], str(self.root))
        self.assertTrue(cm.running(self.win))

    def test_a_click_anywhere_else_or_with_another_button_does_nothing(self):
        rect = self.draw()
        with mock.patch.object(cm.subprocess, "Popen") as made:
            self.assertFalse(cm.handle_click(self.win, click((5, 5))))
            self.assertFalse(cm.handle_click(self.win, click((rect.centerx, rect.top - 40))))
            self.assertFalse(cm.handle_click(self.win, click(rect.center, button=3)))
            self.assertFalse(cm.handle_click(self.win, click(rect.center, kind=pygame.MOUSEBUTTONUP)))
            self.assertFalse(cm.handle_click(self.win, pygame.event.Event(pygame.MOUSEMOTION, pos=rect.center, rel=(0, 0), buttons=(0, 0, 0))))
        made.assert_not_called()

    def test_a_click_before_the_first_frame_does_nothing(self):
        self.assertFalse(cm.handle_click(self.win, click((600, 640))))

    def test_a_second_click_while_it_is_open_does_not_open_another(self):
        self.start()
        rect = self.draw()
        with mock.patch.object(cm.subprocess, "Popen") as made:
            self.assertTrue(cm.handle_click(self.win, click(rect.center)))               # the click is used up, nothing starts
        made.assert_not_called()

    def test_asking_for_the_picker_while_it_is_open_starts_nothing(self):
        self.start()
        with mock.patch.object(cm.subprocess, "Popen") as made:
            self.assertFalse(cm.start_picker(self.win))
        made.assert_not_called()

    def test_a_picker_that_cannot_be_started_says_so(self):
        self.start(popen=mock.Mock(side_effect=OSError("no")))
        self.assertFalse(cm.running(self.win))
        self.assertEqual(self.status(), "The mod picker could not be opened.")
        self.assertTrue(self.win._cm_state["status"][1])

    def test_the_picker_going_missing_after_the_button_was_drawn_is_told(self):
        rect = self.draw()
        (self.root / "modloader" / "modpicker.py").unlink()
        self.assertTrue(cm.handle_click(self.win, click(rect.center)))
        self.assertEqual(self.status(), "The mod picker is not in this folder.")


class OutcomeTests(Base):
    """What each way of closing the picker does."""

    def setUp(self):
        super().setUp()
        self.restarts: list = []
        self.quits: list = []
        for patcher in (mock.patch.object(cm.relaunch, "restart_after_exit",
                                          lambda **kw: self.restarts.append(kw) or self.restart_ok),
                        mock.patch.object(cm.pygame.event, "post", lambda e: self.quits.append(e))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restart_ok = True

    def change_mods(self):
        (self.root / "mods" / "beta").mkdir()
        (self.root / "mods" / "beta" / "mod.json").write_text("{}", encoding="utf-8")

    def test_the_picker_still_open_changes_nothing(self):
        self.start()
        self.draw()
        self.assertTrue(cm.running(self.win))
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.quits, [])

    def test_applied_with_other_mods_restarts_the_client(self):
        proc = self.start()
        self.change_mods()
        proc.finish(0)
        self.draw()
        self.assertFalse(cm.running(self.win))
        self.assertEqual(self.restarts, [{"root": self.root}])
        self.assertEqual([e.type for e in self.quits], [pygame.QUIT])
        self.assertEqual(self.status(), "Restarting with your mods...")

    def test_applied_with_nothing_changed_stays_on_the_login_screen(self):
        proc = self.start()
        proc.finish(0)
        self.draw()
        self.assertEqual(self.status(), "No changes.")
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.quits, [])

    def test_closed_without_applying_does_nothing_at_all(self):
        proc = self.start()
        self.change_mods()                                    # (even if something changed on disk: the player cancelled)
        proc.finish(2)
        self.draw()
        self.assertFalse(cm.running(self.win))
        self.assertIsNone(self.status())
        self.assertEqual(self.restarts, [])
        self.assertEqual(self.quits, [])

    def test_a_picker_that_crashed_points_at_its_error_file(self):
        proc = self.start()
        proc.finish(1)
        self.draw()
        self.assertIn("picker_error.txt", self.status())
        self.assertTrue(self.win._cm_state["status"][1])
        self.assertEqual(self.quits, [])

    def test_when_the_restart_cannot_be_set_up_the_game_is_not_closed(self):
        self.restart_ok = False
        proc = self.start()
        self.change_mods()
        proc.finish(0)
        self.draw()
        self.assertEqual(self.quits, [])
        self.assertEqual(self.status(), "Saved. Close the game and start it again to use the new mods.")

    def test_the_button_can_be_used_again_after_the_picker_closed(self):
        proc = self.start()
        proc.finish(2)
        self.draw()
        proc2 = FakeProc()
        rect = self.draw()
        with mock.patch.object(cm.subprocess, "Popen", mock.Mock(return_value=proc2)) as made:
            cm.handle_click(self.win, click(rect.center))
        made.assert_called_once()


class DigestTests(Base):
    def mod(self, name="alpha"):
        return self.root / "mods" / name

    def test_a_mod_has_the_same_digest_until_its_files_change(self):
        before = cm.digest(self.mod())
        self.assertEqual(before, cm.digest(self.mod()))
        (self.mod() / "thing.py").write_text("x = 2\n", encoding="utf-8")
        changed = cm.digest(self.mod())
        self.assertNotEqual(before, changed)
        (self.mod() / "new.patch").write_text("@@ a @@\n", encoding="utf-8")
        self.assertNotEqual(changed, cm.digest(self.mod()))

    def test_a_file_that_is_renamed_or_moved_changes_it(self):
        before = cm.digest(self.mod())
        (self.mod() / "thing.py").rename(self.mod() / "other.py")
        self.assertNotEqual(before, cm.digest(self.mod()))

    def test_a_mod_that_was_only_copied_again_has_the_same_digest(self):
        before = cm.digest(self.mod())
        later = time.time() + 500
        for path in self.mod().iterdir():
            os.utime(path, (later, later))                                      # (new times, same content)
        self.assertEqual(cm.digest(self.mod()), before)

    def test_what_does_not_matter_does_not_count(self):
        before = cm.digest(self.mod())
        (self.mod() / "README.md").write_text("# a\n", encoding="utf-8")
        (self.mod() / "__pycache__").mkdir()
        (self.mod() / "__pycache__" / "thing.cpython-314.pyc").write_bytes(b"x")
        (self.mod() / "thing.pyc").write_bytes(b"x")
        (self.mod() / "screenshots").mkdir()
        (self.mod() / "screenshots" / "a.png").write_bytes(b"x")
        self.assertEqual(cm.digest(self.mod()), before)

    def test_digests_lists_every_mod_and_nothing_else(self):
        (self.root / "mods" / "beta").mkdir()
        (self.root / "mods" / "beta" / "mod.json").write_text("{}", encoding="utf-8")
        (self.root / "mods" / ".replaced").mkdir()
        (self.root / "mods" / "stray.txt").write_text("not a mod", encoding="utf-8")
        got = cm.digests(self.root / "mods")
        self.assertEqual(sorted(got), ["alpha", "beta"])
        self.assertNotEqual(got["alpha"], got["beta"])
        self.assertEqual(cm.digests(self.tmp / "nowhere"), {})


class SeenFileTests(Base):
    def test_there_is_no_snapshot_before_the_first_start(self):
        self.assertIsNone(cm.read_seen(self.root))

    def test_a_snapshot_is_read_back_and_leaves_nothing_else_behind(self):
        self.assertTrue(cm.write_seen({"alpha": "abc"}, self.root))
        self.assertEqual(cm.read_seen(self.root), {"alpha": "abc"})
        self.assertEqual([p.name for p in self.root.iterdir() if "mods_seen" in p.name], [cm.SEEN_FILE])

    def test_a_newer_snapshot_replaces_the_older(self):
        cm.write_seen({"alpha": "1"}, self.root)
        cm.write_seen({"alpha": "2", "beta": "3"}, self.root)
        self.assertEqual(cm.read_seen(self.root), {"alpha": "2", "beta": "3"})

    def test_a_damaged_snapshot_is_no_snapshot(self):
        path = self.root / cm.SEEN_FILE
        for text in ("not json", "[]", '{"mods": 5}', '{"mods": {"a": 1}}', '{"mods": {"a": "x"}'):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(cm.read_seen(self.root), text)

    def test_a_snapshot_that_cannot_be_written_says_so(self):
        self.assertFalse(cm.write_seen({"a": "b"}, self.tmp / "no" / "such" / "folder"))


class DiffTests(unittest.TestCase):
    def test_updated_new_and_removed_are_told_apart_and_sorted(self):
        old = {"a": "1", "b": "1", "c": "1", "d": "1"}
        new = {"a": "1", "b": "2", "d": "9", "e": "1", "f": "1"}
        self.assertEqual(cm.diff(old, new), (["b", "d"], ["e", "f"], ["c"]))

    def test_nothing_changed_is_nothing(self):
        self.assertEqual(cm.diff({"a": "1"}, {"a": "1"}), ([], [], []))
        self.assertEqual(cm.diff({}, {}), ([], [], []))

    def test_the_note_has_a_header_and_a_line_per_kind(self):
        self.assertEqual(cm.note_lines(["quick_bars", "ui_layouts"], ["log_out"], []),
                         ["Mods changed since your last start:", "  updated: quick_bars, ui_layouts", "  new: log_out"])
        self.assertEqual(cm.note_lines([], [], ["old_mod"]), ["Mods changed since your last start:", "  removed: old_mod"])

    def test_nothing_changed_is_no_note(self):
        self.assertEqual(cm.note_lines([], [], []), [])

    def test_a_long_list_is_cut_with_a_count(self):
        names = [f"mod{i}" for i in range(9)]
        lines = cm.note_lines(names, [], [])
        self.assertEqual(lines[1], "  updated: mod0, mod1, mod2, mod3, mod4, mod5 +3 more")


class ScanTests(Base):
    def test_the_first_start_says_nothing_and_keeps_a_snapshot(self):
        self.assertEqual(cm.scan_changes(self.root), ([], [], []))
        self.assertEqual(cm.read_seen(self.root), cm.digests(self.root / "mods"))

    def test_a_start_with_nothing_changed_says_nothing(self):
        cm.scan_changes(self.root)
        self.assertEqual(cm.scan_changes(self.root), ([], [], []))

    def test_an_updated_a_new_and_a_removed_mod_are_found_and_then_not_again(self):
        (self.root / "mods" / "gamma").mkdir()
        (self.root / "mods" / "gamma" / "mod.json").write_text("{}", encoding="utf-8")
        cm.scan_changes(self.root)
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2\n", encoding="utf-8")
        (self.root / "mods" / "beta").mkdir()
        (self.root / "mods" / "beta" / "mod.json").write_text("{}", encoding="utf-8")
        (self.root / "mods" / "gamma" / "mod.json").unlink()
        (self.root / "mods" / "gamma").rmdir()
        self.assertEqual(cm.scan_changes(self.root), (["alpha"], ["beta"], ["gamma"]))
        self.assertEqual(cm.scan_changes(self.root), ([], [], []))                 # (the snapshot moved on)

    def test_a_snapshot_that_cannot_be_read_is_a_first_start(self):
        (self.root / cm.SEEN_FILE).write_text("junk", encoding="utf-8")
        self.assertEqual(cm.scan_changes(self.root), ([], [], []))
        self.assertEqual(cm.read_seen(self.root), cm.digests(self.root / "mods"))


class NoteDrawTests(Base):
    real_scan = True

    def wait_scan(self):
        holder = self.win._cm_state["scan"]
        deadline = time.monotonic() + 10
        while not holder["done"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(holder["done"])

    def start_clean(self):
        """A start that has a snapshot to compare with: draw once, let the scan finish, then change a mod for the next start."""
        cm.write_seen(cm.digests(self.root / "mods"), self.root)

    def test_a_changed_mod_is_listed_at_the_top_of_the_login_screen(self):
        self.start_clean()
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2\n", encoding="utf-8")
        self.draw()
        self.wait_scan()
        self.draw()
        self.assertIn("Mods changed since your last start:", self.text)
        self.assertIn("  updated: alpha", self.text)

    def test_nothing_is_listed_when_nothing_changed_or_on_the_first_start(self):
        self.start_clean()
        self.draw()
        self.wait_scan()
        self.draw()
        self.assertNotIn("Mods changed since your last start:", self.text)
        other = SimpleNamespace()
        (self.root / cm.SEEN_FILE).unlink()
        cm_state_before = self.win
        self.win = other
        self.draw()
        self.wait_scan()
        self.draw()
        self.assertNotIn("Mods changed since your last start:", self.text)
        self.win = cm_state_before

    def test_the_note_goes_away_after_a_while(self):
        self.start_clean()
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2\n", encoding="utf-8")
        self.draw()
        self.wait_scan()
        self.draw()
        self.assertIn("  updated: alpha", self.text)
        with mock.patch.object(cm.time, "monotonic", return_value=time.monotonic() + cm.NOTE_S + 1):
            self.draw()
        self.assertNotIn("  updated: alpha", self.text)

    def test_the_scan_is_started_once(self):
        self.start_clean()
        with mock.patch.object(cm.threading, "Thread") as thread:
            self.draw()
            self.draw()
            self.draw()
        self.assertEqual(thread.call_count, 1)

    def test_nothing_is_scanned_or_drawn_where_there_is_no_picker(self):
        (self.root / "modloader" / "modpicker.py").unlink()
        with mock.patch.object(cm.threading, "Thread") as thread:
            self.draw()
        thread.assert_not_called()
        self.assertFalse((self.root / cm.SEEN_FILE).exists())

    def test_a_window_too_short_to_leave_room_above_the_card_skips_the_note(self):
        self.start_clean()
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2\n", encoding="utf-8")
        self.screen = pygame.Surface((1200, 600))
        self.draw()
        self.wait_scan()
        self.draw()
        self.assertNotIn("Mods changed since your last start:", self.text)

    def test_a_scan_that_fails_is_logged_and_the_login_screen_goes_on(self):
        with mock.patch.object(cm, "scan_changes", side_effect=RuntimeError("boom")), self.assertLogs("change_mods", level="ERROR"):
            self.draw()
            self.wait_scan()
        self.draw()
        self.assertIn("CHANGE MODS", self.text)

    def test_the_note_is_there_even_while_the_picker_is_open(self):
        self.start_clean()
        (self.root / "mods" / "alpha" / "thing.py").write_text("x = 2\n", encoding="utf-8")
        self.draw()
        self.wait_scan()
        self.start()
        self.draw()
        self.assertIn("  updated: alpha", self.text)
        self.assertIn("CHOOSING MODS...", self.text)


class SnapshotOnRestartTests(Base):
    def test_the_players_own_choice_is_not_reported_at_the_next_start(self):
        cm.write_seen(cm.digests(self.root / "mods"), self.root)
        (self.root / "mods" / "beta").mkdir()                                     # what the picker just did
        (self.root / "mods" / "beta" / "mod.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(cm.relaunch, "restart_after_exit", return_value=True), \
                mock.patch.object(cm.pygame.event, "post"):
            cm.restart(self.win)
        self.assertEqual(cm.read_seen(self.root), cm.digests(self.root / "mods"))
        self.assertEqual(cm.scan_changes(self.root), ([], [], []))

    def test_a_snapshot_that_cannot_be_kept_does_not_stop_the_restart(self):
        with mock.patch.object(cm, "write_seen", side_effect=RuntimeError("boom")), self.assertLogs("change_mods", level="ERROR"), \
                mock.patch.object(cm.relaunch, "restart_after_exit", return_value=True) as restart, \
                mock.patch.object(cm.pygame.event, "post"):
            cm.restart(self.win)
        restart.assert_called_once()


class HookTests(unittest.TestCase):
    """On the game's real login screen."""

    @classmethod
    def setUpClass(cls):
        import login
        cls.login = login

        class Hooked(login.LoginScreen):
            pass

        cls.Hooked = Hooked
        cls.problems = cm.install(Hooked)

    def setUp(self):
        cm.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(finish_scans)
        self.root = make_root(Path(self._tmp.name))
        for patcher in (mock.patch.object(cm, "_root", lambda: self.root), mock.patch.object(cm, "mods_in_effect", lambda: None),
                        mock.patch.object(cm, "_begin_scan", quiet_scan)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.screen_obj = self.Hooked(lambda _line: None, lambda _line: None)
        self.screen_obj._screen = "login"
        self.surface = pygame.Surface(WINDOW)
        self.fonts = (pygame.font.Font(None, 28), pygame.font.Font(None, 18), pygame.font.Font(None, 14))

    def draw(self):
        with mock.patch.object(self.Hooked, "_draw_background", lambda self_, screen: None):
            self.screen_obj.draw(self.surface, self.fonts)

    def test_installing_reports_no_problem_and_is_idempotent(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(cm.install(self.Hooked), [])
        self.assertTrue(self.Hooked.__dict__["_change_mods_installed"])

    def test_the_real_login_screen_draws_the_button_under_its_card(self):
        self.draw()
        rect = self.screen_obj._cm_state["rect"]
        self.assertEqual(rect, cm.button_rect(*WINDOW))
        self.assertTrue(self.surface.get_rect().contains(rect))

    def test_the_real_login_screen_hands_the_click_to_the_button(self):
        self.draw()
        rect = self.screen_obj._cm_state["rect"]
        with mock.patch.object(cm.subprocess, "Popen", mock.Mock(return_value=FakeProc())) as made:
            self.screen_obj.handle_event(click(rect.center), *WINDOW)
        made.assert_called_once()

    def test_the_card_still_works_as_before(self):
        self.draw()
        field = next(iter(self.screen_obj._field_rects.items()))
        with mock.patch.object(cm.subprocess, "Popen") as made:
            self.screen_obj._field_focus = None
            self.screen_obj.handle_event(click(field[1].center), *WINDOW)
        made.assert_not_called()
        self.assertEqual(self.screen_obj._field_focus, field[0])

    def test_the_connecting_screen_has_the_button_too(self):
        self.screen_obj._screen = "connecting"
        self.draw()
        self.assertIsNotNone(self.screen_obj._cm_state["rect"])

    def test_an_error_in_the_button_switches_it_off_and_the_login_screen_goes_on(self):
        with mock.patch.object(cm, "draw_button", side_effect=RuntimeError("boom")), self.assertLogs("change_mods", level="ERROR"):
            self.draw()
        self.assertFalse(cm.enabled())
        self.draw()                                           # still draws the card (and the button stays off)

    def test_the_environment_switch_turns_it_off(self):
        with mock.patch.dict(os.environ, {cm.ENV_OFF: "1"}):
            self.draw()
        self.assertIsNone(self.screen_obj.__dict__.get("_cm_state"))


class GamePatchTests(unittest.TestCase):
    def test_the_patched_client_installs_the_hooks_right_after_the_login_import(self):
        source = modloader.game_source("Client.py")
        marker = "from login import LoginScreen, MUSIC_LOGIN\nimport change_mods"
        self.assertIn(marker, source)
        self.assertIn("change_mods.install(LoginScreen)", source)

    def test_the_login_methods_the_hooks_go_on_exist_with_the_arguments_the_hooks_expect(self):
        import inspect
        import login
        self.assertEqual(list(inspect.signature(login.LoginScreen.draw).parameters), ["self", "screen", "fonts"])
        self.assertEqual(list(inspect.signature(login.LoginScreen.handle_event).parameters), ["self", "ev", "ww", "wh"])

    def test_the_app_draws_and_feeds_the_login_screen_the_way_the_hooks_assume(self):
        source = modloader.game_source("Client.py")
        self.assertIn("self._login.handle_event(ev, ww, wh)", source)
        self.assertIn("self._login.draw(screen, (font_big, font_med, font_sm))", source)


if __name__ == "__main__":
    unittest.main()
