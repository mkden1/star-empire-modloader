"""chat_suggest.py: the system-name list that opens while you type ``/ap`` in the chat line.

The ranking is pure and tested directly. The keys, clicks, draw and hooks run on the real SolarSystemWindow class
(built without its init: only what these paths read), and the list's candidates are checked against the game's own
``_engage_route_autopilot_to`` so a suggestion is exactly a system ``/ap`` accepts."""
import modloader
import logging
import os
import threading
import unittest
from pathlib import Path
from unittest import mock

import pygame

import chat_suggest as cs
from gui_window_manager import GuiWindowManager

SCREEN = (1280, 800)
INPUT_RECT = pygame.Rect(120, 560, 300, 22)      # the chat line's text area (render_mixin publishes it)
SEND_RECT = pygame.Rect(40, 560, 78, 22)


def setUpModule():
    pygame.init()
    pygame.display.set_mode(SCREEN)


def gate(target):
    return {"target_system": target, "x": 0.0, "y": 0.0}


def galaxy():
    """Home is where the ship is. Alpha Centauri and Alcor are 1 jump away; Zeta Alpha and Beta Hydri 2; Alpheratz 3.
    Island is explored but not connected; Mystery is only named by a gate (never explored)."""
    return {
        "Home": {"warp_gates": [gate("Alpha Centauri"), gate("Alcor")]},
        "Alpha Centauri": {"warp_gates": [gate("Home"), gate("Zeta Alpha"), gate("Beta Hydri")]},
        "Alcor": {"warp_gates": [gate("Home")]},
        "Zeta Alpha": {"warp_gates": [gate("Alpha Centauri"), gate("Alpheratz")]},
        "Beta Hydri": {"warp_gates": [gate("Alpha Centauri"), gate("Mystery")]},
        "Alpheratz": {"warp_gates": [gate("Zeta Alpha")]},
        "Island": {"warp_gates": []},
    }


def names(items):
    return [item.name for item in items]


class ParseTests(unittest.TestCase):
    def test_only_an_ap_command_with_a_space_after_it_is_a_name_being_typed(self):
        for text, expected in (("/ap ", ""), ("/ap alp", "alp"), ("/AP Alpha  Cen", "Alpha Cen"),
                               ("/ap   Alpha", "Alpha"), ("/ap\tAlpha", "Alpha")):
            with self.subTest(text=text):
                self.assertEqual(cs.parse_command(text), expected)
        for text in ("/ap", "/apple", "/ap1", "ap alpha", "hello /ap x", "", "/w ap x", "/layouts al", None):
            with self.subTest(text=text):
                self.assertIsNone(cs.parse_command(text))


