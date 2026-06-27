# 魔灯策略 —— 掘金量化复现

本目录把 MoDeng（魔灯）的策略用[掘金量化](https://www.myquant.cn/)（gm SDK）复现，使其可在掘金平台直接回测/实盘。

## 文件清单

| 文件 | 复现的策略 | 说明 |
|------|-----------|------|
| `modeng_grid_rsv_strategy.py` | **动态网格交易 + RSV 偏置** | 魔灯最核心的盯盘策略，完整可回测 |

> 海选选股、LSTM 预测如需复现可继续补充。

## 一、动态网格 + RSV 偏置

### 原始逻辑 → 掘金实现映射

| 魔灯原始代码 | 掘金复现函数 | 公式 |
|--------------|--------------|------|
| `StdForReseau/Sub.py::Reseau` | `cal_reseau_unit()` | `unit = mean(std₃, std₆)`，对 close/low/high 求标准差 |
| `cal_rsv_class.py::RSV.add_rsv` | `cal_rsv()` | `RSV = (MA(close,5)-MA(low,5)) / (MA(high,5)-MA(low,5))` |
| `reseau_judge_class.py::cal_reseau` | `_judge_one()` | `thh_sale = unit·2·RSV`，`thh_buy = unit·2·(1-RSV)` |
| `reseau_judge_class.py::bs_judge` | `_judge_one()` | 见下方触发条件 |

### 买卖触发条件（与魔灯完全一致）

**卖出**（同时满足）：
```
(price - b_p_min) > thh_sale  且  (price - b_p_min)/b_p_min >= pcr
```

**买入**（同时满足）：
```
(price - last_p) < -thh_buy   且  (price - last_p)/b_p_min <= -pcr
```

其中 `b_p_min` = 历史最低买入价，`last_p` = 上次操作价，`pcr` = 最小操作幅度。

### 参数对照

| 掘金参数 | 魔灯参数 | 默认值 |
|----------|----------|--------|
| `QUICK_WIN` / `SLOW_WIN` | quick / slow | 3 / 6 |
| `RSV_M` | RSV 的 m | 5 |
| `PCR` | config['pcr']/100 | 0.02 |
| `EACH_ORDER_PCT` | money_each_opt（仓位化） | 0.2 |

### 与原版的差异（适配掘金平台）

1. **数据源**：魔灯用聚宽/tushare，这里改用掘金 `history_n`（前复权）。
2. **下单**：魔灯只「闪动提示」由人工操作，这里改为 `order_target_percent` **自动按比例加减仓**，使其可回测。
3. **触发频率**：魔灯实盘 30 秒轮询实时价，回测用日线 `on_bar`。若要更贴近原版可改订阅 `frequency='60s'` 并在 `on_tick`/分钟线判断。
4. **防重复提示** `has_flashed_flag` 在自动交易里改为仓位约束（达到 0% / 100% 自然停止），保留了字段便于扩展。

## 二、运行步骤

```bash
pip install gm
```

1. 掘金终端获取 `token`、新建策略拿到 `strategy_id`，填入文件顶部。
2. 填写 `SYMBOLS`（掘金代码格式 `SHSE.600000` / `SZSE.000001`）。
3. 运行：
   ```bash
   python modeng_grid_rsv_strategy.py
   ```
4. 回测结束会打印累计收益、年化、最大回撤、夏普比率。
5. 实盘/仿真：把 `run(...)` 里的 `mode` 改为 `MODE_LIVE`。

## 三、调参建议

- **`PCR`**：越大交易越少越稳，越小越频繁。震荡市可调小（0.01），趋势市调大。
- **`QUICK/SLOW_WIN`**：决定网格对波动的敏感度，窗口越短越灵敏。
- **`EACH_ORDER_PCT`**：单次加减仓比例，配合标的数量控制总仓位。
- 网格策略本质适合**震荡行情**，单边趋势中会出现「卖飞 / 越买越亏」，建议配合魔灯海选先筛震荡标的。
