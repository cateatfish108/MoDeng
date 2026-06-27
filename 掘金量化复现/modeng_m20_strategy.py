# coding=utf-8
"""
魔灯（MoDeng）M20 均线穿越策略 —— 掘金量化（gm SDK）复现版

================================================================================
策略来源
--------------------------------------------------------------------------------
复现 MoDeng 的 M20 均线监控逻辑，原始代码见：
    Function/M20/m20_class.py::StkAveData / MNote / AverageStatics

核心思想
--------------------------------------------------------------------------------
监控价格相对 N 日均线（默认 M20）的穿越方向：
    m_N      = MA(close, N)
    m_pn_N   = (close - m_N >= 0)            # True=在均线上方
    pn_pot_N = (m_pn_N != m_pn_N.shift(1))   # 状态翻转点 = 穿越点

交易规则（把魔灯的「穿越提示」做成可回测的择时）：
    价格上穿 M20（由下方穿到上方）→ 买入
    价格下穿 M20（由上方穿到下方）→ 卖出
================================================================================
"""

import numpy as np
from gm.api import (
    set_token, run, subscribe, history_n,
    order_target_percent, MODE_BACKTEST, ADJUST_PREV, PositionSide_Long,
)

# ============================== 用户配置 ====================================
TOKEN = "你的token"
STRATEGY_ID = "你的strategy_id"

SYMBOLS = ['SHSE.600000', 'SZSE.000001', 'SZSE.300508']
M = 20                  # 均线周期（魔灯默认 M20）
EACH_PCT = 0.3          # 每只标的目标仓位
HISTORY_LEN = M + 5     # 拉取的 K 线数（够算均线和判断穿越）


# ============================ 核心：穿越判断 ================================
def cross_signal(closes, m=M):
    """
    判断最新一根是否发生穿越，复现 m20_class.py 的 m_pn / pn_pot 逻辑
    返回:  1=上穿(买)   -1=下穿(卖)   0=无穿越
    """
    if len(closes) < m + 2:
        return 0
    ma = np.convolve(closes, np.ones(m) / m, mode='valid')  # 简单移动平均
    # 对齐：ma[-1] 对应 closes[-1]，ma[-2] 对应 closes[-2]
    pn_now = closes[-1] - ma[-1] >= 0       # 当前在均线上方?
    pn_last = closes[-2] - ma[-2] >= 0      # 上一根在均线上方?

    if pn_now and not pn_last:
        return 1     # 由下穿到上 → 涨破
    elif not pn_now and pn_last:
        return -1    # 由上穿到下 → 跌破
    return 0


# ============================== 掘金回调 ====================================
def init(context):
    subscribe(symbols=','.join(SYMBOLS), frequency='1d', count=HISTORY_LEN)


def on_bar(context, bars):
    for bar in bars:
        sym = bar['symbol']
        his = history_n(symbol=sym, frequency='1d', count=HISTORY_LEN,
                        fields='close', adjust=ADJUST_PREV,
                        end_time=context.now, df=False)
        if len(his) < M + 2:
            continue
        closes = np.array([h['close'] for h in his], dtype=float)

        sig = cross_signal(closes, M)
        if sig == 1:
            order_target_percent(symbol=sym, percent=EACH_PCT,
                                 position_side=PositionSide_Long, order_type=2)
            print('[上穿M%d 买入] %s 价:%.3f' % (M, sym, closes[-1]))
        elif sig == -1:
            order_target_percent(symbol=sym, percent=0,
                                 position_side=PositionSide_Long, order_type=2)
            print('[下穿M%d 卖出] %s 价:%.3f' % (M, sym, closes[-1]))


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
        filename='modeng_m20_strategy.py',
        mode=MODE_BACKTEST,
        token=TOKEN,
        backtest_start_time='2022-01-01 09:00:00',
        backtest_end_time='2023-12-31 15:00:00',
        backtest_initial_cash=1000000,
        backtest_commission_ratio=0.0003,
        backtest_slippage_ratio=0.0001,
        backtest_adjust=ADJUST_PREV,
    )
