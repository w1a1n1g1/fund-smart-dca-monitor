#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_strategy.py — 基金智能定投信号计算器（零依赖，纯标准库）

三种子命令：
  signal      买入信号 = 估值倍数 × 均线倍数 × RSI修正（封顶 2.0）
  dca         补仓档位（现价低于成本时，按浮亏深度给日均额定额的倍数）
  exit-check  三重止盈体检

本工具只输出信号、档位与倍数，不给具体金额建议、不代任何操作。
"""
import argparse
import json
import sys

CAP = 2.0

# 补仓档位：(浮亏下限%, 浮亏上限%, 日均额定额倍数)
DCA_TIERS = [
    (0.0, 5.0, 5.0),
    (5.0, 10.0, 7.5),
    (10.0, 15.0, 12.5),
    (15.0, float("inf"), 20.0),
]


def pe_multiplier(pe_percentile: float) -> float:
    """PE 近52周分位 → 估值倍数"""
    if pe_percentile < 20:
        return 1.5
    if pe_percentile < 50:
        return 1.0
    if pe_percentile < 80:
        return 0.5
    return 0.0


def ma_multiplier(price: float, ma250: float):
    """现价/MA250 → 均线倍数"""
    if ma250 <= 0:
        raise ValueError("ma250 必须大于 0")
    ratio = price / ma250
    if ratio < 0.95:
        mult = 1.2
    elif ratio < 1.0:
        mult = 1.1
    elif ratio <= 1.05:
        mult = 0.9
    else:
        mult = 0.8
    return mult, round(ratio, 4)


def rsi_modifier(rsi: float) -> float:
    """RSI 修正：RSI6<30 超卖 ×1.15（RSI6 口径按更敏感处理）"""
    return 1.15 if rsi < 30 else 1.0


def cmd_signal(args) -> dict:
    for name, value in (("pe_percentile", args.pe_percentile), ("rsi6", args.rsi6)):
        if not (0 <= value <= 100):
            raise ValueError(f"{name} 应在 0-100 之间")
    if args.price <= 0:
        raise ValueError("price 必须大于 0")

    est = pe_multiplier(args.pe_percentile)
    ma, ratio = ma_multiplier(args.price, args.ma250)
    rsi_m = rsi_modifier(args.rsi6)
    raw = est * ma * rsi_m

    # 超买封顶：RSI6 > 80 时信号封顶 0.5
    overbought = args.rsi6 > 80
    signal = min(raw, CAP)
    if overbought:
        signal = min(signal, 0.5)

    if signal <= 0:
        action = "不买（估值档为 0 或超买封顶）"
    elif signal < 1.0:
        action = "只观察，不动手"
    else:
        action = "按纪律买入（给档位与占比）"

    return {
        "command": "signal",
        "pe_percentile": args.pe_percentile,
        "valuation_multiplier": est,
        "price_ma250_ratio": ratio,
        "ma_multiplier": ma,
        "rsi6": args.rsi6,
        "rsi_modifier": rsi_m,
        "signal_raw": round(raw, 4),
        "signal": round(signal, 2),
        "capped": raw > CAP,
        "action": action,
    }


def cmd_dca(args) -> dict:
    """补仓档位：浮亏深度 → 日均额定额倍数"""
    dd = args.drawdown
    if dd < 0:
        return {
            "command": "dca",
            "drawdown_pct": round(dd, 2),
            "tier_multiple": 0,
            "note": "现价高于成本（浮盈中），不触发补仓档位",
        }
    for lo, hi, mult in DCA_TIERS:
        if lo <= dd < hi:
            return {
                "command": "dca",
                "drawdown_pct": round(dd, 2),
                "tier_multiple": mult,
                "daily_budget": args.daily_budget,
                "tier_amount_example": round(mult * args.daily_budget, 2),
                "note": "tier_amount_example 仅为演示换算；正式使用请以'倍数'表达，金额由用户自行决定",
            }
    return {"command": "dca", "drawdown_pct": dd, "tier_multiple": 0, "note": "未落入任何档位"}


def cmd_exit_check(args) -> dict:
    """三重止盈体检"""
    if args.pe_percentile is not None and not (0 <= args.pe_percentile <= 100):
        raise ValueError("pe_percentile 应在 0-100 之间")

    gain = args.gain if args.gain is not None else 0.0
    actions = []

    # 规则 1：估值/浮盈分批止盈
    rule1a = (args.pe_percentile is not None and args.pe_percentile > 85) or gain > 20
    rule1b = (args.pe_percentile is not None and args.pe_percentile > 95) or gain > 30
    if rule1b:
        actions.append("规则1b：PE分位>95% 或 浮盈>30% → 累计减仓 2/3（本次再减 1/3）")
    elif rule1a:
        actions.append("规则1a：PE分位>85% 或 浮盈>20% → 减仓 1/3")

    # 规则 2：回撤止盈（浮盈>10% 后，距20日高点回撤>8% 卖一半）
    if gain > 10 and args.dd_20d is not None and args.dd_20d > 8:
        actions.append("规则2：浮盈>10% 且 距20日高点回撤>8% → 卖出 1/2")

    # 规则 3：移动止盈（浮盈>15% 后，距最高点回撤>8% 再卖1/3）
    if gain > 15 and args.dd_high is not None and args.dd_high > 8:
        actions.append("规则3：浮盈>15% 且 距最高点回撤>8% → 卖出 1/3")

    return {
        "command": "exit-check",
        "pe_percentile": args.pe_percentile,
        "gain_pct": gain,
        "drawdown_from_20d_high_pct": args.dd_20d,
        "drawdown_from_all_time_high_pct": args.dd_high,
        "take_profit_triggered": len(actions) > 0,
        "actions": actions,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fund_strategy.py",
        description="基金智能定投信号计算器：只算信号、给档位，不给金额、不代操作",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("signal", help="买入信号计算")
    s.add_argument("--pe-percentile", type=float, required=True, help="PE 近52周分位（0-100）")
    s.add_argument("--price", type=float, required=True, help="现价（或最新净值对应的指数点位）")
    s.add_argument("--ma250", type=float, required=True, help="250日均线值")
    s.add_argument("--rsi6", type=float, required=True, help="RSI6 值")

    d = sub.add_parser("dca", help="补仓档位计算")
    d.add_argument("--drawdown", type=float, required=True, help="现价低于成本的百分比（正数）")
    d.add_argument("--daily-budget", type=float, default=40.0, help="日均额定额（示例值 40）")

    e = sub.add_parser("exit-check", help="三重止盈体检")
    e.add_argument("--pe-percentile", type=float, default=None, help="PE 近52周分位")
    e.add_argument("--gain", type=float, default=None, help="浮盈百分比")
    e.add_argument("--dd-20d", type=float, default=None, dest="dd_20d", help="距20日高点回撤百分比")
    e.add_argument("--dd-high", type=float, default=None, dest="dd_high", help="距历史最高点回撤百分比")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "signal":
            result = cmd_signal(args)
        elif args.command == "dca":
            result = cmd_dca(args)
        else:
            result = cmd_exit_check(args)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

    if getattr(args, "json", False) or True:  # 默认 JSON 输出，便于流水线接入
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
