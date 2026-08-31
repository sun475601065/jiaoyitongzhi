# -*- coding: utf-8 -*-
"""
================================================================================
 股票/ETF 趋势回调策略（只做多）· Streamlit 网页版（stock_etf_app.py）
================================================================================
 逻辑来源：stock_etf_scanner.py（命令行版）——数据获取（新浪优先/东方财富备用）、
          指标计算、趋势状态机、入场信号扫描与过滤、持仓监控均与命令行版一致。
 页面功能：
   · 侧边栏：账户总权益、单笔风险比例、是否启用持仓录入
   · 主区域：点击「运行策略扫描」后才开始获取数据与计算（页面加载不自动运行）
   · 结果：股票池信息 / 趋势资格标的列表 / 新开仓信号表 / 持仓监控 / 资金占用与总仓位 / 次日条件单 / 数据获取统计
 资金管理硬约束：
   · 单品种买入金额 ≤ 账户权益×20%（建议股数 = min(风险股数, 单品种上限股数)，向下取整到100股）
   · 总持仓买入金额红线 = 账户权益×30%（考虑T+1隔夜风险），超限红色警告"请减少持仓"
 股票池：全部中证A500成分股（约500只，不做成交额过滤）+ 手动配置14只行业ETF
================================================================================
 部署方法（Streamlit Cloud）：
   1. 将本文件与 requirements.txt 上传到 GitHub 仓库；
   2. 在 Streamlit Cloud（share.streamlit.io）中新建 App，
      选择该仓库，main file path 填写 stock_etf_app.py；
   3. 等待自动部署完成后，即可在网页上使用。
 本地运行：streamlit run stock_etf_app.py
 依赖：pip install streamlit akshare pandas numpy（见 requirements.txt）
================================================================================
"""

import os
import re
import sys
import io
import time
import math
import contextlib
import datetime as dt

import numpy as np
import pandas as pd

try:
    import streamlit as st
except ImportError:
    raise SystemExit("未安装 streamlit，请先执行：pip install streamlit")

try:
    import akshare as ak
except ImportError:
    raise SystemExit("未安装 akshare，请先执行：pip install akshare pandas numpy")

# =============================================================================
# 一、配置（与命令行版一致）
# =============================================================================

# ---- 手动配置的14只行业ETF池（名称 → 代码） ----
ETF_POOL = [
    ("消费ETF",     "159928"),
    ("半导体ETF",   "512480"),
    ("煤炭ETF",     "515220"),
    ("医药ETF",     "512010"),
    ("新能源车ETF", "515030"),
    ("军工ETF",     "512660"),
    ("证券ETF",     "512880"),
    ("银行ETF",     "512800"),
    ("有色金属ETF", "512400"),
    ("化工ETF",     "159870"),
    ("白酒ETF",     "512690"),
    ("农业ETF",     "159825"),
    ("通信ETF",     "515880"),
    ("计算机ETF",   "159998"),
]

# ---- 关联风险组（多品种开仓推荐时，同组只取1个；与命令行版一致） ----
RISK_GROUPS_STOCK = {
    "A500股票": ["STOCK"],                      # 全部A500成分股视为同一关联组
    "消费ETF": ["159928"], "半导体ETF": ["512480"], "煤炭ETF": ["515220"],
    "医药ETF": ["512010"], "新能源车ETF": ["515030"], "军工ETF": ["512660"],
    "证券ETF": ["512880"], "银行ETF": ["512800"], "有色金属ETF": ["512400"],
    "化工ETF": ["159870"], "白酒ETF": ["512690"], "农业ETF": ["159825"],
    "通信ETF": ["515880"], "计算机ETF": ["159998"],
}

A500_INDEX = "000510"                 # 中证A500指数代码
STOCK_STOP_PCT = 0.05                 # 股票默认百分比止损 5%（3%-5%区间内）
ETF_STOP_PCT = 0.03                   # ETF默认百分比止损 3%（3%-5%区间内）
STOP_PCT_OVERRIDE = {}                # 各品种单独配置止损比例，如 {"600519": 0.04, "159928": 0.035}
BE_BUFFER = 0.0015                    # 保本缓冲 0.15%
BE_ARM_MULT = 1.0                     # 浮盈达 1×止损幅度 后启用保本

# ---- 资金管理硬约束（新增） ----
POS_AMOUNT_CAP_PCT = 0.20    # 硬约束1：单品种买入金额上限 = 账户权益×20%
TOTAL_AMOUNT_CAP_PCT = 0.30  # 硬约束2：总持仓买入金额红线 = 账户权益×30%（考虑T+1隔夜风险，超限警告）

RISK_PCT = 0.02                       # 温和趋势：单笔风险占权益 2%（运行时可由侧边栏覆盖）
RISK_PCT_STRONG = 0.01                # 强趋势：半仓，单笔风险 1%
SIGNAL_VALID_DAYS = 5                 # 入场信号有效期5天（信号日=第1天）
SIGNAL_LOOKBACK = 8                   # 最近8个交易日扫描入场信号
TREND_CROSS_LOOKBACK = 15             # 趋势撤销：近15个交易日是否出现EMA25/50交叉
EMA144_FLAT_PCT = 0.005               # EMA144走平：5日变化<0.5%
ADX_N = 14
ADX_MIN = 20.0
ATR_N = 14
EMA_FAST, EMA_MID, EMA_SLOW = 25, 50, 144
KDJ_N, KDJ_M1, KDJ_M2 = 9, 3, 3
MACD_F, MACD_S, MACD_SIG = 12, 26, 9
DATA_MIN_BARS = 170                   # 指标计算所需最少K线数
MONITOR_MIN_BARS = 60                 # 持仓监控所需最少K线数
RETRY_TIMES = 3
RETRY_BACKOFF = (2, 4)                # 第1次重试等2秒、第2次等4秒
HIST_START = "20180101"               # 历史数据起始日（前复权）

# =============================================================================
# 二、指标计算（与 stock_etf_scanner.py 完全一致）
# =============================================================================


def ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def calc_kdj(df, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2):
    """KDJ(9,3,3)：RSV→K→D，J = 3K - 2D"""
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (df["close"] - low_n) / rng * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def calc_macd(df, fast=MACD_F, slow=MACD_S, sig=MACD_SIG):
    """MACD(12,26,9)：DIF、DEA、柱=2×(DIF-DEA)"""
    dif = ema(df["close"], fast) - ema(df["close"], slow)
    dea = dif.ewm(span=sig, adjust=False).mean()
    hist = 2.0 * (dif - dea)
    return dif, dea, hist


def calc_tr(df):
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)


def calc_atr(df, n=ATR_N):
    return calc_tr(df).ewm(alpha=1.0 / n, adjust=False).mean()


def calc_adx(df, n=ADX_N):
    """ADX(14)：Wilder 平滑"""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_s = calc_tr(df).ewm(alpha=1.0 / n, adjust=False).mean()
    pdi = 100.0 * plus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / tr_s
    mdi = 100.0 * minus_dm.ewm(alpha=1.0 / n, adjust=False).mean() / tr_s
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / n, adjust=False).mean()
    return adx.fillna(0.0)


