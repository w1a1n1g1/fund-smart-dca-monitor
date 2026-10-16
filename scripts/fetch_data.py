#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_data.py — 回测数据抓取（零依赖，纯标准库）

为 fund_backtest.py 准备输入数据：
  1. 基金累计净值：天天基金 pingzhongdata（Data_ACWorthTrend）
  2. 指数 PE 近 52 周滚动分位：中证指数官网日频 PE(TTM) 自算（244 交易日窗口）

用法：
  # 只取净值
  python scripts/fetch_data.py --fund 110020 --nav-out nav.json --start 2019-01-01
  # 只取 PE 分位
  python scripts/fetch_data.py --index 000300 --pe-out pe.json --start 2019-01-01
  # 一次取一对（基金净值 + 跟踪指数 PE 分位）
  python scripts/fetch_data.py --fund 110020 --index 000300 \
      --nav-out hs300_nav.json --pe-out hs300_pe.json --start 2019-01-01

口径说明：
  - 黄金/QDII 基金无对应 A 股指数 PE：只取净值即可（QDII 回测退化为"均线+RSI+浮盈/回撤止盈"
    参数验证，规则1 的 PE 分支不生效，输出会注明）
  - PE 分位窗口不足 244 个交易日（首年）输出 null，与回测首年 MA250 不足、不产生信号的口径一致
  - 本脚本只做公开数据抓取，不存储任何个人账户信息
"""
import argparse
import datetime
import json
import re
import urllib.request

PE_WINDOW = 244  # 近 52 周交易日
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def _get(url, referer=None, timeout=30):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "ignore")


def fetch_nav(fund_code, start=None):
    """天天基金累计净值 Data_ACWorthTrend → [["YYYY-MM-DD", nav], ...]"""
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % fund_code
    text = _get(url, referer="https://fund.eastmoney.com/")
    m = re.search(r"Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if not m:
        raise RuntimeError("未在 pingzhongdata 中找到 Data_ACWorthTrend（基金代码 %s 是否正确？）" % fund_code)
    rows = json.loads(m.group(1))
    out = []
    for ts, v in rows:  # Data_ACWorthTrend 元素为 [毫秒时间戳, 累计净值]
        if v is None:
            continue
        d = datetime.datetime.fromtimestamp(int(ts) / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
        out.append([d, float(v)])
    if start:
        out = [r for r in out if r[0] >= start]
    if len(out) < 30:
        raise RuntimeError("净值序列仅 %d 条（<30），请检查基金代码或起始日期" % len(out))
    return out


def _rolling_percentile(values, window=PE_WINDOW, min_obs=120):
    """近 window 日滚动分位：窗口内严格小于当前值的有效值比例×100。

    窗口不足输出 None；窗口内有效值 < min_obs（早期指数 PE 可能长期缺失）也输出 None。
    """
    out = []
    for i, v in enumerate(values):
        if v is None or i + 1 < window:
            out.append(None)
        else:
            hist = [x for x in values[i + 1 - window:i + 1] if x is not None]
            if len(hist) < min_obs:
                out.append(None)
            else:
                lower = sum(1 for x in hist if x < v)
                out.append(round(lower / (len(hist) - 1) * 100, 2))
    return out


def fetch_index_pe_pct(index_code, start=None, end=None):
    """中证指数官网日频 PE(TTM, peg 字段) → 近52周滚动分位 [["YYYY-MM-DD", pct], ...]

    无论 start 取何值，内部统一从 2010 年开始多取数据以喂满 244 日窗口，
    再按 start 过滤，保证回测起始年即有分位（否则首年信号会退化为无 PE 口径）。
    """
    end = (end or datetime.date.today().isoformat()).replace("-", "")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           "?indexCode=%s&startDate=20100101&endDate=%s" % (index_code, end))
    text = _get(url, referer="https://www.csindex.com.cn/")
    payload = json.loads(text)
    if str(payload.get("code")) != "200" or not payload.get("data"):
        raise RuntimeError("中证指数接口未返回数据（指数代码 %s 是否正确？响应：%s）"
                           % (index_code, str(payload)[:120]))
    rows, pe_values = [], []
    for rec in payload["data"]:
        d = str(rec["tradeDate"])
        date_iso = "%s-%s-%s" % (d[0:4], d[4:6], d[6:8])
        pe = rec.get("peg")
        rows.append(date_iso)
        pe_values.append(float(pe) if pe is not None else None)
    pcts = _rolling_percentile(pe_values)
    out = [[d, p] for d, p in zip(rows, pcts) if p is not None]
    if start:
        out = [r for r in out if r[0] >= start]
    if len(out) < 30:
        raise RuntimeError("PE 分位序列仅 %d 条（<30），请检查指数代码或拉长起始区间" % len(out))
    return out


def _save(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="fetch_data.py",
        description="回测数据抓取：天天基金累计净值 + 中证指数 PE 52周分位（零依赖）")
    p.add_argument("--fund", help="天天基金代码（如 110020）")
    p.add_argument("--index", help="中证指数代码（如 000300=沪深300；000905=中证500；000922=中证红利）")
    p.add_argument("--nav-out", dest="nav_out", help="净值 JSON 输出路径")
    p.add_argument("--pe-out", dest="pe_out", help="PE 分位 JSON 输出路径")
    p.add_argument("--start", help="起始日期 YYYY-MM-DD（默认净值全历史/PE 取足52周窗口）")
    args = p.parse_args(argv)

    if not args.fund and not args.index:
        p.error("至少提供 --fund 或 --index")
    if args.fund and not args.nav_out:
        p.error("取基金净值需指定 --nav-out")
    if args.index and not args.pe_out:
        p.error("取指数 PE 需指定 --pe-out")

    summary = {"command": "fetch-data"}
    if args.fund:
        navs = fetch_nav(args.fund, args.start)
        _save(args.nav_out, navs)
        summary["nav"] = {"fund": args.fund, "rows": len(navs),
                          "start": navs[0][0], "end": navs[-1][0], "path": args.nav_out}
    if args.index:
        pcts = fetch_index_pe_pct(args.index, args.start)
        _save(args.pe_out, pcts)
        summary["pe_percentile"] = {"index": args.index, "rows": len(pcts),
                                    "start": pcts[0][0], "end": pcts[-1][0],
                                    "latest_pct": pcts[-1][1], "path": args.pe_out}
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
