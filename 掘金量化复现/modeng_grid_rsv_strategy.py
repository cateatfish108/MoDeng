# coding=utf-8
"""
魔灯（MoDeng）动态网格交易策略 —— 掘金量化（gm SDK）复现版

================================================================================
策略来源
--------------------------------------------------------------------------------
本文件复现 MoDeng 量化软件的核心盯盘策略「动态网格交易 + RSV 偏置」，
原始逻辑见：
    SDK/StdForReseau/Sub.py::Reseau              （网格大小 = 价格标准差）
    Function/GUI/GUI_main/cal_rsv_class.py::RSV  （RSV 非对称偏置）
    Function/GUI/GUI_main/reseau_judge_class.py  （买卖触发判断）

核心思想
--------------------------------------------------------------------------------
1. 网格大小不固定，由近期价格波动（标准差）动态决定 —— 波动大格子大，波动小格子小。
2. 用 RSV（0~1，价格在近期高低位的相对位置）把网格在买/卖方向上做非对称切分：
       卖出网格 thh_sale = 网格单位 * 2 * RSV
       买入网格 thh_buy  = 网格单位 * 2 * (1 - RSV)
   价格越靠近期高位（RSV 大）→ 越容易卖、越难买；反之越容易买。
3. 价格相对“最低买入价 / 上次操作价”突破对应网格，且幅度不小于 pcr，则买/卖。

================================================================================
运行方式
--------------------------------------------------------------------------------
1. pip install gm           # 安装掘金量化 SDK
2. 在掘金量化终端获取 token，填入下方 TOKEN
3. 填入 strategy_id（在掘金终端「策略管理」中新建策略获得）
4. 直接运行本文件即可启动回测；实盘/仿真把 mode 改为 MODE_LIVE
================================================================================
"""

import numpy as np
from gm.api import (
    set_token, run, subscribe, history_n,
    order_target_percent, order_volume,
    MODE_BACKTEST, MODE_LIVE, OrderSide_Buy, OrderSide_Sell,
    OrderType_Market, PositionSide_Long, ADJUST_PREV,
)

# ============================== 用户配置 ====================================
TOKEN = "你的token"                     # 掘金量化 token
STRATEGY_ID = "你的strategy_id"          # 掘金量化 策略ID

# 交易标的（掘金代码格式：SHSE.xxxxxx / SZSE.xxxxxx）
SYMBOLS = ['SHSE.600000', 'SZSE.000001', 'SZSE.300508']

# 策略参数（对应魔灯原始参数）
QUICK_WIN = 3          # 网格标准差「快窗口」(魔灯 quick=3)
SLOW_WIN = 6           # 网格标准差「慢窗口」(魔灯 slow=6)
RSV_M = 5              # RSV 均线窗口 (魔灯 m=5)
PCR = 0.02            # 最小操作幅度，过滤微小波动 (魔灯 config['pcr']/100)
EACH_ORDER_PCT = 0.2   # 每只标的单次操作占总资金比例（仓位管理）
HISTORY_LEN = 60       # 拉取历史 K 线根数（够算 RSV(400→此处60足够) 和标准差）


# ============================ 核心策略函数 ==================================
def cal_reseau_unit(closes, lows, highs, quick=QUICK_WIN, slow=SLOW_WIN):
    """
    动态网格基准单位 = 快/慢窗口内 [close, low, high] 标准差的均值
    复现 SDK/StdForReseau/Sub.py::Reseau.get_single_stk_reseau_sub
    """
    def win_std(c, l, h, win):
        # 取最后 win 根的 close/low/high 全部数值求标准差
        seg = np.concatenate([c[-win:], l[-win:], h[-win:]])
        return np.std(seg)

    std_q = win_std(closes, lows, highs, quick)
    std_s = win_std(closes, lows, highs, slow)
    return np.mean([std_q, std_s])


def cal_rsv(closes, lows, highs, m=RSV_M):
    """
    RSV（未成熟随机值）= (MA(close,m) - MA(low,m)) / (MA(high,m) - MA(low,m))
    复现 Function/GUI/GUI_main/cal_rsv_class.py::RSV.add_rsv
    无波动时返回 0.5
    """
    close_m = np.mean(closes[-m:])
    low_m = np.mean(lows[-m:])
    high_m = np.mean(highs[-m:])

    if (high_m - low_m == 0) or (close_m - low_m == 0):
        return 0.5
    return (close_m - low_m) / (high_m - low_m)


# ============================== 掘金回调 ====================================
def init(context):
    # 每只标的维护一份操作记录（对应魔灯的 opt_record）
    context.opt = {
        sym: {
            'last_p': None,       # 上次操作价
            'b_p_min': None,      # 历史最低买入价
            'has_flashed': False  # 防重复提示标志（此处用于防重复下单）
        }
        for sym in SYMBOLS
    }
    context.pcr = PCR
    # 订阅日线行情，bar 到达触发 on_bar
    subscribe(symbols=','.join(SYMBOLS), frequency='1d', count=HISTORY_LEN)


