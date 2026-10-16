#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fund_strategy.py — 基金智能定投信号计算器（零依赖，纯标准库）

四种子命令：
  signal      买入信号 = 估值倍数 × 均线倍数 × RSI修正（封顶 2.0）
  dca         补仓档位（现价低于成本时，按浮亏深度给日均额定额的倍数）
  exit-check  三重止盈体检
  peer-check  盘友圈公开实盘一致性分析（对照三因子信号诊断发帖操作）

本工具只输出信号、档位与倍数，不给具体金额建议、不代任何操作。
"""
import argparse
import json
import sys
from argparse import Namespace

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


# 估值止盈分资产类型档位（2026-10-16 规则升级）
ASSET_RULES = {
    "a":    {"name": "A股/红利", "pe1": 85, "gain1": 20.0, "pe2": 95, "gain2": 30.0},
    "qdii": {"name": "QDII",    "pe1": 85, "gain1": 25.0, "pe2": 95, "gain2": 30.0},
    "gold": {"name": "黄金"},
    "bond": {"name": "纯债"},
}


def _line(threshold, current, state, note=""):
    """止盈线距离：threshold-current 为正表示距触发还有余量，为负表示已越过"""
    return {
        "threshold": threshold,
        "current": current,
        "distance": round(threshold - current, 2),
        "state": state,
        "note": note,
    }


def cmd_exit_check(args) -> dict:
    """三重止盈体检（估值止盈按资产类型分档），输出距各止盈线的距离"""
    if args.pe_percentile is not None and not (0 <= args.pe_percentile <= 100):
        raise ValueError("pe_percentile 应在 0-100 之间")

    asset = getattr(args, "asset_type", "a") or "a"
    if asset not in ASSET_RULES:
        raise ValueError("asset_type 应为 a/qdii/gold/bond")
    rules = ASSET_RULES[asset]

    gain = args.gain if args.gain is not None else 0.0
    rsi = getattr(args, "rsi6", None)
    ma_dev = getattr(args, "ma_deviation", None)
    if rsi is not None and not (0 <= rsi <= 100):
        raise ValueError("rsi6 应在 0-100 之间")

    actions = []
    lines = {}

    # 规则 1：估值/浮盈分批止盈（按资产类型分档）
    if asset == "bond":
        lines["rule1_valuation"] = {"state": "不适用", "note": "纯债不止盈"}
    elif asset == "gold":
        trig_dev = ma_dev is not None and ma_dev > 20
        trig_rsi = rsi is not None and rsi > 80
        if trig_dev or trig_rsi:
            reasons = []
            if trig_dev:
                reasons.append("偏离250日均线>20%")
            if trig_rsi:
                reasons.append("RSI>80")
            actions.append("规则1：黄金 " + " 或 ".join(reasons) + " → 减仓 1/3")
        if ma_dev is not None:
            lines["rule1_ma_deviation"] = _line(
                20, ma_dev, "已触发" if trig_dev else ("接近" if ma_dev > 18 else "正常"))
        if rsi is not None:
            lines["rule1_rsi"] = _line(
                80, rsi, "已触发" if trig_rsi else ("接近" if rsi > 75 else "正常"))
    else:
        pe = args.pe_percentile
        trig1a = (pe is not None and pe > rules["pe1"]) or gain > rules["gain1"]
        trig1b = (pe is not None and pe > rules["pe2"]) or gain > rules["gain2"]
        if trig1b:
            actions.append(
                f"规则1b：PE分位>{rules['pe2']}% 或 浮盈>{rules['gain2']}% → 累计减仓 2/3（本次再减 1/3）")
        elif trig1a:
            actions.append(f"规则1a：PE分位>{rules['pe1']}% 或 浮盈>{rules['gain1']}% → 减仓 1/3")
        if pe is not None:
            lines["rule1_pe"] = _line(
                rules["pe1"], pe,
                "已触发" if pe > rules["pe1"] else ("接近" if pe > rules["pe1"] - 5 else "正常"))
        lines["rule1_gain"] = _line(
            rules["gain1"], gain,
            "已触发" if gain > rules["gain1"] else ("接近" if gain > rules["gain1"] - 5 else "正常"))

    # ATR 自适应阈值（可选）：提供 --atr-pct 时，回撤阈值 = max(6, min(12, 2×ATR%))
    atr = getattr(args, "atr_pct", None)
    dd_thr = 8.0
    atr_note = ""
    if atr is not None:
        if not (0 < atr <= 30):
            raise ValueError("atr_pct 应在 0-30 之间（ATR/价格×100）")
        dd_thr = max(6.0, min(12.0, 2.0 * atr))
        atr_note = f"阈值=2×ATR 自适应（ATR={atr}%）"

    # 规则 2：回撤止盈（浮盈>10% 后，距20日高点回撤超阈值 卖一半）——任何基金
    if args.dd_20d is not None:
        active = gain > 10
        trig = active and args.dd_20d > dd_thr
        if trig:
            actions.append(f"规则2：浮盈>10% 且 距20日高点回撤>{dd_thr}% → 卖出 1/2")
        lines["rule2_drawdown_20d"] = _line(
            dd_thr, args.dd_20d,
            "已触发" if trig else ("未激活（浮盈需>10%）" if not active else ("接近" if args.dd_20d > dd_thr - 2 else "正常")),
            atr_note)

    # 规则 3：移动止盈（浮盈>15% 后，距最高点回撤超阈值 再卖1/3）
    if args.dd_high is not None:
        active = gain > 15
        trig = active and args.dd_high > dd_thr
        if trig:
            actions.append(f"规则3：浮盈>15% 且 距最高点回撤>{dd_thr}% → 卖出 1/3")
        lines["rule3_trailing"] = _line(
            dd_thr, args.dd_high,
            "已触发" if trig else ("未激活（浮盈需>15%）" if not active else ("接近" if args.dd_high > dd_thr - 2 else "正常")),
            atr_note)

    return {
        "command": "exit-check",
        "asset_type": asset,
        "asset_name": rules["name"],
        "pe_percentile": args.pe_percentile,
        "gain_pct": gain,
        "drawdown_from_20d_high_pct": args.dd_20d,
        "drawdown_from_all_time_high_pct": args.dd_high,
        "take_profit_triggered": len(actions) > 0,
        "actions": actions,
        "take_profit_lines": lines,
    }


def cmd_peer_check(args) -> dict:
    """盘友圈公开实盘一致性分析：对照三因子信号诊断发帖人的操作"""
    if args.cost <= 0:
        raise ValueError("cost 必须大于 0")

    sig_out = cmd_signal(args)
    gain = (args.price / args.cost - 1) * 100
    sig = sig_out["signal"]
    action = args.action
    flags = []

    if action == "buy":
        if sig >= 1.0:
            verdict = "与纪律信号一致：当前三因子信号 ≥1.0，属于顺势买入"
        elif sig > 0:
            verdict = "信号不足（<1.0）：发帖人抢跑，参考需谨慎"
        else:
            verdict = "逆纪律买入：估值档为 0 或超买封顶，不建议跟"
        if sig_out["price_ma250_ratio"] > 1.05:
            flags.append("年线上方追买：趋势高位，注意回撤")
        if sig_out["valuation_multiplier"] == 0:
            flags.append("高估值买入：PE 分位 >80%")
    elif action == "sell":
        if gain < 0:
            verdict = "浮亏卖出（割肉）：注意情绪传染，非纪律行为"
        else:
            ex = cmd_exit_check(Namespace(pe_percentile=args.pe_percentile, gain=gain,
                                          dd_20d=args.dd_20d, dd_high=args.dd_high))
            if ex["take_profit_triggered"]:
                verdict = "与止盈纪律一致：" + ex["actions"][0]
            else:
                verdict = "浮盈卖出但未触发止盈规则：发帖人主动减仓，非机械纪律"
    else:
        verdict = "观望/持仓不动：以当前信号自行复核"

    if gain > 30:
        flags.append("高浮盈晒单：幸存者偏差样本，风险常被省略")

    return {
        "command": "peer-check",
        "peer_action": action,
        "peer_cost": args.cost,
        "peer_gain_pct": round(gain, 2),
        "current_signal": sig,
        "signal_detail": sig_out,
        "verdict": verdict,
        "risk_flags": flags,
        "note": "仅基于帖子公开信息与当前行情做纪律对照，不构成跟单建议；不存储他人可识别信息",
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
    e.add_argument("--asset-type", default="a", dest="asset_type", choices=["a", "qdii", "gold", "bond"],
                   help="资产类型：a=A股/红利、qdii=海外、gold=黄金、bond=纯债（估值止盈分档）")
    e.add_argument("--rsi6", type=float, default=None, help="RSI6 值（黄金止盈线用，0-100）")
    e.add_argument("--ma-deviation", type=float, default=None, dest="ma_deviation",
                   help="现价偏离250日均线百分比，正为上方（黄金止盈线用）")
    e.add_argument("--atr-pct", type=float, default=None, dest="atr_pct",
                   help="近14日ATR/价格×100（可选）；提供时回撤阈值=2×ATR 并钳位在 6-12 之间")

    p_ = sub.add_parser("peer-check", help="盘友圈公开实盘一致性分析")
    p_.add_argument("--action", required=True, choices=["buy", "sell", "hold"], help="发帖人操作：buy/sell/hold")
    p_.add_argument("--cost", type=float, required=True, help="发帖人披露的成本")
    p_.add_argument("--price", type=float, required=True, help="该标的现价")
    p_.add_argument("--pe-percentile", type=float, required=True, help="跟踪指数 PE 近52周分位（0-100）")
    p_.add_argument("--ma250", type=float, required=True, help="250日均线值")
    p_.add_argument("--rsi6", type=float, required=True, help="RSI6 值")
    p_.add_argument("--dd-20d", type=float, default=None, dest="dd_20d", help="距20日高点回撤百分比（可选）")
    p_.add_argument("--dd-high", type=float, default=None, dest="dd_high", help="距历史最高点回撤百分比（可选）")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "signal":
            result = cmd_signal(args)
        elif args.command == "dca":
            result = cmd_dca(args)
        elif args.command == "peer-check":
            result = cmd_peer_check(args)
        else:
            result = cmd_exit_check(args)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

    # 默认 JSON 输出，便于流水线与 AI 智能体解析
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
