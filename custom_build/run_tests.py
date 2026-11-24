"""Run the whole suite - the game's own tests and the mods' tests - and compare the failures with the known list.

    custom_build\\python.exe custom_build\\run_tests.py

Prints pytest's summary line, then how many tests failed, how many of those are in KNOWN_UNRELATED_FAILURES (the dev's
own tests that fail without any mod, on this runtime) and which are NEW. Exit code 0 when nothing new failed and pytest
reached its summary, 1 otherwise. After a game update, a NEW failure is either a mod to re-point (``Check Mods.bat``
names the patch) or a test of the dev's that now fails for a reason to look at: run it with ``STAR_EMPIRE_NO_MODS=1`` to
see whether a mod is involved; if it fails without any mod, add it to KNOWN_UNRELATED_FAILURES with a comment saying why.

The game's files must be in _internal/ (installed with the official launcher) and the runtime in custom_build/
(see fix_runtime.py).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNAL = REPO_ROOT / "_internal"
PYTHON_EXE = REPO_ROOT / "custom_build" / "python.exe"


# Tests that kill the interpreter natively (access violation, no pytest summary at all) on this machine,
# so the whole run is lost -- they are deselected, not failed. Confirmed vanilla's: a pure v0.5.552 copy
# (the dev's files only, none of our hooks) dies in the very same place.
#  * test_ui_own_textures ...test_own_textures_outlive_the_atlas_and_go_with_their_pictures calls
#    gc.collect(); in a full run that full collection finalises native-backed objects earlier tests left
#    (the cffi/PortAudio ones the `-p no:unraisableexception` note below is about) and crashes. Any
#    explicit gc.collect() after the rest of the suite crashes the same way (checked). The test passes
#    alone. First seen reconciling v0.5.552.
CRASHING_TESTS = {
    "_internal/test_ui_own_textures.py::OwnTextureTests::"
    "test_own_textures_outlive_the_atlas_and_go_with_their_pictures",
}


# Folders under _internal/ that are not client code and whose tests cannot run on an installed client.
#  * BossDungeonEditor: the dev's Tkinter editor for his SERVER-side boss dungeons (new in v0.5.57). Its
#    test_item_catalog reads the dev's Server/ tree (Items/*.xml, equipment.py), which client installs do
#    not ship, and test_multi_drop_tables imports tkinter, whose Python package this runtime does not have
#    (only _tkinter.pyd is bundled). Nothing in the client imports it. Left in, that one import error
#    aborted the whole pytest run at collection (0 tests ran, "no summary"), which looked like a clean run.
NOT_CLIENT_CODE = ["BossDungeonEditor"]


def run_tests():
    smoke_ignore = ["numpy", "pygame", "moderngl", "glcontext", "orjson"] + NOT_CLIENT_CODE
    # `--continue-on-collection-errors`: a test file that cannot even be imported must show up as one
    # ERROR line (and so as a NEW failure) while every other test still runs, not stop the run.
    args = [str(PYTHON_EXE), "-c",
            "import os; os.add_dll_directory(r'%s')\n"
            "import pytest, sys\n"
            "sys.exit(pytest.main(['_internal', 'mods', '-q', '--tb=short', '-rfE', "
            "'--continue-on-collection-errors', "
            "'-p', 'no:unraisableexception'] + %r))"
            % (INTERNAL, [f"--ignore=_internal/{d}" for d in smoke_ignore]
               + [f"--deselect={t}" for t in sorted(CRASHING_TESTS)])]
    # `-p no:unraisableexception`: that plugin runs gc.collect() five times when pytest shuts
    # down, and since v0.5.431 that kills the interpreter natively (0xC0000005 / 0xC000001D,
    # Windows "python.exe stopped working") -- reproducible with the vanilla test
    # test_npc_trade_undo.py ALONE, which builds a real SolarSystemWindow: finalising its
    # cffi/PortAudio objects crashes. With the plugin off the run exits cleanly and every
    # result is reported. PYTHONUNBUFFERED stops a crash from eating the buffered summary.
    # Decode as UTF-8 and never fail on a stray byte: with the Windows default (cp1252) one
    # non-ASCII character in the pytest output (a game string in a failure message, say) kills
    # the reader thread, subprocess returns stdout=None, and the script died with a TypeError instead of
    # reporting the run.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONUNBUFFERED": "1"}
    result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


# Failures already understood to be pre-existing gaps in the dev's own test
# fixtures, unrelated to our mods -- confirmed (each time this has come up
# since v0.4.92) by diffing the failing test file against pure vanilla and
# finding it byte-identical, and confirming the real code path involved
# works correctly at runtime. Seeing ONLY these in a run's failures is
# expected and not a regression; anything else is new and needs a look.
KNOWN_UNRELATED_FAILURES = {
    "_internal/test_perf_profiler.py::ServerProfilerResultsRenderTests::"
    "test_cpu_wall_columns_render_from_profile_payload",
    # Vanilla-only test-fixture gap, confirmed harmless: a bare
    # LoginScreen.__new__() harness never sets _login_err, which a v0.5.17+
    # code path reads on disconnect. login.py's real __init__ sets it fine;
    # neither login.py nor this test has ever been touched by our mods.
    # First seen reconciling v0.5.17.
    "_internal/test_device_identity.py::LoginDeviceTests::"
    "test_disconnection_cancels_pending_auth",
    # Vanilla-only test staleness, confirmed harmless: v0.5.21 extracted the
    # whole Coalition System Wars panel (including the war-time controls
    # this test checks for) out of render_mixin.py into a new
    # coalition_csw_panel.py -- the string literals this test looks for in
    # render_mixin.py genuinely moved there (and are present and correctly
    # wired there), vanilla's own test just wasn't updated to check the new
    # file. First seen reconciling v0.5.21.
    "_internal/test_coalition_system_controls.py::"
    "CoalitionSystemControlSourceTests::"
    "test_owner_war_time_control_is_permission_and_lockout_gated",
    # Vanilla tests that read the dev's SERVER source tree (Server/rarity.py,
    # Server/solar_system.py). Client installs do not ship Server/, so these
    # error out with FileNotFoundError here whatever the client code does.
    # First seen reconciling v0.5.431 (both files already existed in v0.5.42).
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::"
    "test_a_dropped_item_is_labelled_in_its_rarity_colour",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::"
    "test_a_targeted_item_glows_in_its_rarity_colour",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::"
    "test_an_unknown_tier_falls_back_to_common",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::"
    "test_the_client_has_the_colour_of_every_server_tier",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_app_loop_selects_csw_on_login_and_keeps_ticking_after_handoff",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_csw_entry_and_exit_pause_five_seconds_during_song_or_gap",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_csw_track_repeats_after_thirty_seconds_in_any_event_phase",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_normal_system_with_csw_entry_gate_keeps_normal_music",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_recreated_csw_system_recovers_identity_before_selecting_music",
    "_internal/test_music_playlist.py::MusicPlaylistTests::"
    "test_server_layout_preserves_csw_identity_and_topology",
    # Vanilla-only test staleness, confirmed harmless: v0.5.431's
    # _engage_nav_autopilot sends REQUEST_PLAYER_STATION_DOCK_ACCESS through
    # self._send_json for player-station targets, but this test's `Window`
    # stub only has _send_fn. The function and the test are byte-identical to
    # vanilla; Client's real class defines _send_json. First seen v0.5.431.
    "_internal/test_market.py::MarketClientTests::"
    "test_arrival_runs_real_tracked_navigation_and_follows_station_motion",
    # The dev's own test of his new GPU particle path (gpu_particles.py, new in v0.5.501) against its
    # CPU twin: weapon trails drawn on the card differ from the CPU picture by 1.74 against a 0.36
    # tolerance. Not ours: gpu_particles.py / thruster_trails.py / exhaust_types.py are byte-identical
    # to vanilla and swapping in the pure-vanilla render_mixin.py gives the same failure with the same
    # numbers. (The thruster-trail picture tests in the same file pass.) First seen reconciling
    # v0.5.501; whether the card path really draws weapon trails differently in the game is the dev's.
    "_internal/test_gpu_particles.py::PictureTests::test_weapon_trails_draw_the_same",
    # Wall-clock test (time.perf_counter around 90 frames x 100 ships): it passed alone three times in a
    # row and in two of three full runs, and failed in the third when the machine was busy. Timing noise
    # in the dev's test; seen reconciling v0.5.552.
    "_internal/test_exhaust_types.py::ExhaustTypeTests::"
    "test_new_types_cost_no_more_than_the_pixel_trail",
    # The dev's test compiles App.start in an isolated namespace (pygame, logger, Path, ... but no `os`),
    # and v0.5.58 added `os.environ.setdefault("SDL_MOUSE_FOCUS_CLICKTHROUGH", "1")` as the first line of
    # App.start, so all three tests die with NameError at that line. Not ours: copying the pure-vanilla
    # Client.py and test_music_startup.py (commit "Vanilla update ... v0.5.58") to an empty folder gives
    # the same three NameErrors. First seen reconciling v0.5.58.
    "_internal/test_music_startup.py::MusicStartupTests::"
    "test_available_audio_plays_login_music_and_applies_saved_volume",
    "_internal/test_music_startup.py::MusicStartupTests::"
    "test_mixer_initialized_by_pygame_is_reused",
    "_internal/test_music_startup.py::MusicStartupTests::"
    "test_wasapi_failure_still_draws_login_and_starts_network",
    # v0.5.63: the slot list became server-driven and four of the dev's test modules now import
    # Server/equipment_categories.py through his new test_support_gear_slots.py. Client installs do not
    # ship Server/ (same family as the rarity / solar_system tests above), so pytest cannot COLLECT these
    # four files and none of their tests run. Not ours: with a stand-in equipment_categories (labels, order
    # and always-shown set rebuilt from the v0.5.62 client) all 40 tests in them and test_inventory_sections
    # pass on our tree. When one of these appears, re-run them that way (a launcher that puts the stand-in
    # on sys.path: the embedded runtime ignores PYTHONPATH). First seen reconciling v0.5.63.
    "_internal/test_support_gear_slots.py",
    "_internal/test_item_search_words.py",
    "_internal/test_selected_unequip.py",
    "_internal/test_ship2_inventory_search.py",
    # Fails by one pixel value (247 vs 248, 1 of 120000) comparing the batched hull draw with the plain
    # one on this machine's GL. Not ours: the pure-vanilla v0.5.63 tree fails the same four subtests
    # (units=3/32 x depth_pass) in an empty folder. It showed in earlier reconciles too, unnoticed because
    # an ad-hoc summary script ignored SUBFAILED lines - use parse_failed_tests, which counts them.
    "_internal/test_hull_batch.py::HullBatchTests::test_pixels_and_depth_match_the_plain_batch",
    # Wall-clock flake: the dev's market panel cache key includes the snapshot age in whole seconds
    # (market.py, time.monotonic), so a second boundary between the test's two draws rebuilds the surface
    # and assertIs fails. Passes alone, on our tree and on pure vanilla; seen once in a full run at v0.5.63.
    "_internal/test_market.py::MarketClientTests::test_draw_scales_caches_and_tab_clicks_work",
    # v0.5.70: the dev's _draw_station_overlay now calls self._sync_npc_market_watch() every frame, which reads
    # self._npc_market_watch_sent - set only in SolarSystemWindow.__init__ - while these two UNCHANGED dev tests
    # draw a bare SolarSystemWindow.__new__ window. Not ours: both files fail the same 5 tests on the pure
    # v0.5.70 tree (AttributeError in vanilla Client.py), in an empty folder. (Our own tests that copy his
    # bare-window setup - construction_eta, construction_tooltip, station_auto_name - set the attribute now.)
    "_internal/test_construction_detail_render.py::ConstructionDetailRenderTests::"
    "test_an_older_server_without_estimates_still_draws",
    "_internal/test_construction_detail_render.py::ConstructionDetailRenderTests::"
    "test_no_selection_or_an_open_blueprint_popup_hides_it",
    "_internal/test_construction_detail_render.py::ConstructionDetailRenderTests::"
    "test_the_selected_project_shows_its_details_beside_the_bay",
    "_internal/test_station_control_render.py::StationControlRenderTests::"
    "test_healing_updates_bars_without_uploading_whole_control_panel",
    "_internal/test_station_control_render.py::StationControlRenderTests::"
    "test_software_surface_still_renders_control_and_scrolls",
    # v0.5.761: the dev's new test_jump_warp reads a whole array uniform back with `prog["u_warp"].read()` and
    # indexes row 1. Our runtime's moderngl 5.6.4 reads back only the FIRST element of an array uniform (16 bytes
    # of the 48 a 3 x vec4 array holds - probed with a standalone context), so `rows[1]` raises IndexError. Not
    # ours: the pure v0.5.761 tree fails the same way in an empty folder. The updater's own run (16 failures)
    # happens BEFORE fix_runtime.py puts our moderngl back, and a full run afterwards counted 21 - presumably
    # the launcher's newer moderngl passing this test in between (not checked test by test). Reading is the
    # test's only use; the client writes the array with `.value = [...]`, which 5.6.4 accepts in full.
    "_internal/test_jump_warp.py::WarpPixelTests::test_holes_and_folds_ride_together_and_are_capped",
    # v0.5.77: the dev shipped tests for exhaust RIBBONS (test_gpu_particles +325 lines, test_exhaust_types +29)
    # without the code they test: gpu_particles has no `_RIBBON_FRAG`, exhaust_types no `_heat`, ExhaustTypes no
    # `_ribbons` - AttributeError in each. Not ours and not a stale launcher copy: the pure v0.5.77 tree fails the
    # same 10 (11 with test_weapon_trails_draw_the_same above), and compare_frozen_loose.py finds every loose
    # game module (177) identical to the code frozen in the dev's own Client.exe, so the build itself has no ribbon
    # code yet. Expect these to pass again in the release that ships it (then remove them from this list).
    "_internal/test_exhaust_types.py::ExhaustTypeTests::test_particles_cool_from_white_hot_to_ember",
    "_internal/test_exhaust_types.py::ExhaustTypeTests::test_the_cpu_path_lays_no_plume",
    "_internal/test_gpu_particles.py::PictureTests::test_ribbons_close_the_gaps_between_puffs",
    "_internal/test_gpu_particles.py::PictureTests::test_without_the_ribbon_shader_the_types_look_as_before",
    "_internal/test_gpu_particles.py::RecordTests::test_a_growing_ribbon_ring_keeps_every_link",
    "_internal/test_gpu_particles.py::RecordTests::test_a_retired_emitter_hides_its_plume_and_frees_its_lanes",
    "_internal/test_gpu_particles.py::RecordTests::test_each_nozzle_lays_a_linked_plume",
    "_internal/test_gpu_particles.py::RecordTests::test_plumes_start_where_the_puffs_are_born_not_on_the_nozzle",
    "_internal/test_gpu_particles.py::RecordTests::test_rebasing_keeps_every_ribbon_point",
    "_internal/test_gpu_particles.py::RecordTests::test_the_puff_family_sheds_no_bubbles_and_ember_and_ring_keep_theirs",
    # v0.5.781, all three families below fail identically on the pure v0.5.781 tree in an empty folder, and
    # compare_frozen_loose.py finds all 180 loose game modules identical to the code frozen in the dev's Client.exe
    # (so none is a stale launcher copy):
    # (1) The dev replaced the Shift hotbar set with hotbar TABS (hotbar.py `_hotbar_shown_set`, test_hotbar_tabs.py)
    #     and left test_hotbar_shift_set.py behind: it still calls `window._hotbar_update_shown_set()`, which no
    #     longer exists. A stale dev test for a removed feature.
    "_internal/test_hotbar_shift_set.py::ShiftSetTests::test_shift_number_keys_fire_the_second_set",
    "_internal/test_hotbar_shift_set.py::ShiftSetTests::test_the_bar_shows_the_shift_set_only_while_shift_is_held",
    # (2) App.start now begins with `_premium_account.tick(self)` (the premium-account icon), and test_music_startup
    #     compiles App.start in an isolated namespace that has no `_premium_account` - NameError (same family as the
    #     `os` NameError above, which this file's other three tests still show).
    "_internal/test_music_startup.py::MusicStartupTests::test_leaving_the_app_closes_the_audio_device",
    "_internal/test_music_startup.py::MusicStartupTests::test_no_audio_output_still_draws_login_and_starts_network",
    "_internal/test_music_startup.py::MusicStartupTests::test_pygame_never_keeps_its_own_audio_device",
    # (3) The new test_ship_inventory_rows.py imports test_support_gear_slots, which imports the Server-only
    #     equipment_categories (client installs ship no Server/; same family as the four files above) - a
    #     collection error, so none of its tests run. On a scratch copy of our merged tree with a stand-in
    #     Server/equipment_categories.py built from the slot table a live server really sent (scan_data/
    #     combat_scan.jsonl keeps it as a cargo_summary row's `gear_slots`), all five blocked files plus
    #     test_inventory_sections and test_hangar_inventory_lifecycle passed (67 tests) at v0.5.781.
    "_internal/test_ship_inventory_rows.py",
    # v0.5.82, all three fail identically on the pure v0.5.82 tree (git show 0534f1a:...) - dev-side:
    # (1) The dev's test_item_box_rarity_colors reads Server/rarity.py (not shipped to clients) in setUpClass; the
    #     class's four older tests were already known, v0.5.82 added five more to it (loot_box_half_world sizes,
    #     the Item Names / Credit Amounts options).
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::test_credit_amounts_are_an_option_that_starts_off",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::test_every_crate_draws_one_and_a_half_times_a_credit_coin",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::test_item_names_are_an_option_that_starts_on",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::test_the_scoop_prompt_shows_whatever_the_options_say",
    "_internal/test_item_box_rarity_colors.py::ItemBoxRarityColourTests::test_the_size_helper_is_what_the_click_and_reticle_read",
    # (2) test_space_state_codec's capability-token check reads Server/space_state_codec.py (not shipped).
    "_internal/test_space_state_codec.py::ResidualFrameTests::test_the_capability_token_matches_the_servers",
    # (3) The control connection now also announces RESIDUAL_STATE_CAP ("stres1", Client.py:1017 in vanilla), and
    #     test_warp_arrival still expects the three older tokens - a stale dev test.
    "_internal/test_warp_arrival.py::ArrivalCapsTests::test_the_game_control_connection_announces_arrival_states_and_inventory_pages",
    # v0.5.86 (2026-11-20): the new csw_anomalies_client.py reads `self._s` (csw_anomalies_client.py:713) from the ship
    # pass, and the dev's stand-ins for the ship scene (ShipRenderScene in these tests) have no `_s`. All 14 fail with
    # every mod switched off (STAR_EMPIRE_NO_MODS=1), so they are the dev's own.
    "_internal/test_change_name_dialog_z_order.py::ChangeNameDialogZOrderTests::test_dialog_is_drawn_after_stats_and_chat",
    "_internal/test_cloak_fx.py::CloakShipPassTests::test_a_batch_without_the_cloak_keeps_the_tint_and_dots",
    "_internal/test_cloak_fx.py::CloakShipPassTests::test_a_shot_strains_npc_and_player_cloaks_alike",
    "_internal/test_cloak_fx.py::CloakShipPassTests::test_the_hull_shader_gets_the_cloak_and_no_dots_are_made",
    "_internal/test_hull_wrecks.py::ShipPassLookTests::test_a_batch_without_pieces_keeps_no_looks",
    "_internal/test_hull_wrecks.py::ShipPassLookTests::test_plainly_drawn_hulls_leave_their_look_and_hidden_ones_do_not",
    "_internal/test_picture_loading.py::ShipDrawTests::test_a_ship_is_left_out_while_its_picture_loads",
    "_internal/test_picture_loading.py::ShipDrawTests::test_a_ship_is_left_out_while_its_texture_waits_its_turn",
    "_internal/test_picture_loading.py::ShipDrawTests::test_a_ship_with_no_picture_still_gets_its_triangle",
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_a_ship_image_keys_the_hull_texture_by_its_file",
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_actual_ship_pass_aligns_hulls_nozzles_and_shield_hits",
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_fallback_hulls_and_forward_arrow_follow_fractional_translation",
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_fighters_are_drawn_but_never_offered_as_shield_hit_targets",
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_stasis_aura_is_two_circles_on_the_fractional_anchor",
    # v0.5.88 (2026-11-23): the dev's bare-window harness (make_owner) no longer ends up with a "target" entry in
    # `_panel_full_rects` after the retained-layer draw (KeyError: 'target', test_profiler_review_fixes.py:175), a stale
    # test of his: it fails the same with every mod switched off (STAR_EMPIRE_NO_MODS=1).
    "_internal/test_profiler_review_fixes.py::PointerZoneTests::test_the_pointer_repaints_a_panel_only_when_it_changes_what_is_lit",
    # v0.5.89.1 (2026-11-24), all six fail the same with every mod switched off (STAR_EMPIRE_NO_MODS=1), so they are the
    # dev's own stale tests:
    # (1) on_coalition_state now calls `_coalition_vault.rates(data)`, and test_coalition_presence compiles that method in
    #     an isolated namespace that has no `_coalition_vault` - NameError (the real client imports it at Client.py:95).
    "_internal/test_coalition_presence.py::CoalitionChatTests::test_a_login_line_does_not_blink_the_tab_but_a_message_does",
    "_internal/test_coalition_presence.py::CoalitionChatTests::test_logins_and_logouts_are_posted_to_coalition_chat",
    "_internal/test_coalition_presence.py::CoalitionChatTests::test_the_login_message_is_posted_once_and_its_changes_after",
    "_internal/test_coalition_presence.py::CoalitionChatTests::test_the_option_turned_off_posts_nothing",
    "_internal/test_coalition_presence.py::CoalitionChatTests::test_the_same_roster_sent_again_posts_nothing",
    # (2) the ship pass now reads `self._deployable_states`, which the dev's ShipRenderScene stand-in lacks (same family as
    #     the five test_subpixel_rendering tests listed above); this sixth one only reached it now.
    "_internal/test_subpixel_rendering.py::SubpixelRenderingTests::test_station_tags_stay_on_whole_pixels",
}


def parse_failed_tests(output):
    """Node ids of every failed/errored test in a pytest `-rfE` summary. Subtest failures
    print as `SUBFAILED(phase='X') <node id>`; they count against that test's node id."""
    failed = set()
    for line in output.splitlines():
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                node_id = line[len(prefix):].split(" - ", 1)[0].strip()
                failed.add(node_id)
        if line.startswith("SUBFAILED(") and ") " in line:
            node_id = line.split(") ", 1)[1].split(" - ", 1)[0].strip()
            failed.add(node_id)
    return failed


def main() -> int:
    if not PYTHON_EXE.exists():
        print(f"{PYTHON_EXE} not found: the runtime in custom_build/ is needed (see fix_runtime.py).")
        return 2
    code, output = run_tests()
    failed = parse_failed_tests(output)
    new = sorted(f for f in failed if f not in KNOWN_UNRELATED_FAILURES)
    summary = [l for l in output.splitlines() if " passed" in l and " in " in l and l.rstrip().endswith(")")]
    print(summary[-1] if summary else "NO SUMMARY LINE: the run died natively (see the crash notes above CRASHING_TESTS)")
    print(f"failed: {len(failed)} | known: {len(failed) - len(new)} | NEW: {len(new)}")
    for node_id in new:
        print("  NEW FAILURE:", node_id)
    return 1 if new or not summary else 0


if __name__ == "__main__":
    raise SystemExit(main())
