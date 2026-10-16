#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_backtest.py — 三因子定投策略轻量回测（零依赖，纯标准库）

用法：
  python scripts/fund_backtest.py --nav-file nav.json [--pe-file pe.json] [--monthly-units 100]
  python scripts/fund_backtest.py --demo                      # 合成数据演示（CI 冒烟用）
  python scripts/fund_backtest.py --nav-file nav.json --pe-file pe.json --sweep dd   # 回撤阈值参数扫描

策略口径（与 strategy-rules.md 一致）：
- 买入：每日投入 = 日均额 × 三因子信号（估值加权定投）；现价低于持仓均价且信号>0 时按补仓档加码
- 卖出：三重止盈按规则机械执行（估值/浮盈分批、20日高点回撤、历史高点移动回撤）
- 基准：均匀 DCA（每日固定投入）、一次性投入 LSI（等额计划资金首日全投）

纪律声明：金额仅为"模拟单位"，输出全部为百分比/倍数，不构成投资建议、不给任何金额建议。
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fund_strategy import ASSET_RULES, CAP, DCA_TIERS, ma_multiplier, pe_multiplier, rsi_modifier

TRADING_DAYS_PER_YEAR = 244  # A股年均交易日
DAYS_PER_MONTH = 21


# ---------- 数据加载 ----------