class RankTests(unittest.TestCase):
    SYSTEMS = {"Alpha Centauri": 1, "Alcor": 1, "Zeta Alpha": 2, "Beta Hydri": 2, "Alpheratz": 3, "Home Alone": 4}

    def test_favourites_come_before_every_other_system_in_the_players_own_order(self):
        got = cs.rank("", self.SYSTEMS, ["Zeta Alpha", "Alcor"])
        self.assertEqual(names(got)[:2], ["Zeta Alpha", "Alcor"])
        self.assertEqual([item.favourite for item in got], [True, True] + [False] * 4)

    def test_other_systems_follow_alphabetically(self):
        got = cs.rank("", self.SYSTEMS, ["Zeta Alpha"])
        self.assertEqual(names(got)[1:], ["Alcor", "Alpha Centauri", "Alpheratz", "Beta Hydri", "Home Alone"])

    def test_a_name_that_starts_with_the_text_beats_a_word_that_does_which_beats_a_name_that_contains_it(self):
        systems = {"Cinder Alpha": 1, "Malpais": 1, "Alpha": 1, "Alphabet": 1}
        got = cs.rank("alp", systems, [])
        self.assertEqual(names(got), ["Alpha", "Alphabet", "Cinder Alpha", "Malpais"])

    def test_within_the_favourites_a_better_match_still_goes_first(self):
        got = cs.rank("al", self.SYSTEMS, ["Zeta Alpha", "Alcor"])
        self.assertEqual(names(got)[:2], ["Alcor", "Zeta Alpha"])          # Alcor starts with it, Zeta Alpha has a word that does
        self.assertEqual(names(got)[2:], ["Alpha Centauri", "Alpheratz", "Home Alone"])      # the word "alone" starts with it

    def test_a_favourite_that_matches_worse_still_beats_a_non_favourite_that_matches_better(self):
        got = cs.rank("alp", {"Alpha": 1, "Zeta Alpha": 2}, ["Zeta Alpha"])
        self.assertEqual(names(got), ["Zeta Alpha", "Alpha"])

    def test_matching_ignores_case_and_extra_spaces(self):
        self.assertEqual(names(cs.rank("  ALPHA   cen ", self.SYSTEMS, [])), ["Alpha Centauri"])
        self.assertEqual(names(cs.rank("alpha", self.SYSTEMS, ["ZETA ALPHA"])), ["Zeta Alpha", "Alpha Centauri"])

    def test_nothing_matches_gives_nothing(self):
        self.assertEqual(cs.rank("xyz", self.SYSTEMS, ["Alcor"]), [])

    def test_a_hyphen_or_apostrophe_starts_a_word(self):
        self.assertEqual(names(cs.rank("rho", {"Alpha-Rho": 1, "Brho": 1}, [])), ["Alpha-Rho", "Brho"])
        self.assertEqual(names(cs.rank("gu", {"Ka'gu": 1, "Zzgu": 1}, [])), ["Ka'gu", "Zzgu"])

    def test_the_jump_count_travels_with_the_suggestion_and_far_ones_are_flagged(self):
        got = {item.name: item for item in cs.rank("", self.SYSTEMS, [], max_hops=2)}
        self.assertEqual((got["Alcor"].hops, got["Alcor"].too_far), (1, False))
        self.assertEqual((got["Beta Hydri"].hops, got["Beta Hydri"].too_far), (2, False))
        self.assertEqual((got["Alpheratz"].hops, got["Alpheratz"].too_far), (3, True))
        self.assertFalse(any(item.too_far for item in cs.rank("", self.SYSTEMS, [])))      # no limit known

    def test_the_typed_text_is_located_for_highlighting(self):
        got = {item.name: item for item in cs.rank("cen", {"Alpha Centauri": 1, "Zcenta": 1}, [])}
        self.assertEqual((got["Alpha Centauri"].start, got["Alpha Centauri"].length), (6, 3))
        self.assertEqual((got["Zcenta"].start, got["Zcenta"].length), (1, 3))
        self.assertEqual(cs.rank("", {"A": 1}, [])[0].start, -1)

    def test_limit_and_a_sole_exact_match(self):
        self.assertEqual(len(cs.rank("", self.SYSTEMS, [], limit=3)), 3)
        self.assertTrue(cs.only_match_is_typed("alcor", cs.rank("alcor", self.SYSTEMS, [])))
        self.assertFalse(cs.only_match_is_typed("alpha", cs.rank("alpha", {"Alpha": 1, "Alpha Two": 2}, [])))
        self.assertFalse(cs.only_match_is_typed("alc", cs.rank("alc", self.SYSTEMS, [])))
        self.assertFalse(cs.only_match_is_typed("", []))


class JumpCountTests(unittest.TestCase):
    def test_the_breadth_first_walk_the_autopilot_makes(self):
        self.assertEqual(cs.jump_counts(galaxy(), "Home"),
                         {"Alpha Centauri": 1, "Alcor": 1, "Zeta Alpha": 2, "Beta Hydri": 2, "Alpheratz": 3})

    def test_unconnected_and_never_explored_systems_are_left_out(self):
        counts = cs.jump_counts(galaxy(), "Home")
        self.assertNotIn("Island", counts)                  # explored, but no route reaches it
        self.assertNotIn("Mystery", counts)                 # only named by a gate: not routable by /ap
        self.assertNotIn("Home", counts)

    def test_the_route_goes_through_explored_gates_only(self):
        systems = galaxy()
        del systems["Alpha Centauri"]                       # forget the hub: nothing past it is reachable
        self.assertEqual(cs.jump_counts(systems, "Home"), {"Alcor": 1})


