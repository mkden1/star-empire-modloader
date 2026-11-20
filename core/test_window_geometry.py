"""window_geometry.py: the helpers of the movable, resizable HUD windows (the galaxy map's and Ship Layouts'). They were
written for, and are still pinned by, the galaxy map window's tests of the same numbers."""
import unittest

import pygame

import window_geometry as wg


class GeometryTests(unittest.TestCase):
    R = pygame.Rect(100, 100, 400, 300)

    def test_clamp_grows_to_the_minimum_and_stays_on_screen(self):
        self.assertEqual(wg.clamp_rect(pygame.Rect(1200, 700, 100, 50), 1280, 720, 580, 300),
                         pygame.Rect(700, 420, 580, 300))

    def test_clamp_shrinks_to_the_screen(self):
        self.assertEqual(wg.clamp_rect(pygame.Rect(-50, -50, 3000, 2000), 1280, 720, 580, 300),
                         pygame.Rect(0, 0, 1280, 720))

    def test_clamp_when_the_screen_is_smaller_than_the_minimum(self):
        self.assertEqual(wg.clamp_rect(pygame.Rect(10, 10, 100, 100), 400, 200, 580, 300),
                         pygame.Rect(0, 0, 400, 200))

    def test_clamp_leaves_a_good_rectangle_alone(self):
        self.assertEqual(wg.clamp_rect(self.R, 1280, 720, 200, 150), self.R)

    def test_zone_in_the_middle_is_none(self):
        self.assertEqual(wg.resize_zone(self.R, (300, 250), 6, 16), 0)

    def test_zone_edges(self):
        self.assertEqual(wg.resize_zone(self.R, (100, 250), 6, 16), wg.EDGE_L)
        self.assertEqual(wg.resize_zone(self.R, (499, 250), 6, 16), wg.EDGE_R)
        self.assertEqual(wg.resize_zone(self.R, (300, 100), 6, 16), wg.EDGE_T)
        self.assertEqual(wg.resize_zone(self.R, (300, 399), 6, 16), wg.EDGE_B)

    def test_zone_width_is_the_grab_margin(self):
        self.assertEqual(wg.resize_zone(self.R, (105, 250), 6, 16), wg.EDGE_L)
        self.assertEqual(wg.resize_zone(self.R, (106, 250), 6, 16), 0)
        self.assertEqual(wg.resize_zone(self.R, (494, 250), 6, 16), wg.EDGE_R)
        self.assertEqual(wg.resize_zone(self.R, (493, 250), 6, 16), 0)

    def test_zone_width_is_the_grab_margin_on_the_top_and_bottom_too(self):
        self.assertEqual(wg.resize_zone(self.R, (300, 105), 6, 16), wg.EDGE_T)
        self.assertEqual(wg.resize_zone(self.R, (300, 106), 6, 16), 0)
        self.assertEqual(wg.resize_zone(self.R, (300, 394), 6, 16), wg.EDGE_B)
        self.assertEqual(wg.resize_zone(self.R, (300, 393), 6, 16), 0)

    def test_zone_corners_take_both_edges(self):
        self.assertEqual(wg.resize_zone(self.R, (100, 100), 6, 16), wg.EDGE_L | wg.EDGE_T)
        self.assertEqual(wg.resize_zone(self.R, (499, 399), 6, 16), wg.EDGE_R | wg.EDGE_B)
        self.assertEqual(wg.resize_zone(self.R, (499, 100), 6, 16), wg.EDGE_R | wg.EDGE_T)
        self.assertEqual(wg.resize_zone(self.R, (100, 399), 6, 16), wg.EDGE_L | wg.EDGE_B)

    def test_zone_corner_reaches_further_than_an_edge(self):
        self.assertEqual(wg.resize_zone(self.R, (110, 114), 6, 16), wg.EDGE_L | wg.EDGE_T)
        self.assertEqual(wg.resize_zone(self.R, (120, 103), 6, 16), wg.EDGE_T)       # 20 px along: just the edge

    def test_zone_outside_is_none(self):
        self.assertEqual(wg.resize_zone(self.R, (50, 50), 6, 16), 0)
        self.assertEqual(wg.resize_zone(self.R, (500, 250), 6, 16), 0)                 # right edge is exclusive

    def resized(self, edges, dx, dy, bounds=(1280, 720), minimum=(200, 150)):
        return wg.resized_rect(self.R, edges, dx, dy, bounds[0], bounds[1], minimum[0], minimum[1])

    def test_resize_right_and_bottom(self):
        self.assertEqual(self.resized(wg.EDGE_R, 50, 0), pygame.Rect(100, 100, 450, 300))
        self.assertEqual(self.resized(wg.EDGE_B, 0, 40), pygame.Rect(100, 100, 400, 340))

    def test_resize_stops_at_the_minimum_size(self):
        self.assertEqual(self.resized(wg.EDGE_R, -300, 0), pygame.Rect(100, 100, 200, 300))
        self.assertEqual(self.resized(wg.EDGE_B, 0, -300), pygame.Rect(100, 100, 400, 150))

    def test_resize_left_and_top_move_the_origin(self):
        self.assertEqual(self.resized(wg.EDGE_L, 50, 0), pygame.Rect(150, 100, 350, 300))
        self.assertEqual(self.resized(wg.EDGE_T, 0, -60), pygame.Rect(100, 40, 400, 360))

    def test_resize_left_and_top_stop_at_the_minimum_size_without_sliding(self):
        self.assertEqual(self.resized(wg.EDGE_L, 400, 0), pygame.Rect(300, 100, 200, 300))
        self.assertEqual(self.resized(wg.EDGE_T, 0, 400), pygame.Rect(100, 250, 400, 150))

    def test_resize_never_leaves_the_screen(self):
        self.assertEqual(self.resized(wg.EDGE_R, 5000, 0).right, 1280)
        self.assertEqual(self.resized(wg.EDGE_B, 0, 5000).bottom, 720)
        self.assertEqual(self.resized(wg.EDGE_L, -5000, 0).left, 0)
        self.assertEqual(self.resized(wg.EDGE_T, 0, -5000).top, 0)

    def test_resize_corner_moves_both(self):
        self.assertEqual(self.resized(wg.EDGE_L | wg.EDGE_T, 20, 30), pygame.Rect(120, 130, 380, 270))
        self.assertEqual(self.resized(wg.EDGE_R | wg.EDGE_B, 20, 30), pygame.Rect(100, 100, 420, 330))

    def test_resize_without_movement_is_the_same_rectangle(self):
        for edges in (1, 2, 4, 8, 5, 10, 6, 9):
            self.assertEqual(self.resized(edges, 0, 0), self.R)

    def test_move_is_clamped_on_all_four_sides(self):
        self.assertEqual(wg.moved_rect(self.R, 30, -20, 1280, 720), pygame.Rect(130, 80, 400, 300))
        self.assertEqual(wg.moved_rect(self.R, 5000, 5000, 1280, 720), pygame.Rect(880, 420, 400, 300))
        self.assertEqual(wg.moved_rect(self.R, -500, -500, 1280, 720), pygame.Rect(0, 0, 400, 300))


