# coding=utf-8
"""
魔灯（MoDeng）相对大盘强弱策略 —— 掘金量化（gm SDK）复现版

================================================================================
策略来源
--------------------------------------------------------------------------------
复现 MoDeng 的「相对大盘强弱」逻辑，原始代码见：
    SDK/RelativeChangeStrategySub.py

核心思想
--------------------------------------------------------------------------------
衡量个股相对其所属板块（大盘）的超额表现：
    个股日变化率   change_ratio   = (close - open) / open
    板块日变化率   c_change_ratio = (板块close - 板块open) / 板块open
    相对变化差     ratio_diff     = change_ratio - c_change_ratio

ratio_diff > 0 表示个股当日跑赢板块。

板块归类规则（按代码前缀，复现 get_stk_classified_data）：
    300xxx → 创业板(cyb)    002xxx → 中小板(zxb)
    6xxxxx → 沪市(sh)       0xxxxx → 深市(sz)

交易规则（把「相对强弱」做成可回测的动量策略）：
    每日计算各标的近 N 日累计 ratio_diff（相对强弱得分），
    买入得分最高的若干只（持续跑赢板块），等权持有。
================================================================================
"""

import numpy as np
from gm.api import (
    set_token, run, schedule, history_n,
    order_target_percent, MODE_BACKTEST, ADJUST_PREV, PositionSide_Long,
)

# ============================== 用户配置 ====================================
TOKEN = "你的token"
STRATEGY_ID = "你的strategy_id"

SYMBOLS = ['SHSE.600000', 'SZSE.000001', 'SZSE.300508', 'SZSE.002415', 'SHSE.600519']
LOOKBACK = 20           # 计算相对强弱的回看天数
TOP_N = 3               # 买入相对强弱得分最高的 N 只
REBALANCE_FREQ = '1w'   # 调仓频率（每周）

# 板块指数（掘金代码）
BOARD_INDEX = {
    'cyb': 'SZSE.399006',   # 创业板指
    'zxb': 'SZSE.399005',   # 中小板指
    'sh':  'SHSE.000001',   # 上证指数
    'sz':  'SZSE.399001',   # 深证成指
}


# ============================ 核心：板块归类 ================================
def get_board(symbol):
    """按代码前缀判断所属板块，复现 get_stk_classified_data"""
    code = symbol.split('.')[-1]
    if code[:3] == '300':
        return 'cyb'
    elif code[:3] == '002':
        return 'zxb'
    elif code[0] == '6':
        return 'sh'
    elif code[0] == '0':
        return 'sz'
    return 'sh'


def cal_relative_strength(context, symbol):
    """
    计算个股近 LOOKBACK 日相对所属板块的累计超额收益
    复现 cal_relative_ratio: ratio_diff = change_ratio - c_change_ratio
    """
    board = get_board(symbol)
    board_idx = BOARD_INDEX[board]

    stk = history_n(symbol=symbol, frequency='1d', count=LOOKBACK,
                    fields='open,close', adjust=ADJUST_PREV,
                    end_time=context.now, df=False)
    idx = history_n(symbol=board_idx, frequency='1d', count=LOOKBACK,
                    fields='open,close', end_time=context.now, df=False)

    if len(stk) < LOOKBACK or len(idx) < LOOKBACK:
        return None

    total_diff = 0.0
    n = min(len(stk), len(idx))
    for i in range(n):
        if stk[i]['open'] == 0 or idx[i]['open'] == 0:
            continue
        change = (stk[i]['close'] - stk[i]['open']) / stk[i]['open']
        c_change = (idx[i]['close'] - idx[i]['open']) / idx[i]['open']
        total_diff += (change - c_change)   # ratio_diff 累加

    return total_diff


# ============================== 掘金回调 ====================================
def init(context):
    schedule(schedule_func=rebalance, date_rule=REBALANCE_FREQ, time_rule='09:40:00')


def rebalance(context):
    print('======== %s 计算相对强弱 ========' % context.now.strftime('%Y-%m-%d'))
    scores = []
    for sym in SYMBOLS:
        rs = cal_relative_strength(context, sym)
        if rs is not None:
            scores.append((sym, rs))
            print('  %s 相对强弱得分: %.4f' % (sym, rs))

    # 按得分降序，选 TOP_N
    scores.sort(key=lambda x: x[1], reverse=True)
    selected = [s[0] for s in scores[:TOP_N] if s[1] > 0]   # 只买跑赢板块的
    print('选中（跑赢板块）: %s' % selected)

    # 清掉不在名单的持仓
    held = {p['symbol'] for p in context.account().positions()}
    for sym in held - set(selected):
        order_target_percent(symbol=sym, percent=0,
                             position_side=PositionSide_Long, order_type=2)

    # 等权买入
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
        filename='modeng_relative_strength_strategy.py',
        mode=MODE_BACKTEST,
        token=TOKEN,
        backtest_start_time='2022-01-01 09:00:00',
        backtest_end_time='2023-12-31 15:00:00',
        backtest_initial_cash=1000000,
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
        backtest_adjust=ADJUST_PREV,
    )