class Bench(unittest.TestCase):
    """A real SolarSystemWindow with the state the suggestion paths read."""

    may_trip_the_switch = False          # the safety tests provoke an error on purpose

    def setUp(self):
        cs.reset_for_tests()
        self.addCleanup(cs.reset_for_tests)
        self.addCleanup(self.still_on)       # runs before the reset above
        import Client
        win = Client.SolarSystemWindow.__new__(Client.SolarSystemWindow)
        win._lock = threading.RLock()
        win._uk = 1.0
        win._s = lambda value: int(value)
        win._chat_font_scale = 1.0
        fonts = {}
        win._instrument_font = lambda size, bold=False: fonts.setdefault(
            (size, bold), pygame.font.SysFont("consolas", size, bold=bold))
        win._label_cache, win._fill_surf_cache, win._hud_plate_cache = {}, {}, {}
        win._gui_windows = GuiWindowManager()
        win._layout = {"system_name": "Home"}
        win._last_solar_system_name = "Home"
        win._visited_systems = galaxy()
        win._csw_arena_map_sources = None
        win._gm_favorites = []
        win._player_skills = []
        win._coalition_state = {}
        win._chat_focused = True
        win._chat_popup = None
        win._chat_input_text_rect = pygame.Rect(INPUT_RECT)
        win._chat_send_rect = pygame.Rect(SEND_RECT)
        win._mission_max_jumps = lambda: 5
        self.win = win
        self.screen = pygame.Surface(SCREEN, pygame.SRCALPHA)

    def still_on(self):
        """An error inside the hooks is swallowed (the chat must carry on), so a test could pass over one."""
        if not self.may_trip_the_switch:
            self.assertFalse(cs.has_failed(), "a hook raised and switched the feature off")

    def type(self, text):
        self.win._chat_input = text
        self.win._chat_cursor = len(text)

    def key(self, key, mod=0, unicode=""):
        return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode=unicode)

    def frame(self, *events):
        """One pass of the hook the way Client.py makes it; returns the events the game would still see."""
        return cs.filter_events(self.win, list(events))

    def state(self):
        return cs._state(self.win)

    def shown(self):
        return names(cs.visible_items(self.state()))

    def open(self, text="/ap ", favourites=()):
        self.win._gm_favorites = list(favourites)
        self.type(text)
        self.frame()
        return self.shown()


class OpeningTests(Bench):
    def test_it_opens_on_slash_ap_with_the_favourites_first(self):
        self.assertEqual(self.open("/ap ", ["Zeta Alpha"]),
                         ["Zeta Alpha", "Alcor", "Alpha Centauri", "Alpheratz", "Beta Hydri"])
        self.assertTrue(cs.active(self.win))

    def test_it_narrows_as_you_type(self):
        self.assertEqual(self.open("/ap al"), ["Alcor", "Alpha Centauri", "Alpheratz", "Zeta Alpha"])
        self.type("/ap alph")
        self.frame()
        self.assertEqual(self.shown(), ["Alpha Centauri", "Alpheratz", "Zeta Alpha"])

    def test_the_current_system_and_unreachable_ones_are_never_offered(self):
        shown = self.open("/ap ")
        self.assertNotIn("Home", shown)
        self.assertNotIn("Island", shown)
        self.assertNotIn("Mystery", shown)

    def test_it_stays_closed_for_anything_else(self):
        for text in ("", "/ap", "hello", "/apple x", "/w al", "/market"):
            with self.subTest(text=text):
                self.open(text)
                self.assertFalse(cs.active(self.win))

    def test_it_stays_closed_while_the_chat_line_is_not_focused_or_the_channel_list_is_open(self):
        self.type("/ap al")
        self.win._chat_focused = False
        self.frame()
        self.assertFalse(cs.active(self.win))
        self.win._chat_focused = True
        self.win._chat_popup = {"kind": "send"}
        self.frame()
        self.assertFalse(cs.active(self.win))
        self.win._chat_popup = None
        self.frame()
        self.assertTrue(cs.active(self.win))

    def test_a_name_that_is_complete_and_the_only_match_closes_it(self):
        self.open("/ap alcor")
        self.assertFalse(cs.active(self.win))
        self.open("/ap alpha")
        self.assertTrue(cs.active(self.win))               # Alpha Centauri / Alpheratz / Zeta Alpha

    def test_nothing_matching_says_so_and_offers_nothing(self):
        self.assertEqual(self.open("/ap qqq"), [])
        self.assertTrue(cs.active(self.win))
        self.assertTrue(self.state().empty_hint)
        self.assertEqual(self.frame(self.key(pygame.K_TAB)), [self.key(pygame.K_TAB)])     # nothing to complete

    def test_systems_beyond_the_jump_limit_are_offered_but_flagged(self):
        self.win._mission_max_jumps = lambda: 2
        self.open("/ap alph")
        far = [item for item in cs.visible_items(self.state()) if item.too_far]
        self.assertEqual(names(far), ["Alpheratz"])

    def test_an_unknown_jump_limit_flags_nothing(self):
        self.win._mission_max_jumps = mock.Mock(side_effect=RuntimeError("no skills yet"))
        self.open("/ap ")
        self.assertFalse(any(item.too_far for item in cs.visible_items(self.state())))

    def test_the_list_shows_at_most_eight_rows(self):
        self.win._visited_systems = {"Home": {"warp_gates": [gate(f"S{n:02d}") for n in range(20)]},
                                     **{f"S{n:02d}": {"warp_gates": [gate("Home")]} for n in range(20)}}
        self.open("/ap ")
        self.assertEqual(len(self.shown()), cs.MAX_ROWS)
        self.assertEqual(len(self.state().items), 20)

    def test_it_follows_a_change_of_favourites_within_a_second(self):
        self.open("/ap ", [])
        self.win._gm_favorites = ["Beta Hydri"]
        with mock.patch.object(cs.time, "monotonic", return_value=self.state().built_at + cs.REFRESH_S + 0.1):
            self.frame()
        self.assertEqual(self.shown()[0], "Beta Hydri")