class PlateTests(unittest.TestCase):
    def test_the_chamfer_is_the_one_the_baked_plate_has(self):
        from render_cache import build_hud_panel_plate
        for h in (20, 40, 55, 56, 60, 100, 300, 324, 486, 664, 1000, 1900):
            for cap in (8, 28, 42, 56):
                self.assertEqual(wg.plate_chamfer(h, cap),
                                 build_hud_panel_plate(580, h, 46, chamfer_max=cap)["chamfer"], (h, cap))

    def test_the_primitive_plate_keeps_to_its_rectangle_and_restores_the_clip(self):
        target = pygame.Surface((400, 300), pygame.SRCALPHA)
        target.set_clip(pygame.Rect(0, 0, 400, 300))
        wg.draw_plate(target, 50, 40, 200, 120, 46, 17)
        self.assertEqual(target.get_clip(), pygame.Rect(0, 0, 400, 300))
        outside = [(x, y) for x in range(400) for y in range(300)
                   if not pygame.Rect(50, 40, 200, 120).collidepoint((x, y)) and target.get_at((x, y))[3]]
        self.assertEqual(outside, [])
        narrow = pygame.Surface((400, 300), pygame.SRCALPHA)
        narrow.set_clip(pygame.Rect(100, 0, 50, 300))                      # an outer clip still applies
        wg.draw_plate(narrow, 50, 40, 200, 120, 46, 17)
        self.assertEqual(narrow.get_clip(), pygame.Rect(100, 0, 50, 300))
        painted = [x for x in range(400) for y in range(300) if narrow.get_at((x, y))[3]]
        self.assertTrue(painted)
        self.assertTrue(all(100 <= x < 150 for x in painted))


    def test_the_primitive_plate_is_the_baked_plate_pixel_for_pixel(self):
        from render_cache import build_hud_panel_plate
        for w, h, header, cap in ((580, 324, 46, 28), (900, 500, 46, 28), (870, 486, 69, 42), (200, 100, 46, 28)):
            baked = build_hud_panel_plate(w, h, header, chamfer_max=cap)
            target = pygame.Surface((w + 40, h + 30), pygame.SRCALPHA)
            wg.draw_plate(target, 17, 9, w, h, header, baked["chamfer"])
            expected = pygame.Surface((w + 40, h + 30), pygame.SRCALPHA)
            expected.blit(baked["plate"], (17, 9))
            differing = [(x, y) for x in range(w + 40) for y in range(h + 30)
                         if tuple(target.get_at((x, y))) != tuple(expected.get_at((x, y)))]
            self.assertEqual(differing, [], (w, h, header, cap))


if __name__ == "__main__":
    unittest.main()
