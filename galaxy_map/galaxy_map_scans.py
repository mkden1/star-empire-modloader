"""galaxy_map_scans.py - the galaxy map's scan features on the game window (``SolarSystemWindow``).

* The scanned-body indexes the map's SCANNED readouts and the Survey view read (``init_state``, run by modloader after
  the game's own ``_init_galaxy_map``): seeded once from scan_data/, then kept live by each new PLANET_SCAN_RESULT
  (see scan_capture's protocol hook), so the map never re-reads the file per frame.
* The Survey view's Ctrl/Cmd-click multi-select (``survey_ctrl_click``, ``survey_clear_click``), called from the
  mouse handling in Client.py's event loop.
* VIEW SCANS: ``_gm_open_scan_dashboard`` regenerates the scan archive's dashboard and the Claude export together and
  opens the dashboard in the browser (see scan_dashboard.py and galaxy_export.py). The click that calls it is a hook
  in Client.py's event loop. It is the one thing here that needs the ``scan_archive`` mod, which is optional: without
  it the button is not drawn.

``install`` attaches the method and the hook from a one-line call at the end of Client.py.
"""

import pygame

import modloader
from scan_data import (load_scanned_moons_by_system,
                       load_scanned_planets_by_type,
                       load_scanned_resources_by_system)

_archive = None          # (export_data, generate_and_open) once looked up; () when the scan_archive mod is not installed


def scan_archive():
    """The scan archive's ``(export_data, generate_and_open)``, or None when that mod is not installed. VIEW SCANS is the
    only thing in the map that needs it, so it is looked up here, on first use, and the button is left out without it
    (the rest of the map, the Survey view included, works from scan_data.py in core)."""
    global _archive
    if _archive is None:
        try:
            from galaxy_export import export_data
            from scan_dashboard import generate_and_open
            _archive = (export_data, generate_and_open)
        except ImportError:
            _archive = ()
    return _archive or None


class WindowMethods:
    """The VIEW SCANS action on the game window (SolarSystemWindow)."""

    def _gm_scan_archive_available(self) -> bool:
        """Whether VIEW SCANS can work (the scan_archive mod is installed): the header leaves the button out if not."""
        return scan_archive() is not None

    def _gm_open_scan_dashboard(self) -> None:
        """Regenerate the scan-data outputs and open the HTML one.

        Two outputs, always regenerated together so they never drift out
        of sync: the browser-viewable HTML dashboard (opened immediately,
        focused on the currently-selected system readout if one is open)
        and scan_data/export_for_claude.json — the same planet data plus
        every visited system's sun position, gate positions/bearings and
        adjacency, meant to be attached/pasted directly into a chat for
        route-time / resource-cluster questions rather than browsed.

        export_data() must run FIRST: the dashboard's Galaxies tab now reads
        export_for_claude.json's own "explored_systems" data (see
        scan_dashboard.generate_and_open), and that file is exactly what
        export_data() writes -- calling generate_and_open() first meant it
        always rendered from the PREVIOUS click's export, one click stale,
        real bug found this session (a fresh account's newly-explored
        systems didn't show up until the SECOND "VIEW SCANS" click).
        """
        archive = scan_archive()
        if archive is None:
            return
        export_data, generate_and_open = archive
        export_data(self._visited_systems,
                    server_elapsed=self._server_elapsed())
        generate_and_open(focus_system=self._galaxy_map_info_system)


def init_state(self) -> None:
    """The map's scan state, set up right after the game's own ``_init_galaxy_map``."""
    # {system_name: {planet_name: planet_type}}, PLANETS ONLY (moons excluded) -- seeded once from
    # scan_data/planet_scans.csv, then kept live by each new PLANET_SCAN_RESULT so the galaxy map's "SCANNED"
    # readout never re-reads the file per frame.
    self._scanned_by_system = load_scanned_planets_by_type()
    # {system_name: {scanned moon names}} -- moon counterpart, same seed-once-then-kept-live pattern.
    self._scanned_moons_by_system = load_scanned_moons_by_system()
    # {system_name: {body_name: {resource_key: extractor slots}}} -- one entry per scanned body (planet OR moon),
    # same seed-then-kept-live pattern again. Kept per-body rather than pre-summed so a rescan replaces a body's own
    # contribution instead of double-counting it; the galaxy map's system-info popup RESOURCES section sums across
    # bodies itself (see load_scanned_resources_by_system's docstring).
    self._scanned_resources_by_system = load_scanned_resources_by_system()
    # Survey view's Ctrl/Cmd-click multi-select: systems currently picked for the combined-resources comparison
    # overlay (see _gm_draw_survey_selection). Persists across single-node readout clicks (the two are independent)
    # but is cleared on leaving the Survey tab or closing the map, since the indicators driving the comparison
    # disappear with it.
    self._gm_survey_selected = set()
    self._gm_survey_selection_rect = None
    self._gm_survey_selection_clear_rect = None
    self._gm_view_scans_btn_rect = None


def survey_clear_click(self, pos) -> bool:
    """A click on the Survey comparison overlay: CLEAR ALL empties the selection, anywhere else on the panel is just
    absorbed. Checked before the panel's general click guard, same pattern as the favorite toggle above it. True
    when the click was taken (the loop does ``continue``)."""
    if self._gm_survey_selection_clear_rect and self._gm_survey_selection_clear_rect.collidepoint(pos):
        self._gm_survey_selected = set()
        return True
    return bool(self._gm_survey_selection_rect and self._gm_survey_selection_rect.collidepoint(pos))


def survey_ctrl_click(self, pos) -> bool:
    """Ctrl/Cmd-click on the Survey view toggles a system into the multi-select resource comparison instead of opening
    its single-system readout. Left independent of that readout on purpose -- ctrl-clicking to build a comparison and
    clicking to inspect one system don't need to fight each other. True when a system was toggled.

    MOUSEBUTTONDOWN events carry no .mod attribute (that's KEYDOWN-only): pygame.key.get_mods() is the live modifier
    state, same as the hull multi-select's own Ctrl check elsewhere in that handler."""
    if (self._galaxy_map_view == "survey"
            and (pygame.key.get_mods() & (pygame.KMOD_CTRL | pygame.KMOD_META))):
        name = next((n for n, rect in self._galaxy_map_node_rects.items()
                     if rect.collidepoint(pos) and n in self._visited_systems), None)
        if name is not None:
            if name in self._gm_survey_selected:
                self._gm_survey_selected.discard(name)
            else:
                self._gm_survey_selected.add(name)
            self._galaxy_map_dragging = False
            return True
    return False


def install(target) -> "list[str]":
    """Give the game window the VIEW SCANS method and the scan state set-up."""
    return modloader.install_methods(target, WindowMethods, after={"_init_galaxy_map": init_state})


# what test_mods_apply checks after every game update: (game module, game class, the class above), and
# (game module, game class, game method, the function that runs after it)
ATTACHES = (("Client", "SolarSystemWindow", "WindowMethods"),)
HOOKS = (("Client", "SolarSystemWindow", "_init_galaxy_map", "init_state"),)