class KeyTests(Bench):
    def test_tab_completes_the_first_suggestion_and_swallows_the_key(self):
        self.open("/ap al", ["Alpheratz"])
        self.assertEqual(self.frame(self.key(pygame.K_TAB)), [])
        self.assertEqual((self.win._chat_input, self.win._chat_cursor), ("/ap Alpheratz", len("/ap Alpheratz")))

    def test_down_highlights_and_tab_completes_the_highlighted_one(self):
        self.open("/ap al")                                          # Alcor, Alpha Centauri, Alpheratz, Zeta Alpha
        self.assertEqual(self.frame(self.key(pygame.K_DOWN), self.key(pygame.K_DOWN)), [])
        self.assertEqual(self.state().selected, 1)
        self.frame(self.key(pygame.K_TAB))
        self.assertEqual(self.win._chat_input, "/ap Alpha Centauri")

    def test_down_stops_at_the_last_row_and_up_walks_back_then_lets_history_have_the_key(self):
        self.open("/ap alph")                                        # three rows
        self.frame(*[self.key(pygame.K_DOWN)] * 6)
        self.assertEqual(self.state().selected, 2)
        self.frame(self.key(pygame.K_UP))
        self.assertEqual(self.state().selected, 1)
        self.frame(self.key(pygame.K_UP), self.key(pygame.K_UP))
        self.assertIsNone(self.state().selected)
        up = self.key(pygame.K_UP)
        self.assertEqual(self.frame(up), [up])                       # nothing highlighted: the game's message recall

    def test_shift_tab_takes_the_last_row_or_the_one_before_the_highlight(self):
        self.open("/ap alph")                                        # Alpha Centauri, Alpheratz, Zeta Alpha
        self.frame(self.key(pygame.K_TAB, mod=pygame.KMOD_SHIFT))
        self.assertEqual(self.win._chat_input, "/ap Zeta Alpha")

    def test_ctrl_or_alt_tab_is_not_ours(self):
        self.open("/ap al")
        tab = self.key(pygame.K_TAB, mod=pygame.KMOD_CTRL)
        self.assertEqual(self.frame(tab), [tab])
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_enter_with_a_highlighted_system_completes_it_and_still_reaches_the_chat_to_send(self):
        self.open("/ap al")
        self.frame(self.key(pygame.K_DOWN), self.key(pygame.K_DOWN))
        enter = self.key(pygame.K_RETURN)
        self.assertEqual(self.frame(enter), [enter])                 # the game's Enter now sends the completed line
        self.assertEqual(self.win._chat_input, "/ap Alpha Centauri")

    def test_enter_with_nothing_highlighted_sends_exactly_what_was_typed(self):
        self.open("/ap al")
        enter = self.key(pygame.K_KP_ENTER)
        self.assertEqual(self.frame(enter), [enter])
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_other_keys_and_escape_go_to_the_chat_untouched(self):
        self.open("/ap al")
        keys = [self.key(pygame.K_ESCAPE), self.key(pygame.K_LEFT), self.key(pygame.K_a, unicode="a")]
        self.assertEqual(self.frame(*keys), keys)

    def test_a_selection_is_dropped_when_the_line_changes(self):
        self.open("/ap al")
        self.frame(self.key(pygame.K_DOWN))
        self.assertEqual(self.state().selected, 0)
        self.type("/ap alp")
        self.frame()
        self.assertIsNone(self.state().selected)

    def test_when_the_line_is_edited_earlier_in_the_same_frame_the_list_is_out_of_date_and_keys_pass(self):
        self.open("/ap al")
        typed = self.key(pygame.K_c, unicode="c")
        tab = self.key(pygame.K_TAB)
        down = self.key(pygame.K_DOWN)
        self.assertEqual(self.frame(typed, tab, down), [typed, tab, down])
        self.assertEqual(self.win._chat_input, "/ap al")
        text = pygame.event.Event(pygame.TEXTINPUT, text="c")
        self.assertEqual(self.frame(text, tab), [text, tab])

    def test_shift_pressed_in_the_same_frame_as_tab_does_not_count_as_editing(self):
        self.open("/ap al")
        shift = self.key(pygame.K_LSHIFT, mod=pygame.KMOD_SHIFT)
        self.assertEqual(self.frame(shift, self.key(pygame.K_TAB, mod=pygame.KMOD_SHIFT)), [shift])
        self.assertEqual(self.win._chat_input, "/ap Zeta Alpha")

    def test_with_no_list_every_key_goes_to_the_chat(self):
        self.type("hello")
        keys = [self.key(pygame.K_TAB), self.key(pygame.K_DOWN), self.key(pygame.K_UP), self.key(pygame.K_RETURN)]
        events = list(keys)
        self.assertIs(self.frame(*events) is events or self.frame(*events) == keys, True)
        self.assertEqual(self.frame(*keys), keys)


