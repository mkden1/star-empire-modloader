"""construction_stock.py: what construction_eta and construction_tooltip share about a station's cargo."""
import math
import unittest

import construction_eta
import construction_stock
import construction_tooltip


class NumTests(unittest.TestCase):
    def test_numbers_pass_through_and_everything_else_is_the_default(self):
        self.assertEqual(construction_stock.num("4.5"), 4.5)
        self.assertEqual(construction_stock.num(None, 7.0), 7.0)
        self.assertEqual(construction_stock.num("x"), 0.0)
        self.assertEqual(construction_stock.num(math.inf, 1.0), 1.0)
        self.assertEqual(construction_stock.num(float("nan"), 2.0), 2.0)


class StockFromCargoTests(unittest.TestCase):
    def test_only_resource_rows_count_and_workers_are_left_out(self):
        cargo = [{"item_category": "resource", "item_key": "metal", "cargo_size": 210267},
                 {"item_category": "resource", "item_key": "oil", "quantity": "30"},
                 {"item_category": "resource", "item_key": "workers", "cargo_size": 50},
                 {"item_category": "module", "item_key": "drill", "quantity": 2}, "junk"]
        self.assertEqual(construction_stock.stock_from_cargo(cargo), {"metal": 210267.0, "oil": 30.0})

    def test_no_cargo_list_is_none(self):
        self.assertIsNone(construction_stock.stock_from_cargo(None))
        self.assertIsNone(construction_stock.stock_from_cargo("x"))


class BothModsUseTheSameFunctionsTests(unittest.TestCase):
    def test_construction_eta_and_construction_tooltip_import_them_from_core(self):
        self.assertIs(construction_eta.stock_from_cargo, construction_stock.stock_from_cargo)
        self.assertIs(construction_tooltip.stock_from_cargo, construction_stock.stock_from_cargo)
        self.assertIs(construction_eta._num, construction_stock.num)
        self.assertIs(construction_tooltip._num, construction_stock.num)


if __name__ == "__main__":
    unittest.main()
