# 魔灯策略 —— 掘金量化复现

本目录把 MoDeng（魔灯）的策略用[掘金量化](https://www.myquant.cn/)（gm SDK）复现，使其可在掘金平台直接回测/实盘。

## 文件清单

| 文件 | 复现的策略 | 类型 | 原始源码 |
|------|-----------|------|---------|
| `modeng_grid_rsv_strategy.py` | **动态网格交易 + RSV 偏置** | 择时交易 | `StdForReseau`、`cal_rsv_class`、`reseau_judge_class` |
| `modeng_seaselect_strategy.py` | **每日海选选股**（6 规则引擎） | 选股 | `Function/SeaSelect/Sub/select_class.py` |
| `modeng_m20_strategy.py` | **M20 均线穿越** | 择时交易 | `Function/M20/m20_class.py` |
| `modeng_relative_strength_strategy.py` | **相对大盘强弱** | 选股/动量 | `SDK/RelativeChangeStrategySub.py` |
| `modeng_lstm_predict.py` | **LSTM 次日指数预测** | 预测 | `Function/LSTM/.../TomorrowPredict.py` |

> 魔灯全部策略均已复现。下文按文件逐一说明原始逻辑 → 掘金实现的映射。

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

## 二、每日海选选股（`modeng_seaselect_strategy.py`）

把魔灯的规则引擎做成「定期选股 + 等权持有」的可回测策略：每月第一个交易日海选一次。

| 魔灯规则 | 复现函数 | 逻辑 |
|----------|---------|------|
| MACD 反转 | `macd_stray_judge()` | 最近 3 根 MACD 中间值最小（V 型底），fast=6/slow=12/signal=9 |
| SAR 反转 | `sar_stray_judge()` | SAR 由价上方跌到价下方返回 1（向上反转）|
| RSI 区间 | `rsi_judge()` | RSI 落在 [low, high] |
| 上市年龄 | `get_stock_age()` | 当前年 − 上市年 |
| 当日涨跌幅 | `run_one_rule()` | `(close-昨close)/昨close` 落在区间 |
| 价格分位 | `close_rank()` | 近 N 根 close 的 Min-Max 归一化分位 |

规则在 `FILTER_RULES` 里按 `priority` 升序串行执行（与魔灯一致）。候选池默认用沪深300成分。

## 三、M20 均线穿越（`modeng_m20_strategy.py`）

复现 `m_pn / pn_pot` 穿越判断：价格上穿 M20 买入、下穿 M20 卖出。

```
m_N = MA(close, N);  pn = (close - m_N >= 0)
上穿(pn 由 False→True) → 买；下穿(True→False) → 卖
```

## 四、相对大盘强弱（`modeng_relative_strength_strategy.py`）

复现 `ratio_diff = 个股变化率 − 板块变化率`，按代码前缀归类板块（300→创业板/002→中小板/6→沪/0→深），
累计近 N 日 `ratio_diff` 作为相对强弱得分，定期买入得分最高且为正（跑赢板块）的 TOP_N 只。

## 五、LSTM 次日指数预测（`modeng_lstm_predict.py`）

复现对上证/深证/创业板预测次日高/低/收。特征工程完全对齐魔灯（m9 乖离度 + 历史分位 + Min-Max 归一化，N_STEPS=20）。

> ⚠️ 原版基于 TensorFlow 1.x 静态图，本复现改用 **TF2/Keras** `LSTM` 层，逻辑等价、可直接训练运行。数据源改用掘金 `history`。需 `pip install tensorflow scikit-learn`。

## 六、运行步骤

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