class PointerTests(Bench):
    def setUp(self):
        super().setUp()
        self.open("/ap al", ["Alpheratz"])
        cs.queue(self.win, mock.Mock(screen=self.screen))             # lays the list out, as the frame does

    def click(self, pos, button=1):
        return pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)

    def test_the_list_sits_above_the_chat_line_and_inside_the_screen(self):
        rect = self.state().rect
        self.assertLess(rect.bottom, INPUT_RECT.top)
        self.assertEqual(rect.left, SEND_RECT.left)
        self.assertTrue(self.screen.get_rect().contains(rect))
        self.assertEqual(len(self.state().rows), 4)

    def test_it_opens_below_the_chat_line_when_there_is_no_room_above(self):
        self.win._chat_input_text_rect = pygame.Rect(120, 4, 300, 22)
        self.win._chat_send_rect = pygame.Rect(40, 4, 78, 22)
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertGreater(self.state().rect.top, 4 + 22)

    def test_a_click_on_a_row_completes_it_keeps_the_focus_and_is_not_passed_on(self):
        row, suggestion = self.state().rows[1]
        self.assertEqual(suggestion.name, "Alcor")
        self.assertEqual(self.frame(self.click(row.center)), [])
        self.assertEqual(self.win._chat_input, "/ap Alcor")
        self.assertTrue(self.win._chat_focused)

    def test_a_click_on_the_header_or_between_rows_is_swallowed_without_completing(self):
        rect = self.state().rect
        self.assertEqual(self.frame(self.click((rect.x + 5, rect.y + 3))), [])
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_a_right_click_on_the_list_is_swallowed_but_does_not_complete(self):
        row, _ = self.state().rows[0]
        self.assertEqual(self.frame(self.click(row.center, button=3)), [])
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_a_click_elsewhere_is_left_to_the_game(self):
        click = self.click((900, 100))
        self.assertEqual(self.frame(click), [click])

    def test_the_mouse_wheel_over_the_list_does_not_scroll_the_chat(self):
        wheel = pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1)
        with mock.patch.object(pygame.mouse, "get_pos", return_value=self.state().rect.center):
            self.assertEqual(self.frame(wheel), [])
        with mock.patch.object(pygame.mouse, "get_pos", return_value=(900, 100)):
            self.assertEqual(self.frame(wheel), [wheel])


