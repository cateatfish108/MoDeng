# coding=utf-8
"""
魔灯（MoDeng）每日海选选股策略 —— 掘金量化（gm SDK）复现版

================================================================================
策略来源
--------------------------------------------------------------------------------
复现 MoDeng 的「每日海选」规则引擎，原始逻辑见：
    Function/SeaSelect/Sub/select_class.py::ExecuteSelectRole / SeaSelect
    Function/SeaSelect/Sub/Sub.py

核心思想
--------------------------------------------------------------------------------
海选是一个「可配置的规则引擎」：把整个股票池逐条规则串行过滤，
每条规则按 priority 优先级执行，最终留下同时满足所有规则的标的。

支持 6 类过滤规则（与魔灯一致）：
    1. MACD 反转   —— 最近 3 根 MACD 中间值最小（V 型底）
    2. SAR 反转    —— SAR 由价上方跌到价下方（向上反转）
    3. RSI 区间    —— RSI 落在 [low, high]
    4. 上市年龄    —— age 在 [low, high]
    5. 当日涨跌幅  —— cp 在 [low, high]
    6. 价格分位    —— close 的 Min-Max 归一化分位在 [low, high]

本复现把海选做成「定期选股 + 等权持有」的可回测策略：
每月第一个交易日海选一次，等权买入选中标的，下次调仓前持有。
================================================================================
"""

import numpy as np
import talib
from gm.api import (
    set_token, run, schedule, history_n, get_symbols, current,
    order_target_percent, get_instrumentinfos,
    MODE_BACKTEST, ADJUST_PREV, PositionSide_Long,
)

# ============================== 用户配置 ====================================
TOKEN = "你的token"
STRATEGY_ID = "你的strategy_id"

# 股票池（示例：沪深300成分；实盘可用 get_history_constituents 取指数成分）
# 这里用 get_symbols 动态获取，也可手工指定
USE_INDEX_POOL = 'SHSE.000300'      # 用沪深300成分作为候选池
MAX_HOLD = 10                        # 最多持有标的数

# ---- 海选规则配置（对应魔灯 json 中的 filter_rule，按 priority 升序执行）----
# kind 可选: macd反转 / sar反转 / rsi / 上市年龄 / 当日涨跌幅 / close_rank
FILTER_RULES = [
    {'priority': 1, 'kind': '当日涨跌幅', 'cp_low': 3.0,  'cp_high': 11.0},
    {'priority': 2, 'kind': 'macd反转',  'k_kind': 'd'},
    {'priority': 3, 'kind': '上市年龄',  'age_low': 4,    'age_high': 100},
    {'priority': 4, 'kind': 'close_rank','amount': 60, 'rank_low': 0, 'rank_high': 50},
]


# ============================ 指标计算（复现魔灯公式）========================
def macd_stray_judge(closes, fast=6, slow=12, signal=9):
    """MACD 反转：最近 3 根 MACD 中间值最小 → V 型底 (select_class.py::macd_stray_judge)"""
    macd, _, _ = talib.MACD(closes, fastperiod=fast, slowperiod=slow, signalperiod=signal)
    macd = macd[~np.isnan(macd)]
    if len(macd) < 3:
        return False
    last3 = macd[-3:]
    return last3[1] == np.min(last3)


def sar_stray_judge(highs, lows, closes):
    """SAR 反转：返回 1 表示向上反转 (select_class.py::sar_stray_judge_sub)"""
    sar = talib.SAR(highs, lows, acceleration=0.05, maximum=0.2)
    if len(sar) < 2:
        return 0
    if (sar[-1] >= closes[-1]) and (sar[-2] <= closes[-2]):
        return -1   # 向下反转
    elif (sar[-1] <= closes[-1]) and (sar[-2] >= closes[-2]):
        return 1    # 向上反转
    return 0


def rsi_judge(closes, span, low, high):
    """RSI 落在区间内 (judge_rsi_sub)"""
    rsi = talib.RSI(closes, timeperiod=span)
    rsi = rsi[~np.isnan(rsi)]
    if len(rsi) == 0:
        return False
    return low <= rsi[-1] <= high


def close_rank(closes, amount):
    """价格分位：最近 amount 根 close 的 Min-Max 归一化分位 (cal_close_rank)"""
    c = closes[-int(amount):]
    rng = np.max(c) - np.min(c)
    if rng == 0:
        return 0.5
    return (c[-1] - np.min(c)) / rng


