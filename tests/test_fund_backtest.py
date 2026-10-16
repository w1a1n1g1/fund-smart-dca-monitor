#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fund_backtest.py 与 ATR 自适应止盈的测试"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from argparse import Namespace

import fund_backtest as fb
from fund_strategy import cmd_exit_check


def _exit(**kw):
    base = dict(pe_percentile=50, gain=0.0, dd_20d=None, dd_high=None)
    base.update(kw)
    return cmd_exit_check(Namespace(**base))


class TestAtrAdaptive(unittest.TestCase):
    def test_no_atr_keeps_default_8(self):
        out = _exit(gain=15, dd_20d=8.5)
        self.assertEqual(out["take_profit_lines"]["rule2_drawdown_20d"]["threshold"], 8.0)

    def test_atr_4_gives_threshold_8(self):
        out = _exit(gain=15, dd_20d=8.5, atr_pct=4.0)
        self.assertEqual(out["take_profit_lines"]["rule2_drawdown_20d"]["threshold"], 8.0)

    def test_atr_5_gives_threshold_10(self):
        out = _exit(gain=15, dd_20d=8.5, atr_pct=5.0)
        line = out["take_profit_lines"]["rule2_drawdown_20d"]
        self.assertEqual(line["threshold"], 10.0)
        self.assertNotIn("已触发", line["state"])
        self.assertIn("ATR", line["note"])

    def test_atr_clamped_low_6(self):
        out = _exit(gain=15, dd_20d=6.5, atr_pct=2.0)
        self.assertEqual(out["take_profit_lines"]["rule2_drawdown_20d"]["threshold"], 6.0)
        self.assertIn("已触发", out["take_profit_lines"]["rule2_drawdown_20d"]["state"])

    def test_atr_clamped_high_12(self):
        out = _exit(gain=16, dd_high=12.5, atr_pct=7.0)
        self.assertEqual(out["take_profit_lines"]["rule3_trailing"]["threshold"], 12.0)
        self.assertIn("已触发", out["take_profit_lines"]["rule3_trailing"]["state"])

    def test_atr_invalid_raises(self):
        with self.assertRaises(ValueError):
            _exit(atr_pct=0)
        with self.assertRaises(ValueError):
            _exit(atr_pct=31)


