"""squad_hud.py: the SQUAD and FIGHTERS / DRONES roster panels, called from the HUD pass of the game window's run loop."""
import unittest
from types import SimpleNamespace
from unittest import mock

import modloader
import squad_hud

PNL_DIM = (90, 100, 120)


def window(**kw):
    win = SimpleNamespace(
        _squad_state={"members": ["me", "ally", "Bravo"]}, _username="me", _local_player_id=7,
        _HUD_SH_CAP=(1, 1, 1), _HUD_SH_FILL=(2, 2, 2), _HUD_EN_CAP=(3, 3, 3), _HUD_EN_FILL=(4, 4, 4),
        _s=lambda px: px, _profiler=mock.Mock(), _draw_panel=mock.Mock())
    for key, value in kw.items():
        setattr(win, key, value)
    return win


def draw(win, entities):
    font = mock.Mock()
    font.get_height.return_value = 12
    squad_hud.draw_rosters(win, "frame", entities, "hdr font", font, 17, 24, PNL_DIM)
    return {call.args[1]: call for call in win._draw_panel.call_args_list}


class RosterTests(unittest.TestCase):
    def test_squad_members_in_the_system_get_two_bar_rows_each_and_you_are_left_out(self):
        win = window()
        ents = [{"username": "ally", "shields": 50, "max_shields": 100, "energy": 20, "max_energy": 40},
                {"username": "me", "shields": 1, "max_shields": 1, "energy": 1, "max_energy": 1},
                {"username": "bravo-not-in-squad", "shields": 9, "max_shields": 9}]
        panels = draw(win, ents)
        call = panels["squad_hud"]
        self.assertEqual(call.args[2:3], ("SQUAD",))
        lines = call.args[3]
        self.assertEqual(lines, [("ally", (1, 1, 1), True, 50.0, 100.0, (2, 2, 2)),
                                 ("", (3, 3, 3), True, 20.0, 40.0, (4, 4, 4))])
        self.assertEqual((call.kwargs["row_h"], call.kwargs["hdr_h"], call.kwargs["bar_h"]), (24, 17, 16))
        self.assertTrue(call.kwargs["hide_bar_values"] and call.kwargs["instrument_style"])
        self.assertEqual(call.kwargs["hdr_font"], "hdr font")

    def test_the_messages_when_there_is_no_squad_or_nobody_of_it_is_here(self):
        alone = draw(window(_squad_state={"members": []}), [])
        self.assertEqual(alone["squad_hud"].args[3], [(" Not in a squad", PNL_DIM, False, 0, 0, None)])
        absent = draw(window(), [{"username": "stranger"}])
        self.assertEqual(absent["squad_hud"].args[3], [(" No squad members in this system", PNL_DIM, False, 0, 0, None)])

    def test_members_are_sorted_by_name_ignoring_case(self):
        ents = [{"username": "zed"}, {"username": "Alpha"}]
        panels = draw(window(_squad_state={"members": ["zed", "Alpha"]}), ents)
        names = [row[0] for row in panels["squad_hud"].args[3] if row[0]]
        self.assertEqual(names, ["Alpha", "zed"])

    def test_only_your_own_drones_and_fighters_are_listed_and_duplicates_are_numbered(self):
        ents = [{"is_drone": True, "drone_owner_id": 7, "drone_type": "repair_drone", "shields": 5, "max_shields": 10},
                {"is_drone": True, "drone_owner_id": 7, "drone_type": "repair_drone", "shields": 6, "max_shields": 10},
                {"is_fighter": True, "fighter_owner_id": 7, "display_name": "Wasp"},
                {"is_fighter": True, "fighter_owner_id": 99, "display_name": "Enemy Wasp"}]
        panels = draw(window(), ents)
        call = panels["fighter_hud"]
        self.assertEqual(call.args[2], "FIGHTERS / DRONES")
        names = [row[0] for row in call.args[3] if row[0]]
        self.assertEqual(names, ["Repair Drone #1", "Repair Drone #2", "Wasp"])

    def test_nothing_deployed_says_so(self):
        panels = draw(window(), [{"username": "someone"}])
        self.assertEqual(panels["fighter_hud"].args[3], [(" No deployed drones or fighters", PNL_DIM, False, 0, 0, None)])

    def test_both_panels_are_drawn_each_time_and_the_profiler_marks_each_part(self):
        win = window()
        draw(win, [])
        self.assertEqual([c.args[0] for c in win._profiler.mark.call_args_list], ["squad_hud", "fighter_hud"])
        self.assertEqual([c.args[1] for c in win._draw_panel.call_args_list], ["squad_hud", "fighter_hud"])


class HookTests(unittest.TestCase):
    def test_the_patched_client_calls_it_from_the_hud_pass_and_does_not_inline_it(self):
        source = modloader.game_source("Client.py")
        self.assertIn("squad_hud.draw_rosters(self, _frame, entities, _inv_fnt_hdr, _inv_fnt_body,", source)
        self.assertIn("import squad_hud", source)
        self.assertNotIn('"FIGHTERS / DRONES"', source)

    def test_every_local_the_loop_passes_is_one_the_function_takes(self):
        import inspect
        params = list(inspect.signature(squad_hud.draw_rosters).parameters)
        self.assertEqual(params, ["self", "_frame", "entities", "_inv_fnt_hdr", "_inv_fnt_body", "_INV_HDR_H",
                                  "_INV_ROW_H", "_PNL_DIM"])
        self.assertEqual(modloader.missing_globals(squad_hud), set())


if __name__ == "__main__":
    unittest.main()
