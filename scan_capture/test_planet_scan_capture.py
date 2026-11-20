"""planet_scan_capture: each planet / moon scan is recorded, and the galaxy map's scan indexes kept live, by a hook that
modloader puts on the game's own ``_on_planet_scan_result`` (what used to be two blocks of patch text in protocol.py)."""
import json
import unittest
from unittest import mock

import Client  # noqa: F401  (its last lines install the hook and fill in the borrowed _loads)
import modloader
import planet_scan_capture as psc
from protocol import ProtocolMixin

LAYOUT = {"planets": [
    {"name": "Earth", "id": 1, "orbit_radius": 5.0, "orbit_speed": 0.1, "orbit_angle": 2.0},
    {"name": "Luna", "id": 2, "is_moon": True, "parent_planet_id": 1, "orbit_radius": 1.0, "orbit_speed": 0.5, "orbit_angle": 1.0},
]}


def reply(name="Earth", ok=True, resources=None, extractors=None, ptype="Terra"):
    return json.dumps({"ok": ok, "planet_name": name, "planet_type": ptype,
                       "resources": {"metal_ore": 4, "gold": 0} if resources is None else resources,
                       "extractors": {"metal_ore": 3, "gold": 9} if extractors is None else extractors})


class Window:
    def __init__(self, **kw):
        self._layout = LAYOUT
        self._last_solar_system_name = "Sol"
        self._scanned_by_system = {}
        self._scanned_moons_by_system = {}
        self._scanned_resources_by_system = {}
        self.chat = []
        self.__dict__.update(kw)

    def chat_log(self, text, colour=None):
        self.chat.append(text)


class Handler(ProtocolMixin):
    """A ProtocolMixin with just the window the scan handler talks to."""

    def __init__(self, window):
        self._solar_window = window


class CaptureTests(unittest.TestCase):
    def capture(self, payload, window=None):
        window = window if window is not None else Window()
        with mock.patch.object(psc, "record_scan") as record:
            psc.capture_planet_scan(Handler(window), payload)
        return window, record

    def test_a_planet_scan_is_recorded_with_its_orbit_and_indexed(self):
        window, record = self.capture(reply("Earth"))
        record.assert_called_once()
        (system, data), kwargs = record.call_args
        self.assertEqual((system, data["planet_name"]), ("Sol", "Earth"))
        self.assertEqual(kwargs, {"orbit_radius": 5.0, "orbit_speed": 0.1, "orbit_angle_seed": 2.0, "parent_planet": None})
        self.assertEqual(window._scanned_by_system, {"Sol": {"Earth": "Terra"}})
        self.assertEqual(window._scanned_moons_by_system, {})
        self.assertEqual(window._scanned_resources_by_system, {"Sol": {"Earth": {"metal_ore": 3}}})   # only what is there

    def test_a_moon_is_recorded_with_its_parent_and_goes_in_the_moon_index_only(self):
        window, record = self.capture(reply("Luna"))
        self.assertEqual(record.call_args.kwargs["parent_planet"], "Earth")
        self.assertEqual(window._scanned_moons_by_system, {"Sol": {"Luna"}})
        self.assertEqual(window._scanned_by_system, {})
        self.assertEqual(window._scanned_resources_by_system, {"Sol": {"Luna": {"metal_ore": 3}}})

    def test_a_rescan_replaces_the_bodys_own_entry_instead_of_adding_to_it(self):
        window, _record = self.capture(reply("Earth", resources={"gold": 1}, extractors={"gold": 7}))
        self.capture(reply("Earth", resources={"gold": 1}, extractors={"gold": 2}), window)
        self.assertEqual(window._scanned_resources_by_system, {"Sol": {"Earth": {"gold": 2}}})

    def test_null_resources_and_extractors_are_an_empty_scan_not_a_crash(self):
        window, record = self.capture(json.dumps({"ok": True, "planet_name": "Earth", "resources": None, "extractors": None}))
        record.assert_called_once()
        self.assertEqual(window._scanned_resources_by_system, {"Sol": {"Earth": {}}})

    def test_an_unknown_body_or_system_is_still_recorded_without_orbit(self):
        window, record = self.capture(reply("Pluto"), Window(_layout=None, _last_solar_system_name=""))
        (system, _data), kwargs = record.call_args
        self.assertEqual(system, "Unknown")
        self.assertEqual(kwargs, {"orbit_radius": None, "orbit_speed": None, "orbit_angle_seed": None, "parent_planet": None})

    def test_what_the_game_would_not_show_is_not_recorded(self):
        for label, payload, window in (("failed scan", reply(ok=False), Window()),
                                       ("not json", "{{", Window()),
                                       ("no solar window", reply(), None)):
            with self.subTest(label):
                handler_window = window
                with mock.patch.object(psc, "record_scan") as record:
                    psc.capture_planet_scan(Handler(handler_window), payload)
                record.assert_not_called()

    def test_a_window_without_the_indexes_yet_still_records_the_scan(self):
        bare = Window()
        for name in ("_scanned_by_system", "_scanned_moons_by_system", "_scanned_resources_by_system"):
            delattr(bare, name)
        _window, record = self.capture(reply("Earth"), bare)
        record.assert_called_once()


class HookedIntoTheGamesHandlerTests(unittest.TestCase):
    def test_the_hook_is_on_the_games_handler(self):
        self.assertIn(("before", psc.capture_planet_scan), modloader.hooks_on(ProtocolMixin, "_on_planet_scan_result"))
        self.assertEqual(modloader.attachment_problems(psc), [])

    def test_the_scan_is_recorded_before_the_game_writes_its_chat_lines_and_survives_a_crash_in_them(self):
        window = Window()
        order = []
        window.chat_log = lambda text, colour=None: (order.append("chat"), (_ for _ in ()).throw(RuntimeError("chat broke")))[0]
        with mock.patch.object(psc, "record_scan", side_effect=lambda *a, **k: order.append("record")) as record:
            with self.assertRaises(RuntimeError):
                Handler(window)._on_planet_scan_result(reply("Earth"))
        self.assertEqual(order, ["record", "chat"])
        self.assertEqual(record.call_count, 1)
        self.assertEqual(window._scanned_by_system, {"Sol": {"Earth": "Terra"}})

    def test_the_games_own_handler_still_shows_the_scan_in_chat(self):
        window = Window()
        with mock.patch.object(psc, "record_scan"):
            Handler(window)._on_planet_scan_result(reply("Earth"))
        self.assertTrue(any("Earth" in line and "Scan Results" in line for line in window.chat), window.chat)
        self.assertTrue(any("Metal Ore: 3 extractor slots" in line for line in window.chat), window.chat)

    def test_a_scan_reply_with_null_resources_no_longer_crashes_the_games_handler(self):
        window = Window()
        payload = json.dumps({"ok": True, "planet_name": "Earth", "planet_type": "Terra", "resources": None, "extractors": None})
        with mock.patch.object(psc, "record_scan"):
            Handler(window)._on_planet_scan_result(payload)
        self.assertIn("  No surface resources detected.", window.chat)

    def test_the_patched_protocol_no_longer_carries_the_blocks(self):
        source = modloader.game_source("protocol.py")
        for gone in ("record_scan(", "_scan_layout", "_scanned_by_system"):
            with self.subTest(gone):
                self.assertNotIn(gone, source)


if __name__ == "__main__":
    unittest.main()