def on_bar(context, bars):
    for bar in bars:
        sym = bar['symbol']
        _judge_one(context, sym, bar['close'])


def _judge_one(context, sym, current_price):
    """对单只标的执行网格买卖判断，复现 reseau_judge_class.py::bs_judge"""

    # ---------- 取历史数据 ----------
    his = history_n(symbol=sym, frequency='1d', count=HISTORY_LEN,
                    fields='close,low,high', adjust=ADJUST_PREV,
                    end_time=context.now, df=False)
    if len(his) < SLOW_WIN + 1:
        return

    closes = np.array([h['close'] for h in his])
    lows = np.array([h['low'] for h in his])
    highs = np.array([h['high'] for h in his])

    # ---------- 计算动态网格 ----------
    unit = cal_reseau_unit(closes, lows, highs)
    rsv = cal_rsv(closes, lows, highs)

    thh_sale = unit * 2 * rsv          # 卖出网格
    thh_buy = unit * 2 * (1 - rsv)     # 买入网格

    rec = context.opt[sym]

    # ---------- 首次：无操作记录，用当前价初始化基准，不交易 ----------
    if rec['last_p'] is None or rec['b_p_min'] is None:
        rec['last_p'] = current_price
        rec['b_p_min'] = current_price
        return

    last_p = rec['last_p']
    b_p_min = rec['b_p_min']

    # ---------- 卖出判断 ----------
    # (current_price - b_p_min) > thh_sale 且涨幅比例 >= pcr
    if (current_price - b_p_min > thh_sale) and \
       ((current_price - b_p_min) / b_p_min >= context.pcr):
        _do_sell(context, sym, current_price, thh_sale, thh_buy, rsv)
        rec['last_p'] = current_price
        return

    # ---------- 买入判断 ----------
    # (current_price - last_p) < -thh_buy 且跌幅比例 <= -pcr
    if (current_price - last_p < -thh_buy) and \
       ((current_price - last_p) / b_p_min <= -context.pcr):
        _do_buy(context, sym, current_price, thh_sale, thh_buy, rsv)
        rec['last_p'] = current_price
        # 刷新最低买入价
        rec['b_p_min'] = min(b_p_min, current_price)
        return

    # 否则：未触发任何警戒线，静默


def _do_buy(context, sym, price, thh_sale, thh_buy, rsv):
    """触发买入网格 —— 加仓"""
    # 计算当前持仓占比，逐步加到目标比例
    pos = context.account().position(symbol=sym, side=PositionSide_Long)
    cur_pct = 0.0
    if pos:
        nav = context.account().cash['nav']
        cur_pct = (pos['volume'] * price) / nav if nav else 0.0
    target = min(cur_pct + EACH_ORDER_PCT, 1.0)
    order_target_percent(symbol=sym, percent=target,
                         order_type=OrderType_Market, position_side=PositionSide_Long)
    print('[买入网格] %s 价:%.3f 买网格:%.3f 卖网格:%.3f RSV:%.2f' %
          (sym, price, thh_buy, thh_sale, rsv))


def _do_sell(context, sym, price, thh_sale, thh_buy, rsv):
    """触发卖出网格 —— 减仓"""
    pos = context.account().position(symbol=sym, side=PositionSide_Long)
    if not pos or pos['volume'] <= 0:
        return
    nav = context.account().cash['nav']
    cur_pct = (pos['volume'] * price) / nav if nav else 0.0
    target = max(cur_pct - EACH_ORDER_PCT, 0.0)
    order_target_percent(symbol=sym, percent=target,
                         order_type=OrderType_Market, position_side=PositionSide_Long)
    print('[卖出网格] %s 价:%.3f 买网格:%.3f 卖网格:%.3f RSV:%.2f' %
          (sym, price, thh_buy, thh_sale, rsv))


def on_order_status(context, order):
    """订单状态回调（可选：记录成交）"""
    pass


def on_backtest_finished(context, indicator):
    print('======== 回测结束 ========')
    print('累计收益率: %.2f%%' % (indicator['pnl_ratio'] * 100))
    print('年化收益率: %.2f%%' % (indicator['pnl_ratio_annual'] * 100))
    print('最大回撤  : %.2f%%' % (indicator['max_drawdown'] * 100))
    print('夏普比率  : %.3f' % indicator['sharp_ratio'])


# ================================ 启动 ======================================
if __name__ == '__main__':
    set_token(TOKEN)
    run(
        strategy_id=STRATEGY_ID,
        filename='modeng_grid_rsv_strategy.py',
        mode=MODE_BACKTEST,                 # 回测；实盘改为 MODE_LIVE
        token=TOKEN,
        backtest_start_time='2022-01-01 09:00:00',
        backtest_end_time='2023-12-31 15:00:00',
        backtest_initial_cash=1000000,      # 初始资金 100 万
        backtest_commission_ratio=0.0003,   # 手续费 万3
        backtest_slippage_ratio=0.0001,     # 滑点
        backtest_adjust=ADJUST_PREV,        # 前复权
    )
