# fund-smart-dca-monitor · 基金智能定投监控技能包

> 一套给 AI 智能体用的基金定投监控技能：**只算信号、给档位和占比，不给金额建议、不代操作**。
> 所有买卖由投资者本人在自己的基金平台手动执行。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
![No Dependencies](https://img.shields.io/badge/依赖-零-orange.svg)

## 这是什么

一个把「估值 + 趋势 + 超买超卖」三因子量化成**买入信号倍数**的纪律化定投辅助体系，配套**补仓档位**和**三重止盈**规则，并内置一份可直接安装到 AI 编程助手（Claude Code / Trae 等）的 SKILL 包。

设计哲学只有一条：**智能体是参谋，不是操盘手。**

## 核心策略

### 买入信号 = 估值倍数 × 均线倍数 × RSI 修正（封顶 2.0）

| 因子 | 档位 | 倍数 |
|---|---|---|
| PE 近 52 周分位 | <20% | 1.5 |
| | 20–50% | 1.0 |
| | 50–80% | 0.5 |
| | >80% | **0（不买）** |
| 现价 / 250日均线 | <0.95 | 1.2 |
| | 0.95–1.0 | 1.1 |
| | 1.0–1.05 | 0.9 |
| | >1.05 | 0.8 |
| RSI | <30 超卖 | ×1.15 |
| | ≥30 | ×1.0 |

示例：PE 分位 35%（×1.0）、价格在 MA250 下方 12%（×1.2）、RSI6 = 8.7（×1.15）→ 综合信号 **1.38**，可按纪律买入。

### 补仓档位（现价低于成本时）

按浮亏深度分四档，金额以**日均额定额的倍数**表达（与个人预算解耦）：

| 浮亏深度 | 档位（× 日均额） | 示例（月预算 800 → 日均 40 元） |
|---|---|---|
| 0–5% | 5× | 200 元 |
| 5–10% | 7.5× | 300 元 |
| 10–15% | 12.5× | 500 元 |
| >15% | 20× | 800 元 |

> 月度总预算是**所有标的共用**的弹药库，不是每只各一份；分批投放，绝不梭哈。

### 三重止盈

1. **估值止盈**：PE 分位 >85% 或浮盈 >20% → 减 1/3；>95% 或 >30% → 再减 1/3
2. **回撤止盈**：浮盈 >10% 后，从 20 日高点回撤 >8% → 卖出一半
3. **移动止盈**：浮盈 >15% 后，从历史最高点回撤 >8% → 再卖 1/3

## 快速开始

零依赖，Python 3.8+ 直接跑：

```bash
# 买入信号：PE分位 34.8%，现价 1.1138，MA250 1.27，RSI6 8.7
python scripts/fund_strategy.py signal --pe-percentile 34.8 --price 1.1138 --ma250 1.27 --rsi6 8.7

# 补仓档位：浮亏 8.3%，日均额定额 40 元
python scripts/fund_strategy.py dca --drawdown 8.3 --daily-budget 40

# 止盈体检：PE分位 87%，浮盈 21%，距20日高点回撤 9%
python scripts/fund_strategy.py exit-check --pe-percentile 87 --gain 21 --dd-20d 9
```

所有命令支持 `--json` 输出，方便接入自动化流水线。

## 安装为 AI 技能（可选）

把整个仓库放进 AI 助手的技能目录即可（以 Trae CN 为例）：

```bash
git clone https://github.com/w1a1n1g1/fund-smart-dca-monitor.git
# Windows: 复制到 %USERPROFILE%\.trae-cn\skills\fund-smart-dca-monitor
# macOS/Linux: 复制到 ~/.trae/skills/fund-smart-dca-monitor
```

重启助手后，说「帮我算基金定投信号」即可触发。

## 数据源建议（详见 references/data-sources.md）

| 优先级 | 数据源 | 用途 |
|---|---|---|
| 1 | 同花顺 iFinD | 净值/申购状态、近52周 PE 分位、RSI、公告资讯 |
| 2 | 蛋卷基金接口 | 10 年 PE 分位（注意与 52 周口径差异） |
| 3 | 天天基金 API | 历史净值 |

## 目录结构

```
fund-smart-dca-monitor/
├── README.md                    # 本文件
├── SKILL.md                     # AI 技能入口（含触发描述）
├── references/
│   ├── strategy-rules.md        # 完整策略规则与口径说明
│   ├── data-sources.md          # 数据源优先级与取数要点
│   └── daily-report.md          # 四段式监控日报模板
├── scripts/
│   └── fund_strategy.py         # 信号/补仓/止盈计算器（零依赖）
├── docs/
│   └── promo.md                 # 社区分享文案
├── LICENSE
└── .gitignore
```

## 设计原则（铁律）

1. **只算信号、给档位和占比，不给具体金额建议、不代操作**
2. 月度预算是全局弹药库，按档位分批投放
3. 信号 < 1.0 或 PE 分位 > 80% 时，纪律优先：不买
4. 止盈条件触发时按规则机械执行，不跟情绪

## 免责声明

本项目仅为**个人纪律化定投的研究工具与规则框架**，不构成任何投资建议。基金有风险，投资需谨慎；历史分位与均线不代表未来表现。使用本工具产生的一切投资结果由使用者自行承担。

## 支持与分享

如果这套体系对你有帮助：

- ⭐ 给仓库点个 Star，让更多定投人看到
- 🔁 转发给你的盘友，一起纪律化投资
- 📣 想发朋友圈/社群安利？[docs/promo.md](docs/promo.md) 里备好了现成文案

## License

[MIT](LICENSE) © 2026 fund-smart-dca-monitor contributors