def prepare_df(df):
    """为日线数据补齐全部指标列"""
    df = df.copy()
    df["ema25"] = ema(df["close"], EMA_FAST)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["ema144"] = ema(df["close"], EMA_SLOW)
    k, d, j = calc_kdj(df)
    df["k"], df["d"], df["j"] = k, d, j
    _, _, hist = calc_macd(df)
    df["hist"] = hist
    df["atr"] = calc_atr(df)
    df["adx"] = calc_adx(df)
    return df


# =============================================================================
# 三、数据获取（新浪优先，东方财富备用；3次重试：退避2秒、4秒；与命令行版一致）
# =============================================================================

_STOCK_RENAME = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount",
    "date": "date", "open": "open", "close": "close", "high": "high", "low": "low",
    "volume": "volume", "amount": "amount",
}


def normalize_stock_df(df):
    """统一股票/ETF日线列名与类型，按日期升序"""
    df = df.rename(columns=_STOCK_RENAME)
    for c in ("date", "open", "high", "low", "close"):
        if c not in df.columns:
            raise ValueError("数据缺少列: %s" % c)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def sina_symbol(code):
    """
    股票/ETF代码 → 新浪符号（自动加交易所前缀）：
      沪市股票 6 开头 → "sh"；深市股票 0、3 开头 → "sz"
      沪市ETF  5 开头 → "sh"；深市ETF  1 开头 → "sz"
    """
    if code.startswith(("5", "6")):
        return "sh" + code
    if code.startswith(("0", "1", "3")):
        return "sz" + code
    return code                          # 其他代码原样返回（罕见，如京市）


