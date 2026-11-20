"""The scan archive's STATIONS tab: how it is wired into the page, and (through node) what its script does."""
import json
import os
import re
import shutil
import subprocess
import unittest

from scan_dashboard import build_dashboard_html

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1_800_000_000.0


def profile(**extra):
    base = {"station_id": "2105", "seen_at": NOW - 3600, "station_name": "cryldorwen - 1", "system_name": "Zordoil Frontier",
            "owner_name": "ExamplePlayer", "taken_by": "ExamplePlayer", "docking": "public", "management": "owner", "motd": "hi",
            "has_colony": False, "flags": {}, "resources": {"metal_ore": 100}, "resource_caps": {"metal_ore": 1000},
            "resource_reserves": {}, "credits": 5.0, "workers_used": 1, "hull_used": 10, "hull_space": 100,
            "modules": [{"name": "Industrial Metal Drill", "quantity": 4,
                         "stats": {"Cycle Output": "3 Metal Ore", "Cycle Time": "10 seconds"}}],
            "trade_entries": None, "trade_listed": 0, "construction": None}
    base.update(extra)
    return base


def embedded(page):
    match = re.search(r"const STATIONS = (.*?);\nconst FOCUS_SYSTEM", page, re.S)
    return json.loads(match.group(1).replace("<\\/", "</"))


class PageTests(unittest.TestCase):
    def test_the_tab_is_in_the_page_with_its_data(self):
        page = build_dashboard_html([], station_profiles={"2105": profile()})
        self.assertIn('id="tabStationsBtn">STATIONS</button>', page)
        self.assertIn('id="stationsView"', page)
        self.assertIn('document.getElementById("tabStationsBtn").addEventListener("click", () => showTab("stations"))',
                      page)
        data = embedded(page)
        self.assertEqual([s["id"] for s in data["stations"]], ["2105"])
        self.assertEqual(data["stations"][0]["name"], "cryldorwen - 1")
        self.assertEqual(data["systems"][0]["name"], "Zordoil Frontier")
        self.assertNotIn("__STATIONS", page)

    def test_no_profiles_still_renders_a_valid_empty_tab(self):
        data = embedded(build_dashboard_html([]))
        self.assertEqual(data["stations"], [])

    def test_text_from_the_game_cannot_close_the_script_tag(self):
        page = build_dashboard_html([], station_profiles={"2105": profile(motd="</script><script>alert(1)</script>",
                                                                          station_name="</script>x")})
        self.assertEqual(len(re.findall(r"</script>", page)), 2)
        self.assertEqual(embedded(page)["stations"][0]["name"], "</script>x")

    def test_a_comment_opener_cannot_put_the_parser_in_the_double_escaped_state(self):
        page = build_dashboard_html([], station_profiles={"2105": profile(motd="<!--<script>")})
        self.assertNotIn("<!--<script>", page)
        self.assertEqual(embedded(page)["stations"][0]["motd"], "<!--<script>")
        self.assertEqual(len(re.findall(r"</script>", page)), 2)

    def test_the_fitted_gear_of_a_station_is_embedded_from_its_setup_scan(self):
        scan = [{"source": "station_setup", "key": "2105", "captured_at": NOW - 86400,
                 "data": {"equipped_module_counts": {"station_shield_mk2": 1, "station_energy_mk3": 1},
                          "cargo": [{"item_key": "item:station:station_shield_mk2", "item_type": "station_shield_mk2",
                                     "display_name": "Station Shield Mk II", "quantity": 30}]}}]
        page = build_dashboard_html([], station_profiles={"2105": profile()}, colony_scan_rows=scan)
        gear = embedded(page)["stations"][0]["gear"]
        self.assertEqual(gear["known"], True)
        self.assertEqual(gear["marks"], [{"mark": 2, "count": 1}, {"mark": 3, "count": 1}])   # the 30 spares are not fitted
        self.assertEqual([m["label"] for m in gear["fitted"]], ["Station Energy", "Station Shield"])
        self.assertEqual(embedded(build_dashboard_html([], station_profiles={"2105": profile()}))["stations"][0]["gear"],
                         {"known": False})

    def test_gear_text_from_the_game_cannot_close_the_script_tag(self):
        scan = [{"source": "station_setup", "key": "2105", "captured_at": NOW - 86400,
                 "data": {"equipped_module_counts": {"station_shield_mk2": 1},
                          "cargo": [{"item_key": "item:station:station_shield_mk2", "item_type": "station_shield_mk2",
                                     "display_name": "</script><script>alert(1)</script> Mk II", "quantity": 1}]}}]
        page = build_dashboard_html([], station_profiles={"2105": profile()}, colony_scan_rows=scan)
        self.assertEqual(len(re.findall(r"</script>", page)), 2)
        self.assertEqual(embedded(page)["stations"][0]["gear"]["fitted"][0]["label"], "</script><script>alert(1)</script>")

    def test_the_stations_script_follows_the_planner_in_the_second_block(self):
        page = build_dashboard_html([])
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        self.assertEqual(len(scripts), 2)
        self.assertIn("STATIONS tab of the scan archive", scripts[1])
        self.assertLess(scripts[1].index("STATIONS tab of the scan archive"), len(scripts[1]))

    def test_the_old_tabs_still_switch(self):
        page = build_dashboard_html([])
        for tab in ("planets", "galaxies", "bots", "planner", "stations"):
            self.assertIn(f'tab === "{tab}"', page)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ScriptTests(unittest.TestCase):
    def test_the_script_passes_its_node_tests(self):
        result = subprocess.run(["node", "test_scan_stations.js"], cwd=HERE, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