# ============================== 海选引擎 ====================================
def get_candidate_pool(context):
    """获取候选股票池"""
    try:
        # 用指数成分作为候选池
        from gm.api import get_history_constituents
        cons = get_history_constituents(index=USE_INDEX_POOL, start_date=None, end_date=None)
        if cons:
            return list(cons[0]['constituents'].keys())
    except Exception as e:
        print('取指数成分失败，回退到全市场股票:', e)
    # 回退：取所有 A 股
    syms = get_symbols(sec_type1=1010, sec_type2=101001, exchanges='SHSE,SZSE', skip_suspended=True, skip_st=True)
    return [s['symbol'] for s in syms]


def run_one_rule(context, pool, rule):
    """对股票池应用单条规则，返回过滤后的股票池"""
    kind = rule['kind']
    survived = []

    for sym in pool:
        try:
            his = history_n(symbol=sym, frequency='1d', count=400,
                            fields='close,high,low', adjust=ADJUST_PREV,
                            end_time=context.now, df=False)
            if len(his) < 60:
                continue
            closes = np.array([h['close'] for h in his], dtype=float)
            highs = np.array([h['high'] for h in his], dtype=float)
            lows = np.array([h['low'] for h in his], dtype=float)

            keep = False
            if kind == 'macd反转':
                keep = macd_stray_judge(closes)
            elif kind == 'sar反转':
                keep = (sar_stray_judge(highs, lows, closes) == 1)
            elif kind == 'rsi':
                keep = rsi_judge(closes, int(rule.get('rsi_p', 12)),
                                 float(rule['rsi_low']), float(rule['rsi_high']))
            elif kind == 'close_rank':
                r = close_rank(closes, rule['amount'])
                keep = (rule['rank_low'] / 100 <= r <= rule['rank_high'] / 100)
            elif kind == '当日涨跌幅':
                cp = (closes[-1] - closes[-2]) / closes[-2] * 100
                keep = (float(rule['cp_low']) <= cp <= float(rule['cp_high']))
            elif kind == '上市年龄':
                age = get_stock_age(context, sym)
                keep = (float(rule['age_low']) <= age <= float(rule['age_high']))

            if keep:
                survived.append(sym)
        except Exception as e:
            continue

    print('规则[%s]过滤后剩余 %d 只' % (kind, len(survived)))
    return survived


def get_stock_age(context, sym):
    """上市年龄 = 当前年 - 上市年 (cal_age)"""
    try:
        info = get_instrumentinfos(symbols=sym, df=False)
        if info:
            listed = str(info[0]['listed_date'])[:4]
            return context.now.year - int(listed)
    except Exception:
        pass
    return 0


def sea_select(context):
    """海选主流程：按 priority 串行执行所有规则 (sea_select)"""
    print('======== %s 开始海选 ========' % context.now.strftime('%Y-%m-%d'))
    pool = get_candidate_pool(context)
    print('初始候选池: %d 只' % len(pool))

    # 按 priority 升序执行
    for rule in sorted(FILTER_RULES, key=lambda x: x['priority']):
        pool = run_one_rule(context, pool, rule)
        if len(pool) == 0:
            print('已无符合条件的标的！')
            break

    selected = pool[:MAX_HOLD]
    print('最终选中: %s' % selected)
    return selected


# ============================== 掘金回调 ====================================
def init(context):
    set_token(TOKEN)
    # 每月第一个交易日 09:40 海选并调仓
    schedule(schedule_func=rebalance, date_rule='1m', time_rule='09:40:00')


def rebalance(context):
    selected = sea_select(context)

    # 清掉不在选中名单里的持仓
    positions = context.account().positions()
    held = {p['symbol'] for p in positions}
    for sym in held - set(selected):
        order_target_percent(symbol=sym, percent=0,
                             position_side=PositionSide_Long, order_type=2)

    # 等权买入选中标的
    if selected:
        pct = round(0.95 / len(selected), 3)
        for sym in selected:
            order_target_percent(symbol=sym, percent=pct,
                                 position_side=PositionSide_Long, order_type=2)


def on_backtest_finished(context, indicator):
    print('======== 回测结束 ========')
    print('累计收益率: %.2f%%' % (indicator['pnl_ratio'] * 100))
    print('年化收益率: %.2f%%' % (indicator['pnl_ratio_annual'] * 100))
    print('最大回撤  : %.2f%%' % (indicator['max_drawdown'] * 100))
    print('夏普比率  : %.3f' % indicator['sharp_ratio'])


if __name__ == '__main__':
    set_token(TOKEN)
    run(
        strategy_id=STRATEGY_ID,
        filename='modeng_seaselect_strategy.py',
        mode=MODE_BACKTEST,
        token=TOKEN,
        backtest_start_time='2022-01-01 09:00:00',
        backtest_end_time='2023-12-31 15:00:00',
        backtest_initial_cash=1000000,
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
        backtest_adjust=ADJUST_PREV,
    )