class DrawTests(Bench):
    def test_the_list_registers_as_a_retained_popover_only_while_it_is_open(self):
        self.open("/ap al")
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertTrue(self.win._gui_windows._draw_queue)
        self.win._gui_windows._draw_queue.clear()
        self.open("/ap")
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertFalse(self.win._gui_windows._draw_queue)
        self.assertIsNone(self.state().rect)

    def test_drawing_paints_inside_the_list_and_nowhere_else(self):
        self.open("/ap al", ["Alpheratz"])
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.screen.fill((0, 0, 0, 0))
        cs.draw(self.win, self.screen)
        painted = pygame.mask.from_surface(self.screen, 1).get_bounding_rects()
        self.assertTrue(painted)
        rect = self.state().rect
        for part in painted:
            self.assertTrue(rect.inflate(2, 2).contains(part), (part, rect))

    def test_the_empty_message_and_the_more_line_draw_without_error(self):
        self.open("/ap qqq")
        cs.queue(self.win, mock.Mock(screen=self.screen))
        cs.draw(self.win, self.screen)
        self.win._visited_systems = {"Home": {"warp_gates": [gate(f"S{n:02d}") for n in range(20)]},
                                     **{f"S{n:02d}": {"warp_gates": [gate("Home")]} for n in range(20)}}
        self.open("/ap ")
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertEqual(len(self.state().rows), cs.MAX_ROWS)
        cs.draw(self.win, self.screen)

    def test_nothing_is_laid_out_before_the_chat_line_has_been_drawn_once(self):
        self.win._chat_input_text_rect = None
        self.open("/ap al")
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertIsNone(self.state().rect)


class AgreesWithTheGameTests(Bench):
    """A suggestion is exactly a system the real ``/ap`` accepts, with the jump count it will plot."""

    def route_to(self, name, limit=5):
        win = self.win
        win._mission_max_jumps = lambda: limit
        logged = []
        win.chat_log = lambda text, *args: logged.append(text)
        win._advance_autopilot_route = lambda: None
        win._galaxy_map_route_preview = None
        win._autopilot_route = []
        ok = win._engage_route_autopilot_to(name, close_map=False, announce_same_system=True)
        return ok, list(win._autopilot_route), logged

    def test_every_suggested_system_is_routed_with_the_jump_count_shown(self):
        self.open("/ap ")
        self.assertTrue(self.state().items)
        for item in self.state().items:
            with self.subTest(system=item.name):
                ok, route, _logged = self.route_to(item.name)
                self.assertTrue(ok)
                self.assertEqual(len(route), item.hops)
                self.assertEqual(route[-1], item.name)

    def test_a_flagged_system_is_one_the_game_refuses_for_distance(self):
        self.win._mission_max_jumps = lambda: 2
        self.open("/ap alph")
        far = [item for item in self.state().items if item.too_far]
        self.assertEqual(names(far), ["Alpheratz"])
        ok, _route, logged = self.route_to("Alpheratz", limit=2)
        self.assertFalse(ok)
        self.assertIn("jumps away", " ".join(logged))

    def test_the_systems_left_out_are_ones_the_game_does_not_route_to(self):
        self.open("/ap ")
        offered = {item.name for item in self.state().items} | {"Home"}
        for name in set(galaxy()) | {"Mystery"}:
            if name in offered:
                continue
            with self.subTest(system=name):
                self.assertFalse(self.route_to(name)[0])

    def test_the_current_system_is_the_game_saying_you_are_already_there(self):
        ok, _route, logged = self.route_to("Home")
        self.assertFalse(ok)
        self.assertIn("already in Home", " ".join(logged))

    def test_the_completed_line_is_what_the_chat_command_parses(self):
        # Client.py takes the name from _msg[3:] with whitespace collapsed: "/ap " + name must give the name back.
        for name in ("Alpha Centauri", "Alcor"):
            self.assertEqual(" ".join(cs.complete_line(name)[3:].split()), name)


