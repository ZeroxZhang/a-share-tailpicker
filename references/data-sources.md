# 免费数据获取方式 (v4 多源)

v4 起所有数据访问收敛到 `scripts/databackend.py` 的 `DataBackend` 抽象,策略层不直接接触任何数据源 SDK。

## 数据源优先级

1. **iFinD**(`IFindBackend`)— 当 `iFinDPy` 可导入时自动启用。**运行在 Kimi Code agent 上时优先使用 iFinD**(Kimi Code 原生支持 iFinD 数据),用于快照与 Level-2 资金流等高质量字段。
2. **AKShare**(`AkShareBackend`)— 封装多个免费公开端点,按方法做端点级 fallback:东方财富 `stock_zh_a_hist_min_em` / 新浪 `stock_zh_a_minute` / 雪球热度 `stock_hot_rank_em` 等。
3. **Eastmoney HTTP**(`EastmoneyHttpBackend`)— 直接打 `push2.eastmoney.com/api/qt/clist/get` 拉沪主板快照,当 AKShare 被限流或字段结构变更时兜底。

`CompositeBackend` 按上述顺序逐源尝试,首个非空结果胜出并落盘缓存;每条结果带 `source` 标签可追溯。所有调用经 `DiskCache`(默认 `~/.claude/skills/a-share-tailpicker/cache/`)缓存,回测可复现,实盘在公网接口抖动时仍可跑。

**缓存策略(修复隐患后)**:只有**原始拉取**方法(minute_bars/daily_history/spot_snapshot/all_codes 等)被 `_call` 长缓存;**派生**方法(`daily_prev_close`,组合 daily_history)不缓存,每次重算(走已缓存的原始数据),避免上游源语义变化时(东财含当日 vs 新浪不含当日)固化旧的标量结果。全大盘逐票拉取用 `--workers` 并发(默认按 universe 大小自动 4-16,上限 32)。

## 富化缓存(enrichment)

慢端点(F10/新闻/行业成分)不在 14:20 实盘窗口内调用。09:00 由 `tailpicker.py enrich` 构建 `enrichment_<date>.json`:

| 字段 | 来源 | 用途 |
|---|---|---|
| market(指数尾盘涨跌/涨跌停家数/宽度/state) | index 分钟线 + spot | halt/bear 纪律,仓位系数 |
| sectors(全市场行业涨幅/活跃数/成分) | `stock_board_industry_name_em` + spot | 板块共振(基于全市场,非扫描池) |
| stock_map(name/sector/pe/市值/换手/量比/ST) | spot 快照 | ST 过滤、市值风控、真实名称 |
| news_sentiment(-1/0/+1) | `stock_news_em` 关键词扫描 | 负面新闻扣分,交叉验证 |
| hot_rank | `stock_hot_rank_em` | 情绪交叉验证 |

缺失字段在 bundle 的 `gaps` 里显式记录;策略层据此把 `fundamentals_status` 标为 `missing` 并**封顶 grade 到 B**(见 `score_stock`)。绝不静默 stub 成"通过"。

## 数据完整性规则

- 永不使用决策时点之后的数据。14:20 模拟不得用当日收盘与 14:30-15:00 K 线(未来数据)。
- 14:20 用 14:00-14:20 作为早盘尾盘窗口,结果标记为条件买入。
- 14:45-14:50 执行用 14:30-asof 作为真正的 C 版尾盘窗口。
- **pre_close 用前一交易日日线收盘**(v4 修正),不是首根 5min K 开盘价——否则"当日涨幅"语义错误。
- **量比用标准定义**:今日均每分钟量 / 过去 5 日均每分钟量。日线历史不可得时回退到"尾盘/早盘"代理量,并在 `volume_ratio_source` 标注,阈值需据此重新标定。
- Level-2 买卖盘缺失时,用分钟线 signed amount 估算资金流并降低置信。
- 基本面/新闻/板块缺失不得编造;在 `cross_validation` 记 `missing` 并封顶 grade 到 B。
- 缓存原始响应与生成报告,保证回测可复现。

## 回测成交与成本(v4 保守口径)

- **限价单成交**:触及限价且当根 5min K 收盘价在成交侧才记成交;仅盘中触及但收盘回落不算成交。买入同理(收盘 ≤ 限价才成交,否则视为未成交)。
- **成本**:`佣金(双边,万2.5) + 印花税(卖出,0.05%) + 沪市过户费(0.001%双边) + 滑点(默认 5bp 双边)`。可用 `--cost-slippage-bps` 调整。
- **跳空止损**:开盘即穿过止损线按开盘价成交(非止损线价位),回测如实记录,并在 `gap_exit_stats` 披露频次与损失。

## 最小安装

```bash
python3 -m pip install --user --upgrade akshare pandas numpy
```

iFinD 仅在 Kimi Code 等已集成环境可用,无需手动安装。无任何付费数据源是必需的;付费 Level-2 可提升资金流准确度,但策略在公网数据下通过代理量 + 置信度惩罚仍可运行。
