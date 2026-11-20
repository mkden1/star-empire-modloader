"""The banner's ETA hook (autopilot_eta.install_banner, installed from Client.py) leaves the dev's banner text
alone until an estimate has been published."""
import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import autopilot_eta
import render_mixin
from test_locked_gate_autopilot import SolarSystemWindow  # noqa: F401  (importing Client runs the install lines at the bottom)


class BannerHookTests(unittest.TestCase):
    def test_the_banner_text_is_unchanged_without_an_estimate(self):
        # install_banner wraps render_mixin's _format_autopilot_status from Client.py; until an estimate is
        # published (the worker started from _ap_fly does that) the banner must read exactly as the dev's does.
        autopilot_eta.reset_for_tests()
        self.addCleanup(autopilot_eta.reset_for_tests)
        self.assertTrue(getattr(render_mixin._format_autopilot_status, "_ap_eta_wrapped", False))
        text = render_mixin._format_autopilot_status(["Gamma", "Delta"], "Delta", "")
        self.assertNotIn("ETA", text)
        self.assertEqual(autopilot_eta.eta_text(["Gamma", "Delta"]), "")


if __name__ == "__main__":
    unittest.main()