def _fetch_sina_hist(symbol, adjust, desc=""):
    """新浪股票日线（3次重试，退避2秒、4秒）。返回 (DataFrame, 错误信息)"""
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.stock_zh_a_daily(symbol=symbol, start_date=HIST_START,
                                     end_date="21000118", adjust=adjust)
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return normalize_stock_df(df), ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                print("    %s 新浪第%d次失败（%s），%d秒后重试..."
                      % (desc, attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def _fetch_em_hist(code, desc=""):
    """东方财富股票/ETF前复权日线（3次重试，退避2秒、4秒；作为新浪的备用数据源）。返回 (DataFrame, 错误信息)"""
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                    start_date=HIST_START, end_date="20500101",
                                    adjust="qfq")
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return normalize_stock_df(df), ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                print("    %s 东方财富第%d次失败（%s），%d秒后重试..."
                      % (desc, attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def _fetch_sina_etf_hist(symbol, desc=""):
    """新浪ETF日线 fund_etf_hist_sina（3次重试，退避2秒、4秒；新浪ETF行情无复权因子，为不复权数据）"""
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.fund_etf_hist_sina(symbol=symbol)
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return normalize_stock_df(df), ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                print("    %s 新浪ETF第%d次失败（%s），%d秒后重试..."
                      % (desc, attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def fetch_hist(code, desc=""):
    """
    股票/ETF日线获取（解决东方财富连接失败问题）：
      1) 股票（0/3/6开头）：优先新浪 stock_zh_a_daily（前复权 qfq）
         ETF  （1/5开头）：优先新浪 fund_etf_hist_sina（新浪ETF行情无复权因子，为不复权数据）
      2) 新浪3次重试全部失败后，回退东方财富 stock_zh_a_hist（前复权）
      3) 两者都失败才报错
    返回 (DataFrame, 错误信息)。
    """
    sym = sina_symbol(code)                        # 自动加 sh/sz 前缀
    if code.startswith(("1", "5")):
        # ETF：走新浪ETF专用日线接口
        df, err = _fetch_sina_etf_hist(sym, desc)
        if df is not None:
            return df, ""
        sina_err = "新浪ETF(%s)" % (err or "失败")
    else:
        # 股票：走新浪A股日线接口（前复权）
        df, err = _fetch_sina_hist(sym, "qfq", desc)
        if df is not None:
            return df, ""
        sina_err = "新浪qfq(%s)" % (err or "失败")
    # 回退东方财富
    df2, err2 = _fetch_em_hist(code, desc)
    if df2 is not None:
        return df2, ""
    return None, "%s与东方财富(%s)均失败" % (sina_err, err2 or "失败")


def fetch_index_cons(symbol=A500_INDEX):
    """
    中证指数成分股（A500），3次重试。
    返回 (代码列表, {代码: 名称})；失败返回空。
    """
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.index_stock_cons_csindex(symbol=symbol)
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            code_col = None
            for cand in ("成分券代码", "证券代码", "代码"):
                if cand in df.columns:
                    code_col = cand
                    break
            if code_col is None:
                raise ValueError("未找到成分券代码列")
            codes = [str(x).zfill(6) for x in df[code_col]]
            names = {}
            for cand in ("成分券名称", "证券名称", "名称"):
                if cand in df.columns:
                    names = dict(zip(codes, df[cand].astype(str)))
                    break
            return codes, names
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return [], {}


def _fetch_sina_spot():
    """新浪全A实时行情（3次重试，退避2秒、4秒；接口分页抓取约需30秒）"""
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            # redirect_stderr 屏蔽新浪接口内部 tqdm 进度条刷屏
            with contextlib.redirect_stderr(io.StringIO()):
                df = ak.stock_zh_a_spot()
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return df, ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                print("    新浪全A行情第%d次失败（%s），%d秒后重试..."
                      % (attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def _fetch_spot_em():
    """东方财富全A实时行情（3次重试，退避2秒、4秒；作为新浪的备用数据源）"""
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return df, ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                print("    东方财富全A行情第%d次失败（%s），%d秒后重试..."
                      % (attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def fetch_spot():
    """
    全A实时行情（解决东方财富连接失败问题）：
      1) 优先新浪 stock_zh_a_spot
      2) 失败回退东方财富 stock_zh_a_spot_em
      3) 两者都失败返回 (None, 错误信息, "")，不影响股票池（仍扫描全部A500成分股）
    返回 (DataFrame, 错误信息, 数据来源)；失败时 DataFrame 为 None。
    """
    print("    正在获取全A实时行情（优先新浪，约30秒）...")
    errs = []
    df, err = _fetch_sina_spot()
    if df is not None:
        return df, "", "新浪"
    errs.append("新浪(%s)" % (err or "失败"))
    df2, err2 = _fetch_spot_em()
    if df2 is not None:
        return df2, "", "东方财富"
    errs.append("东方财富(%s)" % (err2 or "失败"))
    return None, "；".join(errs), ""


# =============================================================================
# 四、趋势判定（只做多，状态机；与命令行版一致）
# =============================================================================


def cross_between(a, b, t):
    if t < 1:
        return False
    return (a[t] >= b[t] and a[t - 1] < b[t - 1]) or (a[t] <= b[t] and a[t - 1] > b[t - 1])


def classify_trend_long(df):
    """
    只做多的趋势判定（在个股/ETF自身日线上以"状态机"方式逐日计算）：
    【确认（状态进入）】某一天同时满足：
        EMA25>EMA50>EMA144，价格>EMA144，EMA144向上或走平（e144[t]>=e144[t-1]）
        + ADX连续3天每天>20且逐日上升
      → 当日 trend_ok=1，进入多头维持状态。
    【维持（状态持续）】从确认日的下一天起，只要以下撤销条件全部未触发，
      就保持 trend_ok=1；维持期间不再要求ADX连续3天上升，也不要求EMA144斜率，
      ADX可以下降、走平、甚至短暂低于20，均不影响趋势资格。
    【撤销（满足任一即失效，trend_ok=0，状态机复位后可重新确认）】
      a. EMA25与EMA50出现死叉（收盘确认）
      b. 收盘价跌破EMA144
      c. 近15个交易日内出现过EMA25/50交叉 且 EMA144走平（5日变化<0.5%）
    【趋势类型】仅在 trend_ok=1 的当天判定：
        强趋势 = ADX>20且今日>昨日 + ATR今日>昨日；否则为温和趋势。
    新增列（列名与格式不变）：trend_dir（1=多头/0=无）、trend_type（'强'/'温和'/None）、trend_ok
    """
    n = len(df)
    dirs = np.zeros(n, dtype=int)
    types = [None] * n
    oks = np.zeros(n, dtype=int)
    e25 = df["ema25"].to_numpy(dtype=float)
    e50 = df["ema50"].to_numpy(dtype=float)
    e144 = df["ema144"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    adx = df["adx"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)

    state_on = False    # 状态机：False=未确认（无趋势），True=多头趋势维持中

    for t in range(2, n):
        if not state_on:
            # ================= 状态进入：趋势资格确认 =================
            # 均线多头排列 + 价格>EMA144 + EMA144向上或走平（仅确认日要求斜率）
            if not (e25[t] > e50[t] > e144[t] and close[t] > e144[t]
                    and e144[t] >= e144[t - 1]):
                continue
            # ADX连续3天每天>20且逐日上升
            if t < 3:
                continue
            if not (adx[t - 2] > ADX_MIN and adx[t - 1] > adx[t - 2] and adx[t] > adx[t - 1]):
                continue
            # 确认成功：进入维持状态，当日即生效
            state_on = True
            dirs[t] = 1
            oks[t] = 1
            strong = adx[t] > ADX_MIN and adx[t] > adx[t - 1] and atr[t] > atr[t - 1]
            types[t] = "强" if strong else "温和"
            continue

        # ================= 状态维持：逐条检查撤销条件（任一触发即撤销） =================
        revoke = False

        # a. EMA25与EMA50出现死叉（收盘确认）
        if e25[t] < e50[t] and e25[t - 1] >= e50[t - 1]:
            revoke = True

        # b. 收盘价跌破EMA144
        if not revoke and close[t] < e144[t]:
            revoke = True

        # c. 近15个交易日内出现过EMA25/50交叉 且 EMA144走平（5日变化<0.5%）
        if not revoke:
            crossed = any(cross_between(e25, e50, k)
                          for k in range(max(1, t - TREND_CROSS_LOOKBACK + 1), t + 1))
            if t >= 5 and e144[t - 5] > 0:
                ema144_chg = abs(e144[t] - e144[t - 5]) / e144[t - 5]
            else:
                ema144_chg = 1.0
            if crossed and ema144_chg < EMA144_FLAT_PCT:
                revoke = True

        if revoke:
            # 撤销：trend_ok=0、状态机复位；后续可再次满足确认条件重新进入
            state_on = False
            continue

        # 维持：trend_ok=1；趋势类型按当天的ADX/ATR判定
        dirs[t] = 1
        oks[t] = 1
        strong = adx[t] > ADX_MIN and adx[t] > adx[t - 1] and atr[t] > atr[t - 1]
        types[t] = "强" if strong else "温和"

    df = df.copy()
    df["trend_dir"] = dirs
    df["trend_type"] = types
    df["trend_ok"] = oks
    return df


# =============================================================================
# 五、入场信号扫描（只做多）与信号过滤（与命令行版一致）
# =============================================================================


def scan_signals_long(df, equity, stop_pct):
    """
    扫描最近 SIGNAL_LOOKBACK 个交易日的做多信号，返回信号字典列表。
    股数计算（资金管理硬约束）：
      风险股数   = 权益×风险比例 ÷ (现价×止损%)，向下取整到100股
      单品种上限股数 = 权益×20% ÷ 现价，向下取整到100股（买入金额≤权益×20%）
      建议股数   = min(风险股数, 单品种上限股数)
    """
    n = len(df)
    signals = []
    if n < DATA_MIN_BARS:
        return signals
    j = df["j"].to_numpy(dtype=float)
    hist = df["hist"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    e25 = df["ema25"].to_numpy(dtype=float)
    tdir = df["trend_dir"].to_numpy(dtype=int)
    ttype = list(df["trend_type"])
    dates = df["date"]
    start = max(1, n - SIGNAL_LOOKBACK)
    for t in range(start, n):
        if tdir[t] == 0 or ttype[t] is None:
            continue
        strong = (ttype[t] == "强")
        th = 30 if strong else 20                    # 强趋势 J<30 拐头；温和 J<20 拐头
        if not (j[t - 1] < th and j[t] > j[t - 1]):
            continue
        if not (hist[t] < 0 and hist[t] > hist[t - 1]):   # MACD绿柱缩短
            continue
        if not j[t] < 80:
            continue
        # ---- 5天确认窗口：信号日=第1天，收盘首次站上EMA25确认入场 ----
        confirm = None
        for k in range(t, min(t + SIGNAL_VALID_DAYS, n)):
            if close[k] > e25[k]:
                confirm = k
                break
        if confirm is not None:
            entry = float(close[confirm])
            stop = entry * (1 - stop_pct)
            risk_pct = RISK_PCT_STRONG if strong else RISK_PCT
            per_share_risk = entry * stop_pct
            shares_risk = int(equity * risk_pct / per_share_risk) if per_share_risk > 0 else 0
            shares_risk = shares_risk // 100 * 100              # 风险股数：向下取整到100股
            # 硬约束1：单品种资金上限 —— 买入金额 ≤ 账户权益×20%
            #          上限股数 = 权益×20% ÷ 现价，向下取整到100股
            shares_cap = int(equity * POS_AMOUNT_CAP_PCT / entry) // 100 * 100
            # 硬约束3：建议股数 = min(风险股数, 单品种上限股数)
            shares = min(shares_risk, shares_cap)
            buy_amount = shares * entry
            signals.append({
                "strong": strong, "signal_day": dates.iloc[t],
                "status": "已确认", "confirm_day": dates.iloc[confirm],
                "day_no": confirm - t + 1,
                "entry": entry, "stop": stop, "shares": shares,
                "risk_amt": shares * per_share_risk,
                # 资金管理硬约束相关字段
                "shares_risk": shares_risk,
                "shares_cap": shares_cap,
                "buy_amount": buy_amount,
                "amount_pct": (buy_amount / equity) if equity > 0 else 0.0,
                "capped": shares_cap < shares_risk,
            })
        else:
            status = "待确认" if t + SIGNAL_VALID_DAYS - 1 >= n - 1 else "已失效"
            signals.append({
                "strong": strong, "signal_day": dates.iloc[t],
                "status": status, "day_no": n - t,
                "entry": None, "stop": None, "shares": 0, "risk_amt": 0.0,
                "shares_risk": 0, "shares_cap": 0,
                "buy_amount": 0.0, "amount_pct": 0.0, "capped": False,
            })
    return signals


def filter_recent_signals_long(sigs, df):
    """
    信号显示过滤（与命令行版一致）：
      · 只保留信号日（signal_day）为今日或昨日的信号（以数据最后两根K线为基准）
      · 已确认信号：确认日（confirm_day）早于昨日超过1个交易日的丢弃
      · 待确认信号：只保留信号日 = 今日或昨日
      · 同一品种只保留最新一个信号（避免因不同信号日重复出现）
    """
    if not sigs or df is None or len(df) < 2:
        return []
    today = df["date"].iloc[-1]        # 最新一根K线 = "今天"（数据截止日）
    yesterday = df["date"].iloc[-2]    # 倒数第二根K线 = "昨天"
    out = []
    for s in sigs:
        if s["signal_day"] != today and s["signal_day"] != yesterday:
            continue                   # 历史信号丢弃
        if s["status"] == "已确认":
            cd = s.get("confirm_day")
            if cd is not None and cd < yesterday:
                continue               # 确认日过早的已确认信号丢弃
        out.append(s)
    if not out:
        return []
    # sigs 按信号日升序生成，取最后一条即"最新信号"（同品种一天最多显示一条）
    return [out[-1]]


# =============================================================================
# 多品种开仓推荐（只影响展示，不改变任何交易逻辑；与命令行版一致）
# =============================================================================


def build_recommendations_stock(signal_entries, equity, positions=None):
    """
    今日开仓推荐（多品种同时满足开仓条件时的排序与过滤）：
      排序：① 强趋势信号 > 温和趋势信号
            ② 已确认信号 > 待确认信号
            ③ 信号日更早 > 信号日更晚
      过滤：④ 关联组去重：A500股票全组只选1个（按排序），各行业ETF各成一组互不排除
            ⑤ 资金约束：单品种买入金额 ≤ 权益×20%；总买入金额 ≤ 权益×30%（含已有持仓）；
               单笔风险 ≤ 权益×2%；资金不足开100股时跳过
      说明：待确认信号无入场价，不参与金额过滤（排序天然靠后）。
    返回 (推荐列表, 排除品种列表[关联组去重], 跳过品种列表[资金约束])。
    """
    positions = positions or []
    # 构建ETF代码→组名映射（A500股票统一归入"A500股票"组）
    etf_group_map = {}
    for g, codes in RISK_GROUPS_STOCK.items():
        for code in codes:
            etf_group_map[code] = g

    # ---- 汇总候选信号（信号过滤已保证每个标的最多一条信号） ----
    candidates = []
    for r in signal_entries:
        for sig in r["sigs"]:
            if sig["status"] not in ("已确认", "待确认"):
                continue
            if r["kind"] == "股票":
                group = "A500股票"
            else:
                group = etf_group_map.get(r["code"], r["name"])
            candidates.append({"entry": r, "sig": sig, "group": group})
    # ---- 排序：强>温和；已确认>待确认；信号日早优先 ----
    candidates.sort(key=lambda x: (0 if x["sig"]["strong"] else 1,
                                   0 if x["sig"]["status"] == "已确认" else 1,
                                   x["sig"]["signal_day"]))

    # ---- 总买入金额：已有持仓占用计入30%红线 ----
    total_amount = sum(p["price"] * p["shares"] for p in positions)
    used_groups = {}          # 组名 → 已入选品种名（用于展示排除原因）
    recs, excluded, skipped = [], [], []
    for cand in candidates:
        r, sig = cand["entry"], cand["sig"]
        # ④ 关联组去重：同组只保留排序最靠前的一个
        if cand["group"] in used_groups:
            excluded.append("%s（%s，与%s同组）"
                            % (r["name"], cand["group"], used_groups[cand["group"]]))
            continue
        # ⑤ 资金约束过滤（仅已确认信号可计算金额）
        if sig["status"] == "已确认":
            entry = sig["entry"]
            stop_pct = r["stop_pct"]
            shares_risk = int(equity * RISK_PCT / (entry * stop_pct)) // 100 * 100
            shares_cap = int(equity * 0.20 / entry) // 100 * 100
            shares = min(shares_risk, shares_cap)
            if shares < 100:
                skipped.append("%s（资金不足开100股）" % r["name"])
                continue
            buy_amount = shares * entry
            risk_amt = shares * entry * stop_pct
            if risk_amt > equity * RISK_PCT + 1e-9:
                skipped.append("%s（单笔风险超%.1f%%）" % (r["name"], RISK_PCT * 100))
                continue
            if buy_amount > equity * 0.20 + 1e-9:
                skipped.append("%s（买入金额超20%%红线）" % r["name"])
                continue
            if total_amount + buy_amount > equity * 0.30 + 1e-9:
                skipped.append("%s（总买入金额将超30%%红线）" % r["name"])
                continue
            total_amount += buy_amount
        reason = "%s趋势+%s+%s唯一入选" % (
            "强" if sig["strong"] else "温和", sig["status"], cand["group"])
        recs.append({
            "code": r["code"], "name": r["name"], "kind": r["kind"],
            "trend": "强" if sig["strong"] else "温和",
            "status": sig["status"], "reason": reason,
        })
        used_groups[cand["group"]] = r["name"]
    return recs, excluded, skipped


# =============================================================================
# 六、持仓监控（只做多，按优先级；与命令行版一致）
# =============================================================================


def monitor_position_stock(df, pos):
    """按优先级逐日模拟持仓监控（df 为已含指标与 trend_type 的日线数据）"""
    entry_price = pos["price"]
    entry_date = pd.Timestamp(pos["date"])
    shares = pos["shares"]
    stop_pct = pos["stop_pct"]

    dates = df["date"]
    n = len(df)
    start_i = None
    for i in range(n):
        if dates.iloc[i] > entry_date:
            start_i = i
            break

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    e25 = df["ema25"].to_numpy(dtype=float)
    e50 = df["ema50"].to_numpy(dtype=float)
    ttype = list(df["trend_type"])
    last = n - 1

    hard_stop = entry_price * (1 - stop_pct)      # 百分比止损价
    be_price = entry_price * (1 + BE_BUFFER)      # 保本价（缓冲0.15%）
    be_arm_price = entry_price * (1 + BE_ARM_MULT * stop_pct)   # 浮盈达1×止损幅度后启用保本

    ext_high = entry_price
    be_armed = False
    events = []
    exit_info = None
    days = 0

    if start_i is None:
        return {
            "status": "持有（等待下一交易日数据）", "days": 0,
            "exit_date": None, "exit_price": None, "exit_reason": None,
            "hard_stop": hard_stop, "be_price": be_price, "be_armed": False,
            "last_close": float(close[last]), "last_ema25": float(e25[last]),
            "last_ema50": float(e50[last]), "last_trend_type": ttype[last],
            "pnl": (float(close[last]) - entry_price) * shares, "events": [],
        }

    for i in range(start_i, n):
        days += 1
        ext_high = max(ext_high, high[i])

        # ---------- ① 百分比止损（盘中触发） ----------
        if low[i] <= hard_stop:
            exit_info = (dates.iloc[i], hard_stop, "百分比止损（盘中触发，%.1f%%）" % (stop_pct * 100))
            break

        # ---------- ② 保本止损（盘中触发）：浮盈曾达止损幅度后生效，缓冲0.15% ----------
        if not be_armed and ext_high >= be_arm_price:
            be_armed = True
            events.append("%s 浮盈曾达 %.1f%%，保本止损生效"
                          % (dates.iloc[i].date(), stop_pct * 100))
        if be_armed and low[i] <= be_price:
            exit_info = (dates.iloc[i], be_price, "保本止损（盘中触发）")
            break

        # ---------- ③ 均线死叉（收盘确认，无条件离场） ----------
        if i > 0 and e25[i] < e50[i] and e25[i - 1] >= e50[i - 1]:
            exit_info = (dates.iloc[i], close[i], "EMA25死叉EMA50，无条件离场")
            break

        # ---------- ④ 止盈（收盘确认）：温和→破EMA50；强→破EMA25 ----------
        if ttype[i] == "温和" and close[i] < e50[i]:
            exit_info = (dates.iloc[i], close[i], "温和趋势止盈（收盘跌破EMA50）")
            break
        if ttype[i] == "强" and close[i] < e25[i]:
            exit_info = (dates.iloc[i], close[i], "强趋势止盈（收盘跌破EMA25）")
            break

    return {
        "status": "持有" if exit_info is None else "已离场",
        "days": days,
        "exit_date": exit_info[0] if exit_info else None,
        "exit_price": exit_info[1] if exit_info else None,
        "exit_reason": exit_info[2] if exit_info else None,
        "hard_stop": hard_stop,
        "be_price": be_price,
        "be_armed": be_armed,
        "last_close": float(close[last]),
        "last_ema25": float(e25[last]),
        "last_ema50": float(e50[last]),
        "last_trend_type": ttype[last],
        "pnl": (float(close[last]) - entry_price) * shares,
        "events": events,
    }


# =============================================================================
# 七、展示辅助（格式化文本）
# =============================================================================


def fmt(x, nd=2, dash="--"):
    if x is None:
        return dash
    try:
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return dash
    except Exception:
        pass
    if isinstance(x, float):
        return ("%." + str(nd) + "f") % x
    return str(x)


def nextday_orders_stock(pos, res):
    """为持有中的持仓生成次日条件单文本（与命令行版一致）"""
    L = []
    L.append("  ① 盘中止损：若最低价 ≤ %s（百分比止损 %.1f%%）→ 立即卖出"
             % (fmt(res["hard_stop"]), pos["stop_pct"] * 100))
    if res["be_armed"]:
        L.append("  ② 盘中保本：若最低价 ≤ %s（保本缓冲0.15%%）→ 立即卖出"
                 % fmt(res["be_price"]))
    else:
        L.append("  ② 盘中保本：未生效。最高价需达 %s（浮盈 %.1f%%）后生效，生效价 %s"
                 % (fmt(pos["price"] * (1 + pos["stop_pct"])), pos["stop_pct"] * 100,
                    fmt(res["be_price"])))
    L.append("  ③ 收盘确认：EMA25死叉EMA50（当前EMA25=%s，EMA50=%s）→ 无条件离场"
             % (fmt(res["last_ema25"]), fmt(res["last_ema50"])))
    tt = res["last_trend_type"]
    if tt == "温和":
        L.append("  ④ 收盘确认（温和趋势）：若收盘 < EMA50（%s）→ 离场" % fmt(res["last_ema50"]))
    elif tt == "强":
        L.append("  ④ 收盘确认（强趋势）：若收盘 < EMA25（%s）→ 离场" % fmt(res["last_ema25"]))
    else:
        L.append("  ④ 收盘确认：当前趋势资格未确认，EMA止盈规则暂不适用"
                 "（止损/保本/死叉规则继续有效）")
    return L


def format_position_block_stock(p, res, equity=None):
    """把单个持仓的监控结果格式化为多行文本（用于 st.markdown 展示）"""
    L = []
    L.append("#### 【%s %s】开仓 %s × %d 股，%s，止损 %.1f%%"
             % (p["code"], p["name"], fmt(p["price"]), p["shares"],
                p["date"], p["stop_pct"] * 100))
    if res["status"].startswith("已离场"):
        L.append("- 状态：已离场 | 日期 %s | 价格 %s | 原因：%s"
                 % (res["exit_date"].date(), fmt(res["exit_price"]), res["exit_reason"]))
        L.append("- 持仓天数：%d 天" % res["days"])
    elif res["status"].startswith("数据"):
        L.append("- 状态：%s" % res["status"])
    else:
        L.append("- 状态：%s | 持仓天数：%d 天" % (res["status"], res["days"]))
        L.append("- 最新收盘：%s | 浮动盈亏：%s 元"
                 % (fmt(res["last_close"]), fmt(res["pnl"], 0)))
        L.append("- 当前趋势类型：%s | EMA25=%s | EMA50=%s"
                 % (res["last_trend_type"] or "未确认",
                    fmt(res["last_ema25"]), fmt(res["last_ema50"])))
        L.append("- 止损价：%s | 保本价：%s（%s）"
                 % (fmt(res["hard_stop"]), fmt(res["be_price"]),
                    "已生效" if res["be_armed"] else "未生效"))
    for ev in res.get("events", []):
        L.append("  · %s" % ev)
    # ---- 资金管理硬约束展示：单品种买入金额及占比 / 20%上限 ----
    if equity and equity > 0:
        buy_amt = p["price"] * p["shares"]
        a_pct = buy_amt / equity * 100.0
        L.append("- 单品种买入金额：%s 元（占权益 %.1f%%；单品种上限=权益×20%%=%s 元）"
                 % (fmt(buy_amt, 0), a_pct, fmt(equity * POS_AMOUNT_CAP_PCT, 0)))
        if buy_amt > equity * POS_AMOUNT_CAP_PCT:
            L.append("- ⚠️ 该品种买入金额已触及单品种20%%上限（%.1f%% > 20%%）" % a_pct)
    # 结论行
    if res["status"].startswith("已离场"):
        L.append("**结论：平仓离场（触发原因：%s）**" % res["exit_reason"])
    elif res["status"].startswith("数据"):
        L.append("**结论：数据不足，无法判断（请核对代码与数据获取）**")
    else:
        L.append("**结论：继续持有（等待次日条件单触发离场）**")
    return "\n\n".join(L)


def build_orders_text_stock(pos_pairs):
    """构建次日条件单可复制文本（仅持有中的持仓）"""
    L = []
    L.append("次日条件单（仅持有中的持仓，触发任一条件即卖出）")
    L.append("=" * 60)
    open_cnt = 0
    for p, res in pos_pairs:
        if not res["status"].startswith("已离场") and not res["status"].startswith("数据"):
            open_cnt += 1
            L.append("")
            L.append("【%s %s】" % (p["code"], p["name"]))
            for line in nextday_orders_stock(p, res):
                L.append(line)
            L.append("结论：继续持有（等待次日条件单触发离场）")
    if open_cnt == 0:
        L.append("无持有中的持仓，无需条件单。")
    return "\n".join(L)


def calc_total_amount(pos_pairs):
    """硬约束2：总仓位 —— 所有持仓的买入金额之和（考虑T+1隔夜风险）"""
    total = 0.0
    for p, _ in pos_pairs:
        total += p["price"] * p["shares"]
    return total


def parse_date_text(s):
    """解析日期文本（YYYYMMDD 或 YYYY-MM-DD），失败返回 None"""
    if s is None:
        return None
    s2 = re.sub(r"[-/.]", "", str(s).strip())
    if len(s2) == 8 and s2.isdigit():
        try:
            return dt.date(int(s2[:4]), int(s2[4:6]), int(s2[6:8]))
        except ValueError:
            return None
    return None


def parse_stop_pct_text(s, auto_pct):
    """
    止损百分比文本解析：
      输入 > 1（如 5）    → 自动除以100（得到 0.05）
      输入 <= 1 且 > 0    → 直接使用（如 0.05）
      输入 0 或留空       → 使用自动计算值（股票5% / ETF3%，或 STOP_PCT_OVERRIDE）
    """
    if s is None or str(s).strip() == "":
        return auto_pct
    try:
        v = float(str(s).strip())
    except ValueError:
        return auto_pct
    if v <= 0:
        return auto_pct
    if v > 1:
        v = v / 100.0
    return v


def parse_positions_rows_stock(rows, name_map=None):
    """
    把 st.data_editor 的持仓行解析为持仓字典列表（只做多）。
    名称可留空，运行时自动匹配（name_map 提供名称映射）。
    返回 (positions, 错误提示列表)。
    """
    positions = []
    errors = []
    name_map = name_map or {}
    for idx, row in rows.iterrows():
        code = str(row.get("代码") or "").strip()
        if not code:
            continue                       # 空行跳过
        if not (code.isdigit() and len(code) == 6):
            errors.append("第%d行：代码应为6位数字" % (idx + 1))
            continue
        try:
            price = float(row.get("开仓价"))
        except (TypeError, ValueError):
            errors.append("第%d行：开仓价无效" % (idx + 1))
            continue
        if not math.isfinite(price) or price <= 0:
            errors.append("第%d行：开仓价需大于0" % (idx + 1))
            continue
        try:
            shares = int(row.get("股数"))
        except (TypeError, ValueError):
            errors.append("第%d行：股数无效" % (idx + 1))
            continue
        if shares < 100:
            errors.append("第%d行：股数需>=100（整手）" % (idx + 1))
            continue
        date = parse_date_text(row.get("开仓日期"))
        if date is None:
            errors.append("第%d行：开仓日期无效（应为YYYYMMDD）" % (idx + 1))
            continue
        # 名称：优先用户填写，否则自动匹配
        name = str(row.get("名称") or "").strip() or name_map.get(code, "未知")
        # 止损百分比：留空自动（ETF按3%、股票按5%，或 STOP_PCT_OVERRIDE）
        auto_pct = STOP_PCT_OVERRIDE.get(code, None)
        if auto_pct is None:
            auto_pct = ETF_STOP_PCT if code.startswith(("15", "16", "51", "56", "58")) \
                else STOCK_STOP_PCT
        stop_pct = parse_stop_pct_text(row.get("止损百分比"), auto_pct)
        positions.append({
            "code": code, "name": name, "price": price, "shares": shares,
            "date": date, "stop_pct": stop_pct,
        })
    return positions, errors


# =============================================================================
# 八、页面主体
# =============================================================================


def render_app():
    st.set_page_config(page_title="股票/ETF 趋势回调策略（只做多）", page_icon="📊", layout="wide")
    st.title("股票/ETF 趋势回调策略（只做多）")
    st.caption("股票池：全部中证A500成分股（约500只，不做成交额过滤）+ 14只行业ETF｜"
               "数据：新浪优先、东方财富备用（3次重试，退避2秒、4秒）")

    # ---------- 侧边栏：参数输入 ----------
    with st.sidebar:
        st.header("参数设置")
        equity = st.number_input("账户总权益（元）", value=100000.0, min_value=1000.0,
                                 step=10000.0, format="%.0f")
        risk_pct_input = st.number_input("单笔风险比例（%）", value=2.0, min_value=0.1,
                                         max_value=20.0, step=0.5)
        enable_positions = st.checkbox("启用持仓录入", value=False,
                                       help="勾选后在下方编辑持仓表格（可添加多行）")
        pos_rows = None
        if enable_positions:
            st.markdown("**持仓录入（只做多）**：每行一个持仓；「名称」留空自动匹配；"
                        "「止损百分比」留空=自动（股票5%/ETF3%）")
            pos_rows = st.data_editor(
                pd.DataFrame([{
                    "代码": "", "名称": "", "开仓价": 0.0,
                    "股数": 100, "开仓日期": "", "止损百分比": "",
                }]),
                num_rows="dynamic",
                use_container_width=True,
                key="stock_positions_editor",
                column_config={
                    "代码": st.column_config.TextColumn("代码（6位）"),
                    "名称": st.column_config.TextColumn("名称（可留空）"),
                    "开仓价": st.column_config.NumberColumn("开仓价（元）", min_value=0.0, format="%.2f"),
                    "股数": st.column_config.NumberColumn("股数", min_value=100, step=100, format="%d"),
                    "开仓日期": st.column_config.TextColumn("开仓日期(YYYYMMDD)"),
                    "止损百分比": st.column_config.TextColumn("止损%(留空=自动)"),
                },
            )
        st.divider()
        st.caption("资金管理硬约束：单品种买入金额 ≤ 权益×20%%（当前上限 %.0f 元）；"
                   "总持仓买入金额红线 = 权益×30%%（当前红线 %.0f 元，考虑T+1隔夜风险），"
                   "超限将在报告中红色警告。"
                   % (equity * POS_AMOUNT_CAP_PCT, equity * TOTAL_AMOUNT_CAP_PCT))
        st.caption("点击主区域的「运行策略扫描」按钮开始获取数据与计算；"
                   "页面加载时不会自动运行。扫描全部A500成分股，预计耗时3-5分钟。")

    # ---------- 主区域：运行按钮 ----------
    st.markdown("---")
    if not st.button("🚀 运行策略扫描", type="primary", use_container_width=True):
        st.info("请在侧边栏设置参数（可启用持仓录入），然后点击「运行策略扫描」开始。")
        return

    global RISK_PCT, RISK_PCT_STRONG
    RISK_PCT = risk_pct_input / 100.0
    RISK_PCT_STRONG = RISK_PCT / 2.0          # 强趋势半仓：风险减半

    # ---------- 数据流水线 ----------
    st.info("开始运行：①获取A500成分股与全A行情 → ②构建股票池（全部A500+ETF）→ "
            "③获取日线（约3-5分钟）→ ④信号扫描与持仓监控...")
    fetch_fails = []

    # ① 提前获取A500成分股（股票池 + 名称映射）
    with st.spinner("正在获取中证A500成分股（中证指数官网）..."):
        a500_codes, cons_names = fetch_index_cons()
    spot_names = dict(cons_names)
    for etf_name, etf_code in ETF_POOL:      # ETF名称并入名称映射
        spot_names.setdefault(etf_code, etf_name)

    # 持仓解析（名称随后自动匹配）
    positions = []
    pos_errors = []
    if enable_positions and pos_rows is not None:
        positions, pos_errors = parse_positions_rows_stock(pos_rows, spot_names)
        if pos_errors:
            for e in pos_errors:
                st.warning("持仓录入：" + e)

    # ② 全A实时行情（仅用于名称与参考，不参与筛选）
    st.write("**① 获取全A实时行情（名称与参考，不参与筛选）...**")
    spot_info = {"ok": False, "total": 0, "a500_in_spot": 0, "err": "", "source": ""}
    if a500_codes:
        spot_df, spot_err, spot_src = fetch_spot()
        if spot_df is not None and "代码" in spot_df.columns:
            spot_df = spot_df.copy()
            # 新浪行情代码带交易所前缀（如 sh600519 / sz000001 / bj920000），统一清洗为6位数字代码
            spot_df["代码"] = spot_df["代码"].astype(str) \
                .str.replace(r"[^0-9]", "", regex=True).str.zfill(6)
            if "名称" in spot_df.columns:
                spot_names.update(dict(zip(spot_df["代码"].astype(str),
                                           spot_df["名称"].astype(str))))
            a500_set = set(a500_codes)
            in_spot = spot_df[spot_df["代码"].isin(a500_set)]
            spot_info = {"ok": True, "total": len(spot_df),
                         "a500_in_spot": len(in_spot), "err": "", "source": spot_src}
            st.write("全A行情 %d 只（来源：%s），其中A500成分匹配 %d 只"
                     % (len(spot_df), spot_src, len(in_spot)))
        else:
            spot_info = {"ok": False, "total": 0, "a500_in_spot": 0,
                         "err": spot_err or "返回数据缺少必要列", "source": ""}
            # 实时行情获取失败：不影响股票池，仍扫描全部A500成分股
            fetch_fails.append("实时行情获取失败（%s），不影响股票池，仍扫描全部A500成分股"
                               % spot_info["err"])
    else:
        fetch_fails.append("A500成分股获取失败，仅扫描ETF池")

    # 持仓名称自动匹配（用完整名称映射刷新）
    for p in positions:
        p["name"] = spot_names.get(p["code"], p["name"])

    # ③ 构建股票池：全部A500成分股（不做成交额过滤）+ 14只ETF
    st.write("**② 构建股票池（全部A500成分股 + ETF）...**")
    stock_codes = a500_codes                 # 全部A500成分股，不做成交额排名过滤
    pool = []      # 每项: dict(code, name, kind, stop_pct)
    for code in stock_codes:
        pool.append({
            "code": code, "name": spot_names.get(code, cons_names.get(code, "未知")),
            "kind": "股票",
            "stop_pct": STOP_PCT_OVERRIDE.get(code, STOCK_STOP_PCT),
        })
    for etf_name, etf_code in ETF_POOL:
        pool.append({
            "code": etf_code, "name": etf_name, "kind": "ETF",
            "stop_pct": STOP_PCT_OVERRIDE.get(etf_code, ETF_STOP_PCT),
        })
    pool_info = {"a500_count": len(a500_codes), "etf_count": len(ETF_POOL),
                 "stock_count": len(stock_codes), "scan_total": len(pool)}
    st.write("股票池：全部A500成分股 %d 只 + ETF %d 只 = %d 个标的"
             % (len(stock_codes), len(ETF_POOL), len(pool)))

    # ④ 获取全部日线并计算指标（新浪优先，预计3-5分钟）
    st.write("**③ 获取日线数据（全部A500成分股+ETF，预计耗时3-5分钟；新浪优先，3次重试）...**")
    prepared = {}
    hist_progress = st.progress(0.0)
    for i, item in enumerate(pool, 1):
        desc = "%s%s" % (item["name"], item["code"])
        df, err = fetch_hist(item["code"], desc=desc)
        if df is None or len(df) < DATA_MIN_BARS:
            fetch_fails.append("%s %s（%s）" % (item["name"], item["code"], err or "数据不足"))
        else:
            prepared[item["code"]] = classify_trend_long(prepare_df(df))
        hist_progress.progress(i / len(pool),
                               text="[%d/%d] %s %s" % (i, len(pool), item["name"], item["code"]))
    hist_progress.empty()

    # ⑤ 信号扫描（已持仓品种跳过 + 只显示今日/昨日信号）与持仓监控
    st.write("**④ 扫描入场信号与持仓监控...**")
    held_codes = set(p["code"] for p in positions)
    signals = []
    for item in pool:
        df = prepared.get(item["code"])
        if df is None:
            continue
        sigs = []
        if item["code"] not in held_codes:   # 已持仓品种跳过信号生成
            # 信号过滤：只保留今日/昨日新出现的信号，同品种只保留最新一个
            sigs = filter_recent_signals_long(scan_signals_long(df, equity, item["stop_pct"]), df)
        last_row = df.iloc[-1]
        signals.append({
            "code": item["code"], "name": item["name"], "kind": item["kind"],
            "stop_pct": item["stop_pct"],
            "trend_ok": bool(last_row["trend_ok"]),
            "trend_type": last_row["trend_type"],
            "sigs": sigs,
        })

    pos_pairs = []
    for p in positions:
        df = prepared.get(p["code"])
        if df is None or len(df) < MONITOR_MIN_BARS:
            res = {
                "status": "数据不足，无法监控", "days": 0,
                "exit_date": None, "exit_price": None, "exit_reason": None,
                "hard_stop": p["price"] * (1 - p["stop_pct"]),
                "be_price": p["price"] * (1 + BE_BUFFER), "be_armed": False,
                "last_close": 0.0, "last_ema25": 0.0, "last_ema50": 0.0,
                "last_trend_type": None, "pnl": 0.0, "events": [],
            }
        else:
            res = monitor_position_stock(df, p)
        pos_pairs.append((p, res))

    st.success("扫描完成，正在生成报告...")

    # ================= 结果展示 =================
    st.markdown("---")
    st.subheader("一、股票池信息")
    st.markdown(
        "**中证A500成分股**：%d 只（akshare.index_stock_cons_csindex, %s）\n\n"
        "**全A实时行情**：%s\n\n"
        "**ETF池**：%d 只（手动配置）\n\n"
        "**实际扫描标的**：%d 个（全部A500成分股 %d 只 + ETF %d 只）"
        % (pool_info["a500_count"], A500_INDEX,
           ("%d 只（来源：%s，仅用于名称与参考，不参与筛选）"
            % (spot_info["total"], spot_info.get("source", "新浪/东方财富"))
            if spot_info["ok"] else
            "实时行情获取失败（不影响股票池，仍扫描全部A500成分股）（%s）" % spot_info["err"]),
           pool_info["etf_count"], pool_info["scan_total"],
           pool_info["stock_count"], pool_info["etf_count"])
    )

    # 趋势资格确认标的列表
    st.subheader("二、趋势资格确认标的列表")
    quals = [r for r in signals if r["trend_ok"]]
    if quals:
        st.write("、".join("%s(%s)" % (r["name"], r["trend_type"]) for r in quals))
    else:
        st.write("当前无趋势资格确认的标的（空仓观望）。")

    # 新开仓信号表
    st.subheader("三、新开仓信号表（仅显示今日或昨日新出现的信号；已持仓品种自动跳过）")
    rows = []
    for r in signals:
        for sig in r["sigs"]:
            if sig["status"] == "已确认":
                remark = "信号后第%d天收盘站上EMA25确认" % sig["day_no"]
                # 资金管理硬约束提示：建议股数被单品种20%资金上限约束时注明
                if sig.get("capped"):
                    remark += "；受单品种20%%资金上限约束（风险%d股→上限%d股）" % (
                        sig.get("shares_risk", 0), sig.get("shares_cap", 0))
            elif sig["status"] == "待确认":
                remark = "窗口第%d天，等待收盘站上EMA25" % sig["day_no"]
            else:
                remark = "5天窗口内未确认，已失效"
            rows.append({
                "代码": r["code"],
                "名称": r["name"],
                "类型": r["kind"],
                "趋势": "强" if sig["strong"] else "温和",
                "信号日": str(sig["signal_day"].date()),
                "状态": sig["status"],
                "入场价": fmt(sig["entry"]),
                "止损价": fmt(sig["stop"]),
                "止损%": "%.1f%%" % (r["stop_pct"] * 100),
                "建议股数": fmt(sig["shares"], 0) if sig["shares"] > 0 else "资金不足",
                "风险金额": fmt(sig["risk_amt"], 0),
                "买入金额": fmt(sig.get("buy_amount"), 0),
                "单品种占比": "%.1f%%" % (sig.get("amount_pct", 0.0) * 100),
                "备注": remark,
            })
    rows.sort(key=lambda x: x["信号日"])
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.write("最近%d个交易日内无新开仓信号。" % SIGNAL_LOOKBACK)

    # 多品种开仓推荐（排序+关联组去重+资金约束；只影响展示）
    st.subheader("四、今日开仓推荐（多品种同时满足开仓条件时：强>温和，已确认>待确认，"
                 "信号日早者优先；同组只取1个）")
    recs, excluded, skipped = build_recommendations_stock(signals, equity, positions)
    if recs:
        rec_rows = []
        for i, r in enumerate(recs, 1):
            rec_rows.append({
                "推荐顺序": i,
                "代码": r["code"],
                "名称": r["name"],
                "类型": r["kind"],
                "趋势": r["trend"],
                "信号状态": r["status"],
                "推荐理由": r["reason"],
            })
        st.dataframe(pd.DataFrame(rec_rows), use_container_width=True, hide_index=True)
    else:
        st.write("无（今日无满足条件的开仓信号）")
    for e in excluded:
        st.warning("排除品种（关联组去重）：" + e)
    for s in skipped:
        st.warning("跳过品种（资金约束）：" + s)

    # 持仓监控结果
    st.subheader("五、持仓监控结果")
    if not pos_pairs:
        st.write("当前无持仓录入。")
    for p, res in pos_pairs:
        st.markdown(format_position_block_stock(p, res, equity))
        # 单品种20%上限：超限用红色警告
        buy_amt = p["price"] * p["shares"]
        if equity > 0 and buy_amt > equity * POS_AMOUNT_CAP_PCT:
            st.error("⚠️ %s 单品种买入金额 %.1f%%，已触及单品种20%%上限"
                     % (p["name"], buy_amt / equity * 100))

    # 资金占用与总仓位（硬约束汇总）
    st.subheader("六、资金占用与总仓位（硬约束）")
    total_amount = calc_total_amount(pos_pairs)
    total_pct = total_amount / equity * 100.0 if equity > 0 else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("单品种买入上限（权益×20%）", "%s 元" % fmt(equity * POS_AMOUNT_CAP_PCT, 0))
    c2.metric("总仓位红线（权益×30%）", "%s 元" % fmt(equity * TOTAL_AMOUNT_CAP_PCT, 0))
    c3.metric("总持仓买入金额", "%s 元" % fmt(total_amount, 0), "占权益 %.1f%%" % total_pct)
    if pos_pairs and total_amount > equity * TOTAL_AMOUNT_CAP_PCT:
        # 硬约束2：总仓位超30%红线（T+1隔夜风险）→ 红色警告
        st.error("⚠️ 总持仓买入金额 %s 元，占总权益 %.1f%%，已超过30%%红线，请减少持仓"
                 % (fmt(total_amount, 0), total_pct))
    elif pos_pairs:
        st.success("总持仓买入金额 %s 元，占总权益 %.1f%%，处于30%%红线以内"
                   % (fmt(total_amount, 0), total_pct))
    else:
        st.write("当前无持仓，无总仓位占用。")

    # 次日条件单（可复制文本）
    st.subheader("七、次日条件单（可复制）")
    st.text_area("次日条件单文本", value=build_orders_text_stock(pos_pairs),
                 height=240, label_visibility="collapsed")

    # 数据获取统计
    st.subheader("八、数据获取统计")
    ok_cnt = sum(1 for item in pool if item["code"] in prepared)
    st.markdown(
        "**数据源**：新浪优先（股票 stock_zh_a_daily 前复权qfq；ETF fund_etf_hist_sina 不复权），"
        "东方财富 stock_zh_a_hist 备用；自%s，3次重试（退避2秒、4秒）\n\n"
        "**日线获取**：成功 %d/%d 个\n\n"
        "**扫描账户权益**：%.0f 元；**单笔风险比例**：温和 %.1f%% / 强趋势 %.1f%%"
        % (HIST_START, ok_cnt, len(pool), equity, RISK_PCT * 100, RISK_PCT_STRONG * 100)
    )
    if fetch_fails:
        st.markdown("**获取失败/数据不足**：")
        for f in fetch_fails:
            st.warning(f)
    st.caption("免责声明：本报告由程序自动生成，仅供研究参考，不构成投资建议。")


if __name__ == "__main__":
    render_app()