def load_series(path):
    """兼容两种格式：天天基金 LSHJ 二元数组 [["2020-01-02","1.05"],...]；字典列表 [{"date":..,"nav":..}]（兼容 FSRQ/DWJZ 键）"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    if data and isinstance(data[0], list):
        for r in data:
            rows.append({"date": str(r[0]), "nav": float(r[1])})
    else:
        for r in data:
            d = r.get("date") or r.get("FSRQ")
            v = r.get("nav", r.get("DWJZ"))
            if d and v is not None:
                rows.append({"date": str(d), "nav": float(v)})
    rows.sort(key=lambda x: x["date"])
    if len(rows) < 30:
        raise ValueError("净值序列至少需要 30 条")
    return rows


def load_pe(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    if data and isinstance(data[0], list):
        for r in data:
            rows.append({"date": str(r[0]), "pe": float(r[1])})
    else:
        for r in data:
            d = r.get("date") or r.get("FSRQ")
            v = r.get("pe", r.get("pe_percentile"))
            if d and v is not None:
                rows.append({"date": str(d), "pe": float(v)})
    rows.sort(key=lambda x: x["date"])
    return rows


def align_pe(navs, pe_rows):
    """PE 分位按日期对齐到净值序列，缺失日沿用最近可得值（前向填充）"""
    pe_map = {r["date"]: r["pe"] for r in pe_rows}
    out, last = [], None
    for rec in navs:
        if rec["date"] in pe_map:
            last = pe_map[rec["date"]]
        out.append(last)
    return out


def demo_data(n=600):
    """合成数据：带趋势与两个周期波动的净值 + 10-90 波动的 PE 分位"""
    navs, nav = [], 1.0
    for i in range(n):
        nav *= 1 + 0.0012 * math.sin(i / 9.0) + 0.0005 * math.cos(i / 41.0) + 0.00025
        navs.append({"date": "d%04d" % i, "nav": round(nav, 6)})
    pes = [{"date": "d%04d" % i, "pe": round(50 + 40 * math.sin(i / 97.0), 2)} for i in range(n)]
    return navs, pes


# ---------- 指标 ----------

def rsi6_series(navs):
    """简单平均 RSI6（6日窗口）。窗口不足时给中性值 50，输出口径为 RSI6 简单均值"""
    out = []
    for i in range(len(navs)):
        if i < 6:
            out.append(50.0)
            continue
        gains, losses = [], []
        for j in range(i - 5, i + 1):
            ch = navs[j] - navs[j - 1]
            gains.append(max(ch, 0.0))
            losses.append(max(-ch, 0.0))
        avg_g = sum(gains) / 6.0
        avg_l = sum(losses) / 6.0
        out.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
    return out


def dca_tier(drawdown_pct):
    for lo, hi, mult in DCA_TIERS:
        if lo <= drawdown_pct < hi:
            return mult
    return 0.0


# ---------- 策略模拟 ----------

def simulate(navs, pes=None, monthly_units=100.0, asset_type="a",
             dd_thr=8.0, trailing_thr=8.0, rsi_list=None):
    rules = ASSET_RULES[asset_type]
    daily_budget = monthly_units / float(DAYS_PER_MONTH)
    rsi_list = rsi_list or rsi6_series([r["nav"] for r in navs])

    shares = cash = invested = cost_basis = 0.0
    month_key, month_spent = None, 0.0
    peak_all = None
    value_curve, invest_curve, sells = [], [], []
    win = 0

    for i, rec in enumerate(navs):
        nav, date = rec["nav"], rec["date"]
        mk = date[:7]
        if mk != month_key:
            month_key, month_spent = mk, 0.0

        # 指标：MA250（样本>=20 日起用可得均值）
        win_ma = min(250, i + 1)
        ma = sum(r["nav"] for r in navs[i + 1 - win_ma:i + 1]) / win_ma if win_ma >= 20 else None
        rsi = rsi_list[i]

        # 三因子信号
        if ma is not None:
            est = pe_multiplier(pes[i]) if (pes and pes[i] is not None) else 1.0
            ma_m, _ = ma_multiplier(nav, ma)
            raw = est * ma_m * rsi_modifier(rsi)
            sig = min(raw, CAP)
            if rsi > 80:
                sig = min(sig, 0.5)
        else:
            sig = 0.0

        # 买入：日均额 × 信号 + 补仓档（受月预算约束，分批不梭哈）
        buy_units = daily_budget * sig
        if cost_basis > 0 and nav < cost_basis and sig > 0:
            buy_units += daily_budget * dca_tier((cost_basis / nav - 1) * 100)
        if buy_units > 0 and month_spent + buy_units <= monthly_units:
            new_sh = buy_units / nav
            cost_basis = (cost_basis * shares + nav * new_sh) / (shares + new_sh)
            shares += new_sh
            invested += buy_units
            month_spent += buy_units

        # 三重止盈（机械执行，规则2优先于规则3）
        hi20 = max(r["nav"] for r in navs[max(0, i - 19):i + 1])
        peak_all = nav if peak_all is None else max(peak_all, nav)
        gain = (nav / cost_basis - 1) * 100 if cost_basis > 0 else 0.0
        action, frac = None, 0.0
        if asset_type == "gold":
            if ma is not None and ((nav / ma - 1) * 100 > 20 or rsi > 80):
                action, frac = "规则1", 1 / 3
        elif asset_type != "bond":
            pe = pes[i] if pes else None
            if (pe is not None and pe > rules["pe2"]) or gain > rules["gain2"]:
                action, frac = "规则1b", 1 / 3
            elif (pe is not None and pe > rules["pe1"]) or gain > rules["gain1"]:
                action, frac = "规则1a", 1 / 3
        if cost_basis > 0 and gain > 10 and (hi20 / nav - 1) * 100 > dd_thr:
            action, frac = "规则2", 0.5
        if cost_basis > 0 and gain > 15 and (peak_all / nav - 1) * 100 > trailing_thr:
            action, frac = "规则3", 1 / 3
        if action and shares > 0:
            sell_sh = shares * frac
            proceeds = sell_sh * nav
            cost = sell_sh * cost_basis
            if proceeds > cost:
                win += 1
            sells.append({"date": date, "rule": action,
                          "gain_pct": round((proceeds - cost) / max(cost, 1e-9) * 100, 2)})
            shares -= sell_sh
            cash += proceeds

        value_curve.append(shares * nav + cash)
        invest_curve.append(invested)

    result = _metrics(navs, shares, cash, invested, sells, win, value_curve, invest_curve)
    result["sells"] = sells
    return result


def _metrics(navs, shares, cash, invested, sells, win, value_curve, invest_curve):
    final = shares * navs[-1]["nav"] + cash
    total_ret = (final - invested) / invested * 100 if invested > 0 else 0.0
    # 单位净值曲线（每单位累计投入对应的组合价值），用于 Sharpe 与回撤
    unit_curve = [v / max(inv, 1.0) for v, inv in zip(value_curve, invest_curve)]
    rets = [unit_curve[i] / unit_curve[i - 1] - 1 for i in range(1, len(unit_curve))
            if unit_curve[i - 1] > 0]
    if rets:
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        sharpe = round(mu / sd * math.sqrt(TRADING_DAYS_PER_YEAR), 3) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak, mdd = unit_curve[0], 0.0
    for u in unit_curve:
        if peak <= 0:
            peak = u
            continue
        mdd = max(mdd, (peak - u) / peak * 100)
        peak = max(peak, u)
    years = len(navs) / float(TRADING_DAYS_PER_YEAR)
    ann = ((1 + total_ret / 100) ** (1 / years) - 1) * 100 if years > 0 and total_ret > -100 else 0.0
    return {
        "invested_units": round(invested, 2),
        "final_value_units": round(final, 2),
        "total_return_pct": round(total_ret, 2),
        "annualized_return_pct": round(ann, 2),
        "sharpe": sharpe,
        "max_drawdown_pct": round(mdd, 2),
        "sell_count": len(sells),
        "win_rate_pct": round(win / len(sells) * 100, 1) if sells else 0.0,
    }


def bench_uniform_dca(navs, monthly_units):
    daily = monthly_units / float(DAYS_PER_MONTH)
    shares = invested = 0.0
    mk, spent = None, 0.0
    for rec in navs:
        d = rec["date"][:7]
        if d != mk:
            mk, spent = d, 0.0
        if spent + daily <= monthly_units:
            shares += daily / rec["nav"]
            invested += daily
            spent += daily
    final = shares * navs[-1]["nav"]
    return {"total_return_pct": round((final - invested) / invested * 100, 2) if invested else 0.0}


def bench_lsi(navs, monthly_units):
    months = max(1, round(len(navs) / float(DAYS_PER_MONTH)))
    total = monthly_units * months
    final = total / navs[0]["nav"] * navs[-1]["nav"]
    return {"total_return_pct": round((final - total) / total * 100, 2)}


# ---------- 命令入口 ----------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="fund_backtest.py",
        description="三因子定投策略轻量回测：只输出百分比/倍数，不给金额建议")
    p.add_argument("--nav-file", help="历史净值 JSON（天天基金格式或字典列表）")
    p.add_argument("--pe-file", help="PE 分位历史 JSON（可选，缺失则估值因子=1.0）")
    p.add_argument("--monthly-units", type=float, default=100.0, help="月度模拟预算（单位数，仅模拟用）")
    p.add_argument("--asset-type", choices=list(ASSET_RULES.keys()), default="a")
    p.add_argument("--dd-thr", type=float, default=8.0, help="回撤止盈阈值%%（默认 8）")
    p.add_argument("--trailing-thr", type=float, default=8.0, help="移动止盈阈值%%（默认 8）")
    p.add_argument("--sweep", choices=["dd"], help="参数扫描：dd=回撤阈值 5-12 步长 1")
    p.add_argument("--demo", action="store_true", help="使用内置合成数据演示")
    args = p.parse_args(argv)

    if args.demo:
        navs, pes = demo_data()
        pes_aligned = [r["pe"] for r in pes]
    else:
        if not args.nav_file:
            p.error("必须提供 --nav-file 或 --demo")
        navs = load_series(args.nav_file)
        pes_aligned = align_pe(navs, load_pe(args.pe_file)) if args.pe_file else None

    if args.sweep == "dd":
        rows = []
        for dd in [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0]:
            m = simulate(navs, pes_aligned, args.monthly_units, args.asset_type, dd_thr=dd)["total_return_pct"]
            rows.append({"dd_threshold": dd, "total_return_pct": m})
        return {"command": "backtest-sweep", "rows": rows,
                "note": "扫描固定移动止盈阈值，仅对比 total_return_pct；避免在单一序列上过拟合"}

    result = simulate(navs, pes_aligned, args.monthly_units, args.asset_type,
                      dd_thr=args.dd_thr, trailing_thr=args.trailing_thr)
    return {
        "command": "backtest",
        "period": {"start": navs[0]["date"], "end": navs[-1]["date"], "days": len(navs)},
        "asset_type": args.asset_type,
        "strategy": result,
        "benchmark_uniform_dca": bench_uniform_dca(navs, args.monthly_units),
        "benchmark_lump_sum": bench_lsi(navs, args.monthly_units),
        "sells": result.pop("sells", []),
        "note": "金额仅为模拟单位；输出全部为百分比/倍数，不构成投资建议；单一序列回测存在过拟合风险，结论须多指数交叉验证",
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