class SafetyTests(Bench):
    may_trip_the_switch = True

    def test_an_error_switches_it_off_and_the_events_go_through_untouched(self):
        self.type("/ap al")
        tab = self.key(pygame.K_TAB)
        with mock.patch.object(cs, "rank", side_effect=RuntimeError("boom")):
            with self.assertLogs(cs.logger, logging.ERROR):
                events = [tab]
                self.assertIs(cs.filter_events(self.win, events), events)
        self.assertTrue(cs.has_failed())
        self.assertEqual(self.frame(tab), [tab])
        cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertFalse(self.win._gui_windows._draw_queue)
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_a_queue_error_switches_it_off_too(self):
        self.type("/ap al")
        with mock.patch.object(cs, "layout", side_effect=RuntimeError("boom")):
            with self.assertLogs(cs.logger, logging.ERROR):
                cs.queue(self.win, mock.Mock(screen=self.screen))
        self.assertTrue(cs.has_failed())

    def test_the_environment_switch_turns_it_off(self):
        self.type("/ap al")
        tab = self.key(pygame.K_TAB)
        with mock.patch.dict(os.environ, {cs.ENV_OFF: "1"}):
            self.assertEqual(self.frame(tab), [tab])
            cs.queue(self.win, mock.Mock(screen=self.screen))
            self.assertFalse(cs.active(self.win))
        self.assertEqual(self.win._chat_input, "/ap al")

    def test_a_window_without_the_route_helpers_still_works_from_its_visited_systems(self):
        class Plain:
            pass

        win = Plain()
        win._layout = {"system_name": "Home"}
        win._visited_systems = galaxy()
        win._lock = threading.RLock()
        systems, limit = cs._candidates(win)
        self.assertEqual(systems["Alcor"], 1)
        self.assertIsNone(limit)


class HookSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = modloader.game_source("Client.py")

    def test_the_three_hook_lines_are_in_client_py_in_the_right_places(self):
        pump = self.source.index("_frame_events = pump_watch.get_events()")
        filt = self.source.index("chat_suggest.filter_events(self, _frame_events)")
        # the game's loop over them: `for event in _frame_events:` (v0.5.84) or `enumerate(_frame_events)` (v0.5.86)
        loop = min(i for i in (self.source.find("for event in _frame_events", pump),
                               self.source.find("for _index, event in enumerate(_frame_events):", pump)) if i >= 0)
        self.assertLess(pump, filt)
        self.assertLess(filt, loop)                      # before the game reads any event
        flush = self.source.index("self._gui_windows.flush_draw_queue(")
        queue = self.source.index("chat_suggest.queue(self, _frame)")
        self.assertLess(queue, flush)
        self.assertIn("import chat_suggest", self.source)

    def test_the_game_still_handles_the_chat_line_the_way_the_hook_assumes(self):
        # Enter sends self._chat_input, Up / Down recall history, and /ap takes the name from _msg[3:].
        self.assertIn("_msg = self._chat_input.strip()", self.source)
        self.assertIn("elif event.key == pygame.K_UP:", self.source)
        self.assertIn("self._chat_history_recall(-1)", self.source)
        self.assertIn('_ap_dest = " ".join(_msg[3:].split())', self.source)
        self.assertIn('elif _cmd == "ap":', self.source)

    def test_the_route_rules_the_candidates_copy_are_still_the_games(self):
        for needle in ("_by_lower = {k.lower(): k for k in _visited_snap}",
                       "_visited_snap = self._route_systems_snapshot(_cur_layout)",
                       "_route_origin = self._resolve_route_origin_system(",
                       "_max_hops = self._mission_max_jumps()",
                       'for _g in _sd.get("warp_gates", []):',
                       "_gm_favorites"):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.source)

    def test_the_chat_line_still_publishes_the_rects_the_list_anchors_to(self):
        render = modloader.game_source("render_mixin.py")
        self.assertIn("self._chat_input_text_rect = pygame.Rect(", render)
        self.assertIn("self._chat_send_rect = _snd_r", render)


if __name__ == "__main__":
    unittest.main()
