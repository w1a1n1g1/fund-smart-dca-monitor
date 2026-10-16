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


class TestAssetTypeExitCheck(unittest.TestCase):
    """估值止盈按资产类型分档（2026-10-16 规则升级）"""

    def test_qdii_gain_threshold(self):
        """QDII 浮盈第一档为 25%（A股为 20%）：22% 不触发，26% 触发"""
        out22 = fs.cmd_exit_check(Namespace(pe_percentile=None, gain=22, dd_20d=None, dd_high=None,
                                            asset_type="qdii"))
        self.assertFalse(out22["take_profit_triggered"])
        out26 = fs.cmd_exit_check(Namespace(pe_percentile=None, gain=26, dd_20d=None, dd_high=None,
                                            asset_type="qdii"))
        self.assertIn("规则1a", out26["actions"][0])

    def test_gold_deviation_trigger(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=None, gain=0, dd_20d=None, dd_high=None,
                                          asset_type="gold", ma_deviation=25, rsi6=50))
        self.assertIn("偏离250日均线>20%", out["actions"][0])
        self.assertEqual(out["take_profit_lines"]["rule1_ma_deviation"]["state"], "已触发")

    def test_gold_rsi_trigger(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=None, gain=0, dd_20d=None, dd_high=None,
                                          asset_type="gold", ma_deviation=5, rsi6=85))
        self.assertIn("RSI>80", out["actions"][0])

    def test_bond_no_valuation_tp(self):
        out = fs.cmd_exit_check(Namespace(pe_percentile=99, gain=50, dd_20d=None, dd_high=None,
                                          asset_type="bond"))
        self.assertFalse(out["take_profit_triggered"])
        self.assertEqual(out["take_profit_lines"]["rule1_valuation"]["state"], "不适用")

    def test_lines_distance_output(self):
        """每日输出距各止盈线的距离"""
        out = fs.cmd_exit_check(Namespace(pe_percentile=84, gain=19, dd_20d=7, dd_high=None,
                                          asset_type="a"))
        self.assertFalse(out["take_profit_triggered"])
        self.assertEqual(out["take_profit_lines"]["rule1_pe"]["state"], "接近")
        self.assertAlmostEqual(out["take_profit_lines"]["rule1_pe"]["distance"], 1, places=2)
        self.assertEqual(out["take_profit_lines"]["rule2_drawdown_20d"]["state"], "接近")

    def test_gold_invalid_rsi(self):
        with self.assertRaises(ValueError):
            fs.cmd_exit_check(Namespace(pe_percentile=None, gain=0, dd_20d=None, dd_high=None,
                                        asset_type="gold", rsi6=120))


class TestPeerCheck(unittest.TestCase):
    def test_buy_consistent(self):
        out = fs.cmd_peer_check(Namespace(action="buy", cost=1.02, price=1.1138,
                                          pe_percentile=34.8, ma250=1.27, rsi6=8.7,
                                          dd_20d=None, dd_high=None))
        self.assertIn("一致", out["verdict"])
        self.assertAlmostEqual(out["peer_gain_pct"], 9.2, places=1)

    def test_buy_against_discipline(self):
        out = fs.cmd_peer_check(Namespace(action="buy", cost=1.02, price=1.10,
                                          pe_percentile=90, ma250=1.0, rsi6=50,
                                          dd_20d=None, dd_high=None))
        self.assertIn("逆纪律", out["verdict"])
        self.assertTrue(any("高估值" in f for f in out["risk_flags"]))

    def test_sell_take_profit_consistent(self):
        out = fs.cmd_peer_check(Namespace(action="sell", cost=1.0, price=1.25,
                                          pe_percentile=87, ma250=1.0, rsi6=50,
                                          dd_20d=None, dd_high=None))
        self.assertIn("止盈纪律一致", out["verdict"])

    def test_sell_at_loss(self):
        out = fs.cmd_peer_check(Namespace(action="sell", cost=1.2, price=1.0,
                                          pe_percentile=40, ma250=1.0, rsi6=50,
                                          dd_20d=None, dd_high=None))
        self.assertIn("割肉", out["verdict"])

    def test_risk_flag_high_gain(self):
        out = fs.cmd_peer_check(Namespace(action="buy", cost=1.0, price=1.40,
                                          pe_percentile=30, ma250=1.3, rsi6=50,
                                          dd_20d=None, dd_high=None))
        self.assertTrue(any("幸存者偏差" in f for f in out["risk_flags"]))

    def test_invalid_cost(self):
        with self.assertRaises(ValueError):
            fs.cmd_peer_check(Namespace(action="buy", cost=0, price=1.0,
                                        pe_percentile=30, ma250=1.0, rsi6=50,
                                        dd_20d=None, dd_high=None))


class TestPosterTrack(unittest.TestCase):
    def _args(self, records):
        import json
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"poster": "代号X", "records": records}, fh, ensure_ascii=False)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return Namespace(file=fh.name, prev_score=None)

    def test_mixed_timeline_scoring(self):
        # 顺势买（低PE+年线下）=aligned；高PE追买=against；hold 不入分母
        args = self._args([
            {"date": "2026-09-01", "action": "buy", "price": 0.90, "ma250": 1.0,
             "pe_percentile": 15, "rsi6": 25},
            {"date": "2026-09-20", "action": "hold", "price": 0.95, "ma250": 1.0},
            {"date": "2026-10-01", "action": "buy", "price": 1.10, "ma250": 1.0,
             "pe_percentile": 90, "rsi6": 60},
        ])
        out = fs.cmd_poster_track(args)
        self.assertEqual(out["graded_actions"], 2)
        self.assertEqual(out["hold_actions"], 1)
        self.assertEqual(out["counts"], {"aligned": 1, "partial": 0, "against": 1})
        # 基础分 50（1/2 合规），该笔同时命中"年线上方追买"+"高估值买入"2 个标签 -10
        self.assertEqual(out["discipline_score"], 40.0)
        self.assertTrue(any("高估值买入" in f for f in out["risk_flag_counts"]))

    def test_sell_aligned_and_cut_loss(self):
        # 浮盈 22% 触发 A 股第二档止盈=aligned；浮亏卖=against
        args = self._args([
            {"date": "2026-09-01", "action": "sell", "cost": 1.0, "price": 1.22,
             "ma250": 1.0, "pe_percentile": 30, "rsi6": 50},
            {"date": "2026-10-01", "action": "sell", "cost": 1.1, "price": 1.0,
             "ma250": 1.0, "pe_percentile": 30, "rsi6": 50},
        ])
        out = fs.cmd_poster_track(args)
        self.assertEqual(out["counts"]["aligned"], 1)
        self.assertEqual(out["counts"]["against"], 1)
        self.assertEqual(out["discipline_score"], 50.0)

    def test_prev_score_delta(self):
        args = self._args([
            {"date": "2026-09-01", "action": "buy", "price": 0.90, "ma250": 1.0,
             "pe_percentile": 15, "rsi6": 25},
        ])
        args.prev_score = 60.0
        out = fs.cmd_poster_track(args)
        self.assertEqual(out["score_delta"], round(out["discipline_score"] - 60.0, 1))

    def test_empty_records_rejected(self):
        with self.assertRaises(ValueError):
            fs.cmd_poster_track(self._args([]))

    def test_cli_end_to_end(self):
        import io
        import contextlib
        import json
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"records": [{"action": "buy", "price": 0.9, "ma250": 1.0,
                                "pe_percentile": 15, "rsi6": 25}]}, fh)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fs.main(["poster-track", "--file", fh.name])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["counts"]["aligned"], 1)


if __name__ == "__main__":
    unittest.main()
