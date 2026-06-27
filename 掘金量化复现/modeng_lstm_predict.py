# coding=utf-8
"""
魔灯（MoDeng）LSTM 次日指数预测 —— 掘金量化（gm SDK）数据 + 现代 LSTM 复现版

================================================================================
策略来源
--------------------------------------------------------------------------------
复现 MoDeng 的「LSTM 次日大盘预测」，原始代码见：
    Function/LSTM/AboutLSTM/Test/TomorrowPredict.py
    SDK/LSTM_Class.py（原版基于 TensorFlow 1.x 静态图）

核心思想（与魔灯一致）
--------------------------------------------------------------------------------
对每个指数/标的训练独立的 LSTM 模型，预测「次日的 high / low / close」。

特征工程（复现 predict_tomorrow）:
    m9      = MA(close, 9)
    diff_m9 = (close - m9) / close                      # 乖离度
    rank    = 乖离度在历史中的分位（0~100）
    归一化  : 价格列用历史 p_min/p_max，成交量用 v_min/v_max 做 Min-Max
    时间步  : N_STEPS = 20

差异说明
--------------------------------------------------------------------------------
原版用 TensorFlow 1.x（tf.Session/placeholder），已过时。
本复现改用 TensorFlow 2.x / Keras（tf.keras.LSTM），逻辑等价、更易运行。
数据源由 tushare 改为掘金 history。

依赖: pip install gm tensorflow scikit-learn numpy
================================================================================
"""

import numpy as np
from gm.api import set_token, history

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense
except ImportError:
    print('需要安装 tensorflow: pip install tensorflow')

# ============================== 用户配置 ====================================
TOKEN = "你的token"

# 要预测的指数（复现魔灯的上证/深证/创业板）
INDEX_SYMBOLS = {
    'SHSE.000001': '上证指数',
    'SZSE.399001': '深证成指',
    'SZSE.399006': '创业板指',
}

N_STEPS = 20            # LSTM 时间步（魔灯 N_STEPS=20）
HIDDEN_SIZE = 64        # 隐含层维度（魔灯 HIDDEN_SIZE）
NUM_LAYERS = 2          # LSTM 层数（魔灯 NUM_LAYERS）
EPOCHS = 50
HISTORY_DAYS = 1000     # 训练用历史天数
FEATURE_COLS = ['close', 'high', 'low', 'open', 'volume', 'rank']
LABELS = ['high', 'low', 'close']   # 分别预测次日高/低/收


# ============================ 特征工程（复现魔灯）===========================
def relative_rank(history_arr, value):
    """value 在历史序列中的分位（0~100），复现 SDK/DataPro.relative_rank"""
    history_arr = np.asarray(history_arr)
    if len(history_arr) == 0:
        return 50.0
    return float((history_arr < value).sum()) / len(history_arr) * 100.0


def build_features(df):
    """
    构造特征，复现 predict_tomorrow 的特征工程
    df: 含 open/close/high/low/volume 的 DataFrame（按时间升序）
    返回: 归一化后的特征矩阵, 极值字典
    """
    df = df.copy().reset_index(drop=True)

    # m9 乖离度及其历史分位
    df['m9'] = df['close'].rolling(window=9).mean()
    df['diff_m9'] = (df['close'] - df['m9']) / df['close']
    diff_hist = df['diff_m9'].dropna().values
    df['rank'] = df['diff_m9'].apply(
        lambda x: 100 - relative_rank(diff_hist, x) if not np.isnan(x) else 50.0)

    df = df.dropna().reset_index(drop=True)

    # 记录极值（复现 stk_max_min.json）
    p_min = df[['close', 'high', 'low', 'open']].values.min()
    p_max = df[['close', 'high', 'low', 'open']].values.max()
    v_min, v_max = df['volume'].min(), df['volume'].max()

    # Min-Max 归一化
    for c in ['close', 'high', 'low', 'open']:
        df[c] = (df[c] - p_min) / (p_max - p_min)
    df['volume'] = (df['volume'] - v_min) / (v_max - v_min)
    df['rank'] = df['rank'] / 100.0   # rank 归一到 0~1

    extremes = {'p_min': p_min, 'p_max': p_max, 'v_min': v_min, 'v_max': v_max}
    return df, extremes


def make_sequences(df, label, n_steps=N_STEPS):
    """构造 (X, y) 监督学习样本：用过去 n_steps 天预测次日 label"""
    feats = df[FEATURE_COLS].values
    target = df[label].values
    X, y = [], []
    for i in range(len(df) - n_steps):
        X.append(feats[i:i + n_steps])
        y.append(target[i + n_steps])   # 次日的归一化 label
    return np.array(X), np.array(y)


# ============================== 模型 ========================================
def build_lstm():
    """多层 LSTM，复现 SDK/LSTM_Class.py 的结构（用 Keras 表达）"""
    model = Sequential()
    for i in range(NUM_LAYERS):
        return_seq = (i < NUM_LAYERS - 1)
        if i == 0:
            model.add(LSTM(HIDDEN_SIZE, return_sequences=return_seq,
                           input_shape=(N_STEPS, len(FEATURE_COLS))))
        else:
            model.add(LSTM(HIDDEN_SIZE, return_sequences=return_seq))
    model.add(Dense(1))
    model.compile(optimizer='adam', loss='mse')
    return model


def train_and_predict(symbol, name):
    """对单个标的训练并预测次日高/低/收，复现 predict_tomorrow_index"""
    print('\n======== %s (%s) ========' % (name, symbol))

    # 取历史数据（掘金 history）
    df = history(symbol=symbol, frequency='1d', count=HISTORY_DAYS,
                 fields='open,close,high,low,volume', adjust=1, df=True)
    if df is None or len(df) < N_STEPS + 50:
        print('数据不足，跳过')
        return

    df_feat, ext = build_features(df)

    results = {}
    for label in LABELS:
        X, y = make_sequences(df_feat, label)
        if len(X) < 50:
            continue

        model = build_lstm()
        model.fit(X, y, epochs=EPOCHS, batch_size=32, verbose=0)

        # 用最近 N_STEPS 天预测次日
        last_seq = df_feat[FEATURE_COLS].values[-N_STEPS:][np.newaxis, :, :]
        pred_norm = float(model.predict(last_seq, verbose=0)[0][0])

        # 反归一化：predict = p_min + (p_max - p_min) * pred_norm
        pred = ext['p_min'] + (ext['p_max'] - ext['p_min']) * pred_norm
        results[label] = pred

    print('次日预测  →  最高:%.2f  最低:%.2f  收盘:%.2f' %
          (results.get('high', 0), results.get('low', 0), results.get('close', 0)))
    return results


# ============================== 主流程 ======================================
def main():
    set_token(TOKEN)
    all_pred = {}
    for sym, name in INDEX_SYMBOLS.items():
        all_pred[sym] = train_and_predict(sym, name)
    print('\n======== 全部预测完成 ========')
    return all_pred


if __name__ == '__main__':
    main()