class TestBacktest(unittest.TestCase):
    def test_demo_runs_and_has_keys(self):
        out = fb.main(["--demo"])
        for key in ("strategy", "benchmark_uniform_dca", "benchmark_lump_sum", "period"):
            self.assertIn(key, out)
        for key in ("total_return_pct", "sharpe", "max_drawdown_pct", "win_rate_pct"):
            self.assertIn(key, out["strategy"])

    def test_demo_sweep_rows(self):
        out = fb.main(["--demo", "--sweep", "dd"])
        self.assertEqual(out["command"], "backtest-sweep")
        self.assertEqual(len(out["rows"]), 8)
        self.assertEqual(out["rows"][0]["dd_threshold"], 5.0)

    def test_rising_market_positive_return(self):
        navs = [{"date": "d%04d" % i, "nav": round(1.0 + i * 0.001, 6)} for i in range(400)]
        out = fb.simulate(navs, pes=None, monthly_units=100.0, asset_type="a")
        self.assertGreater(out["total_return_pct"], 0)

    def test_high_pe_blocks_all_buys(self):
        navs = [{"date": "d%04d" % i, "nav": 1.0 + i * 0.0001} for i in range(300)]
        pes = [95.0] * 300  # PE 分位恒 >80% → 估值档 0，不投入
        out = fb.simulate(navs, pes=pes, monthly_units=100.0, asset_type="a")
        self.assertEqual(out["invested_units"], 0)
        self.assertEqual(out["total_return_pct"], 0.0)

    def test_bond_never_sells(self):
        navs = [{"date": "d%04d" % i, "nav": round(1.0 + (i % 30) * 0.002, 6)} for i in range(300)]
        out = fb.simulate(navs, pes=None, monthly_units=100.0, asset_type="bond")
        self.assertEqual(out["sell_count"], 0)

    def test_load_series_lshj_format(self):
        import tempfile
        rows_data = [["2020-01-%02d" % d, str(1.0 + d * 0.001)] for d in range(1, 29)]
        rows_data.append(["2020-02-01", "1.05"])
        rows_data.append(["2020-02-02", "1.06"])
        rows_data.reverse()  # 打乱顺序验证排序
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(rows_data, f)
            path = f.name
        try:
            rows = fb.load_series(path)
            self.assertEqual(rows[0]["date"], "2020-01-01")
            self.assertEqual(rows[-1]["date"], "2020-02-02")
        finally:
            os.unlink(path)

    def test_metrics_sharpe_and_drawdown_present(self):
        navs = [{"date": "d%04d" % i, "nav": 1.0 + 0.001 * ((-1) ** i) + i * 0.0002} for i in range(300)]
        out = fb.simulate(navs, pes=None, monthly_units=100.0, asset_type="a")
        self.assertIsInstance(out["sharpe"], float)
        self.assertGreaterEqual(out["max_drawdown_pct"], 0)

    def test_fee_reduces_return(self):
        navs, pes = fb.demo_data()
        no_fee = fb.simulate(navs, [r["pe"] for r in pes], fee_buy=0.0, fee_sell=0.0)["total_return_pct"]
        with_fee = fb.simulate(navs, [r["pe"] for r in pes], fee_buy=0.02, fee_sell=0.01)["total_return_pct"]
        self.assertGreater(no_fee, with_fee)

    def test_dca_interval_limits_averaging_down(self):
        navs = [{"date": "d%04d" % i, "nav": round(1.0 - i * 0.0005, 6)} for i in range(300)]
        pes = [15.0] * 300  # 低估值，信号持续 >0
        no_limit = fb.simulate(navs, pes, dca_interval=0)["invested_units"]
        strict = fb.simulate(navs, pes, dca_interval=999)["invested_units"]
        self.assertGreater(no_limit, strict)

    def test_rsi_confirmed_logic(self):
        self.assertTrue(fb.rsi_confirmed([20.0, 25.0], 1, 30.0, 2))
        self.assertFalse(fb.rsi_confirmed([20.0, 35.0], 1, 30.0, 2))
        self.assertTrue(fb.rsi_confirmed([20.0, 35.0], 0, 30.0, 0))
        self.assertFalse(fb.rsi_confirmed([20.0, 25.0], 0, 30.0, 2))  # 样本不足不确认


class TestExitGuard(unittest.TestCase):
    """止盈冷却期与规则1阶段上限：防止'触发后逐日循环卖出'抽血"""

    def test_exit_cooldown_spacing(self):
        navs, pes = fb.demo_data(600)
        res = fb.simulate(navs, [p["pe"] for p in pes])
        idxs = [int(s["date"][1:]) for s in res["sells"]]
        for a, b in zip(idxs, idxs[1:]):
            self.assertGreaterEqual(b - a, 20)

    def test_rule1_stage_cap_two_trims_per_episode(self):
        # 先低估值积累（PE 30），后 PE 恒 >95：规则1 每轮最多 1a+1b 两次，之后不再触发
        n = 400
        navs = [{"date": "d%04d" % i, "nav": round(1.0 * (1.001 ** i), 6)} for i in range(n)]
        pes = [30.0] * 200 + [98.0] * 200
        res = fb.simulate(navs, pes)
        rule1_sells = [s for s in res["sells"] if s["rule"].startswith("规则1")]
        self.assertEqual(len(rule1_sells), 2)

    def test_rule1_stage_resets_after_condition_clears(self):
        # 平坦净值（浮盈≈0，规则2/3 永不触发，隔离规则1）：
        # PE 30 买入 → PE 98 一轮(1a+1b) → PE 30 条件清除（阶段重置）→ PE 98 新一轮再触发
        n = 500
        navs = [{"date": "d%04d" % i, "nav": 1.0} for i in range(n)]
        pes = [30.0] * 200 + [98.0] * 60 + [30.0] * 100 + [98.0] * 140
        res = fb.simulate(navs, pes)
        rule1_sells = [s for s in res["sells"] if s["rule"].startswith("规则1")]
        self.assertGreaterEqual(len(rule1_sells), 3)  # 第1轮1a+1b，第2轮至少1a
        others = [s for s in res["sells"] if not s["rule"].startswith("规则1")]
        self.assertEqual(len(others), 0)  # 平坦净值下不应有规则2/3卖出


if __name__ == "__main__":
    unittest.main()
