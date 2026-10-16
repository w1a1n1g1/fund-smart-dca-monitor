#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fund_strategy.py 单元测试（零依赖，unittest 直接运行）"""
import os
import sys
import unittest
from argparse import Namespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fund_strategy as fs  # noqa: E402


class TestValuationMultiplier(unittest.TestCase):
    def test_tiers(self):
        cases = [(19.9, 1.5), (20.0, 1.0), (49.99, 1.0), (50.0, 0.5),
                 (79.99, 0.5), (80.0, 0.0), (100.0, 0.0)]
        for pct, expected in cases:
            with self.subTest(pe=pct):
                self.assertEqual(fs.pe_multiplier(pct), expected)

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            fs.cmd_signal(Namespace(pe_percentile=101, price=1.0, ma250=1.0, rsi6=30))


class TestMaMultiplier(unittest.TestCase):
    def test_tiers(self):
        cases = [(0.90, 1.2), (0.96, 1.1), (1.02, 0.9), (1.10, 0.8),
                 (0.95, 1.1), (1.05, 0.9)]
        for price, expected in cases:
            with self.subTest(price=price):
                mult, ratio = fs.ma_multiplier(price, 1.0)
                self.assertEqual(mult, expected)
                self.assertAlmostEqual(ratio, price, places=4)

    def test_invalid_ma(self):
        with self.assertRaises(ValueError):
            fs.ma_multiplier(1.0, 0)


class TestRsiModifier(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(fs.rsi_modifier(29.9), 1.15)
        self.assertEqual(fs.rsi_modifier(30.0), 1.0)


class TestSignal(unittest.TestCase):
    def test_reference_example(self):
        """README 示例：PE 34.8% / price 1.1138 / MA250 1.27 / RSI6 8.7 → 1.38"""
        out = fs.cmd_signal(Namespace(pe_percentile=34.8, price=1.1138,
                                      ma250=1.27, rsi6=8.7))
        self.assertEqual(out["valuation_multiplier"], 1.0)
        self.assertEqual(out["ma_multiplier"], 1.2)
        self.assertEqual(out["rsi_modifier"], 1.15)
        self.assertAlmostEqual(out["signal"], 1.38, places=2)
        self.assertIn("买入", out["action"])

    def test_capped_at_2(self):
        out = fs.cmd_signal(Namespace(pe_percentile=10, price=0.90,
                                      ma250=1.0, rsi6=20))
        self.assertTrue(out["capped"])
        self.assertEqual(out["signal"], 2.0)

    def test_overbought_cap(self):
        out = fs.cmd_signal(Namespace(pe_percentile=10, price=1.10,
                                      ma250=1.0, rsi6=85))
        self.assertEqual(out["signal"], 0.5)

    def test_zero_when_expensive(self):
        out = fs.cmd_signal(Namespace(pe_percentile=90, price=1.0,
                                      ma250=1.0, rsi6=30))
        self.assertEqual(out["signal"], 0.0)
        self.assertIn("不买", out["action"])


class TestDca(unittest.TestCase):
    def test_tiers(self):
        cases = [(4.99, 5.0), (5.0, 7.5), (9.99, 7.5), (10.0, 12.5),
                 (14.99, 12.5), (15.0, 20.0), (25.0, 20.0)]
        for dd, expected in cases:
            with self.subTest(dd=dd):
                out = fs.cmd_dca(Namespace(drawdown=dd, daily_budget=40))
                self.assertEqual(out["tier_multiple"], expected)

    def test_amount_example(self):
        out = fs.cmd_dca(Namespace(drawdown=8.3, daily_budget=40))
        self.assertEqual(out["tier_amount_example"], 300.0)

    def test_in_profit_no_tier(self):
        out = fs.cmd_dca(Namespace(drawdown=-3.0, daily_budget=40))
        self.assertEqual(out["tier_multiple"], 0)
        self.assertIn("浮盈", out["note"])


class TestExitCheck(unittest.TestCase):
    def test_rule1a_by_pe(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=87, gain=0, dd_20d=None, dd_high=None))
        self.assertTrue(out["take_profit_triggered"])
        self.assertIn("规则1a", out["actions"][0])

    def test_rule1b_by_gain(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=None, gain=31, dd_20d=None, dd_high=None))
        self.assertIn("规则1b", out["actions"][0])

    def test_boundary_gain_20_not_triggered(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=50, gain=20, dd_20d=None, dd_high=None))
        self.assertFalse(out["take_profit_triggered"])

    def test_rule2(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=50, gain=12, dd_20d=9, dd_high=None))
        self.assertIn("规则2", out["actions"][0])

    def test_rule2_premise_required(self):
        """浮盈未过 10%，即使回撤大也不触发规则 2"""
        out = fs.cmd_exit_check(Namespace(pe_percentile=50, gain=8, dd_20d=20, dd_high=None))
        self.assertEqual(out["actions"], [])

    def test_rule3(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=50, gain=16, dd_20d=1, dd_high=9))
        self.assertIn("规则3", out["actions"][0])

    def test_no_trigger(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=50, gain=5, dd_20d=1, dd_high=1))
        self.assertFalse(out["take_profit_triggered"])

    def test_invalid_pe(self):
        with self.assertRaises(ValueError):
            fs.cmd_exit_check(Namespace(pe_percentile=120, gain=0, dd_20d=None, dd_high=None))


if __name__ == "__main__":
    unittest.main()
