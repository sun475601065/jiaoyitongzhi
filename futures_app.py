# -*- coding: utf-8 -*-
"""
================================================================================
 国内期货趋势回调策略 · Streamlit 网页版（futures_app.py）
================================================================================
 逻辑来源：futures_interactive.py（命令行版）——数据获取、指标计算、
          趋势状态机、入场信号扫描与过滤、持仓监控、保证金计算均与命令行版一致。
 页面功能：
   · 侧边栏：账户总权益、单笔风险比例、是否手动设置保证金、是否启用持仓录入
   · 主区域：点击「运行策略扫描」后才开始获取数据与计算（页面加载不自动运行）
   · 结果：趋势资格品种列表 / 新开仓信号表 / 持仓监控 / 资金占用与隔夜风险 / 次日条件单 / 数据获取统计
 资金管理硬约束：
   · 单品种保证金占用 ≤ 账户权益×20%（建议手数 = min(风险手数, 保证金上限手数)）
   · 总隔夜保证金占用红线 = 账户权益×30%，超限红色警告"请减少持仓"
================================================================================
 部署方法（Streamlit Cloud）：
   1. 将本文件与 requirements.txt 上传到 GitHub 仓库；
   2. 在 Streamlit Cloud（share.streamlit.io）中新建 App，
      选择该仓库，main file path 填写 futures_app.py；
   3. 等待自动部署完成后，即可在网页上使用。
 本地运行：streamlit run futures_app.py
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
# 一、品种配置（65个品种：中文名、品种代码、备用主力合约、合约乘数、交易所保证金比例）
# -----------------------------------------------------------------------------
# 说明：
#   · margin 字段为"交易所保证金比例"（如 0.07 表示 7%），
#     实际保证金比例 = 交易所比例 + EXTRA_MARGIN（期货公司加收，见下方常量）。
#   · FB/BB（纤维板/胶合板）合约乘数单位为"张"。
#   · 主力合约会随换月变化，启动时自动识别（失败时使用下表备用代码，2026-08 时点）。
#   · 列表已按品种代码字母顺序排列。
# =============================================================================
CONTRACTS = [
    # 中文名          品种     备用主力    合约乘数   保证金比例
    ("豆一",         "A",   "A2611",    10,    0.07),
    ("沪银",         "AG",  "AG2610",   15,    0.08),
    ("沪铝",         "AL",  "AL2610",   5,     0.09),
    ("氧化铝",       "AO",  "AO2610",   20,    0.09),
    ("沪金",         "AU",  "AU2610",   1000,  0.08),
    ("胶合板",       "BB",  "BB2610",   500,   0.10),   # 乘数单位：张
    ("沥青",         "BU",  "BU2610",   10,    0.09),
    ("玉米",         "C",   "C2611",    10,    0.07),
    ("棉花",         "CF",  "CF2701",   5,     0.07),
    ("沪铜",         "CU",  "CU2610",   5,     0.08),
    ("棉纱",         "CY",  "CY2611",   5,     0.08),
    ("苯乙烯",       "EB",  "EB2610",   5,     0.08),
    ("集运欧线",     "EC",  "EC2610",   50,    0.12),
    ("乙二醇",       "EG",  "EG2610",   10,    0.08),
    ("纤维板",       "FB",  "FB2610",   500,   0.10),   # 乘数单位：张
    ("玻璃",         "FG",  "FG2701",   20,    0.09),
    ("燃料油",       "FU",  "FU2611",   10,    0.10),
    ("热卷",         "HC",  "HC2610",   10,    0.07),
    ("铁矿石",       "I",   "I2701",    100,   0.08),
    ("焦炭",         "J",   "J2701",    100,   0.09),
    ("鸡蛋",         "JD",  "JD2610",   5,     0.08),
    ("焦煤",         "JM",  "JM2701",   60,    0.09),
    ("粳稻",         "JR",  "JR2611",   20,    0.06),
    ("塑料",         "L",   "L2701",    5,     0.07),
    ("碳酸锂",       "LC",  "LC2611",   1,     0.12),
    ("生猪",         "LH",  "LH2611",   16,    0.08),
    ("晚籼稻",       "LR",  "LR2611",   20,    0.06),
    ("低硫燃料油",   "LU",  "LU2610",   10,    0.10),
    ("豆粕",         "M",   "M2701",    10,    0.07),
    ("甲醇",         "MA",  "MA2610",   10,    0.08),
    ("沪镍",         "NI",  "NI2610",   1,     0.10),
    ("20号胶",       "NR",  "NR2611",   10,    0.09),
    ("菜油",         "OI",  "OI2611",   10,    0.07),
    ("棕榈油",       "P",   "P2701",    10,    0.08),
    ("沪铅",         "PB",  "PB2610",   5,     0.09),
    ("短纤",         "PF",  "PF2611",   5,     0.08),
    ("液化石油气",   "PG",  "PG2610",   20,    0.08),
    ("普麦",         "PM",  "PM2611",   50,    0.07),
    ("聚丙烯",       "PP",  "PP2701",   5,     0.07),
    ("瓶片",         "PR",  "PR2610",   5,     0.08),
    ("多晶硅",       "PS",  "PS2611",   3,     0.12),
    ("对二甲苯",     "PX",  "PX2610",   5,     0.09),
    ("螺纹钢",       "RB",  "RB2610",   10,    0.07),
    ("早籼稻",       "RI",  "RI2611",   20,    0.06),
    ("菜粕",         "RM",  "RM2611",   10,    0.07),
    ("粳米",         "RR",  "RR2611",   10,    0.06),
    ("油菜籽",       "RS",  "RS2611",   10,    0.08),
    ("橡胶",         "RU",  "RU2701",   10,    0.08),
    ("纯碱",         "SA",  "SA2701",   20,    0.09),
    ("原油",         "SC",  "SC2610",   1000,  0.10),
    ("硅铁",         "SF",  "SF2611",   5,     0.09),
    ("烧碱",         "SH",  "SH2611",   30,    0.09),
    ("工业硅",       "SI",  "SI2611",   5,     0.09),
    ("锰硅",         "SM",  "SM2611",   5,     0.09),
    ("沪锡",         "SN",  "SN2610",   1,     0.10),
    ("纸浆",         "SP",  "SP2611",   10,    0.08),
    ("白糖",         "SR",  "SR2701",   10,    0.07),
    ("不锈钢",       "SS",  "SS2610",   5,     0.08),
    ("PTA",          "TA",  "TA2701",   5,     0.07),
    ("尿素",         "UR",  "UR2701",   20,    0.08),
    ("PVC",          "V",   "V2701",    5,     0.07),
    ("强麦",         "WH",  "WH2611",   20,    0.07),
    ("线材",         "WR",  "WR2610",   10,    0.07),
    ("豆油",         "Y",   "Y2701",    10,    0.07),
    ("沪锌",         "ZN",  "ZN2610",   5,     0.08),
]

AUTO_REFRESH_MAIN = True   # 启动时自动识别最新主力合约（失败则用备用代码）

# ---- 保证金 ----
EXTRA_MARGIN = 0.03        # 期货公司加收保证金比例：实际保证金 = 交易所比例 + 3%

# ---- 资金管理硬约束（新增） ----
POS_MARGIN_CAP_PCT = 0.20    # 硬约束1：单品种保证金占用上限 = 账户权益×20%
TOTAL_MARGIN_CAP_PCT = 0.30  # 硬约束2：总隔夜保证金占用红线 = 账户权益×30%（超限警告）

# ---- 指标与策略参数（与命令行版一致） ----
EMA_FAST, EMA_MID, EMA_SLOW = 25, 50, 144      # 均线周期
KDJ_N, KDJ_M1, KDJ_M2 = 9, 3, 3                # KDJ 参数，J = 3K - 2D
MACD_F, MACD_S, MACD_SIG = 12, 26, 9           # MACD 参数，柱 = 2×(DIF-DEA)
ADX_N = 14                                     # ADX 周期
ADX_MIN = 20.0                                 # 趋势判定 ADX 阈值
ATR_N = 14                                     # ATR 周期
SAR_AF_INIT, SAR_AF_STEP, SAR_AF_MAX = 0.02, 0.02, 0.2   # SAR 加速因子
RISK_PCT = 0.02                                # 温和趋势：单笔风险占权益 2%（运行时可由侧边栏覆盖）
RISK_PCT_STRONG = 0.01                         # 强趋势：半仓，单笔风险 1%
STOP_ATR_MULT = 2.0                            # 硬止损距离 = 2×ATR
BE_ATR_MULT = 1.0                              # 浮盈达 1×ATR 后启用保本
BE_BUFFER = 0.0003                             # 保本缓冲 0.03%
SIGNAL_VALID_DAYS = 5                          # 入场信号有效期5天（信号日=第1天）
SIGNAL_LOOKBACK = 8                            # 最近8个交易日扫描入场信号
TREND_CROSS_LOOKBACK = 15                      # 撤销条件c：近15个交易日是否出现EMA25/50交叉
EMA144_FLAT_PCT = 0.005                        # EMA144走平：5日变化<0.5%
TREND_DULL_DAYS = 30                           # 撤销条件d：确认后连续维持30个交易日（钝化判定）
TREND_DULL_GAP_PCT = 0.005                     # 撤销条件d：EMA25与EMA50间距<0.5%视为均线排列不明显
SAR_HOLD_DAYS = 5                              # SAR接管最低持仓天数
DATA_MIN_BARS = 170                            # 指标计算（含EMA144）所需最少K线数
DATA_STALE_DAYS = 30                           # 连续合约数据超过30天未更新视为无交易（如退市/停牌品种）
MONITOR_MIN_BARS = 60                          # 持仓监控所需最少K线数
RETRY_TIMES = 3                                # 数据获取重试次数
RETRY_BACKOFF = (2, 4)                         # 第1次重试等2秒、第2次等4秒


# =============================================================================
# 二、指标计算（与 futures_interactive.py 完全一致）
# =============================================================================


def ema(series, n):
    """指数移动平均"""
    return series.ewm(span=n, adjust=False).mean()


def calc_kdj(df, n=KDJ_N, m1=KDJ_M1, m2=KDJ_M2):
    """KDJ(9,3,3)：RSV→K→D，J = 3K - 2D"""
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (df["close"] - low_n) / rng * 100.0
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()   # 相当于 SMA(X,3,1)
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
    """真实波幅 TR"""
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)


def calc_atr(df, n=ATR_N):
    """ATR(14)：Wilder 平滑"""
    return calc_tr(df).ewm(alpha=1.0 / n, adjust=False).mean()


def calc_adx(df, n=ADX_N):
    """ADX(14)：Wilder 平滑的 +DI/-DI/DX/ADX"""
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


def calc_sar(df, af_init=SAR_AF_INIT, af_step=SAR_AF_STEP, af_max=SAR_AF_MAX):
    """抛物线转向 SAR：加速因子0.02起、每次创新极值+0.02、最大0.2"""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    sar = np.full(n, np.nan)
    if n == 0:
        return pd.Series(sar, index=df.index)
    trend_up = True
    ep = high[0]          # 极值点
    af = af_init
    sar[0] = low[0]
    for i in range(1, n):
        prev = sar[i - 1]
        if trend_up:
            cur = prev + af * (ep - prev)
            ref_low = low[i - 1] if i < 2 else min(low[i - 1], low[i - 2])
            cur = min(cur, ref_low)            # SAR 不得高于前两根K线低点
            if low[i] < cur:                   # 反转
                trend_up = False
                cur = ep
                ep = low[i]
                af = af_init
            elif high[i] > ep:                 # 创新高：加速
                ep = high[i]
                af = min(af + af_step, af_max)
        else:
            cur = prev + af * (ep - prev)
            ref_high = high[i - 1] if i < 2 else max(high[i - 1], high[i - 2])
            cur = max(cur, ref_high)           # SAR 不得低于前两根K线高点
            if high[i] > cur:                  # 反转
                trend_up = True
                cur = ep
                ep = high[i]
                af = af_init
            elif low[i] < ep:                  # 创新低：加速
                ep = low[i]
                af = min(af + af_step, af_max)
        sar[i] = cur
    return pd.Series(sar, index=df.index)


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
    df["sar"] = calc_sar(df)
    return df


# =============================================================================
# 三、数据获取（新浪优先，3次重试：退避2秒、4秒；与命令行版一致）
# =============================================================================

_FUT_RENAME = {
    "日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low",
    "收盘价": "close", "成交量": "volume", "持仓量": "hold", "动态结算价": "settle",
    "date": "date", "open": "open", "high": "high", "low": "low",
    "close": "close", "volume": "volume", "hold": "hold", "settle": "settle",
}


def normalize_futures_df(df):
    """统一新浪期货日线列名与类型，按日期升序"""
    df = df.rename(columns=_FUT_RENAME)
    for c in ("date", "open", "high", "low", "close"):
        if c not in df.columns:
            raise ValueError("数据缺少列: %s" % c)
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_sina_daily(symbol, desc="", quiet=False):
    """
    新浪期货日线，3次重试（退避2秒、4秒）。
    返回 (DataFrame, 错误信息)；成功时错误信息为空字符串。
    """
    last_err = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            df = ak.futures_zh_daily_sina(symbol=symbol)
            if df is None or len(df) == 0:
                raise ValueError("接口返回空数据")
            return normalize_futures_df(df), ""
        except Exception as e:
            last_err = str(e)
            if attempt < RETRY_TIMES:
                if not quiet:
                    print("    %s 第%d次失败（%s），%d秒后重试..."
                          % (desc, attempt, last_err, RETRY_BACKOFF[attempt - 1]))
                time.sleep(RETRY_BACKOFF[attempt - 1])
    return None, last_err


def refresh_main_codes(verbose=True):
    """
    通过新浪主力合约接口自动识别各品种当前主力合约（新浪格式，直接兼容K线接口）。
    返回 {品种代码(大写): 主力合约代码}，失败品种不包含在内。
    """
    if verbose:
        print("    正在识别主力合约（新浪接口，约需30秒）...")
    main_map = {}
    # shfe 分组已包含能源中心(INE)品种；gfex 为广州期货交易所（工业硅/碳酸锂/多晶硅等）
    for exch in ("shfe", "dce", "czce", "gfex"):
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                text = ak.match_main_contract(symbol=exch)
        except Exception:
            continue
        if not text:
            continue
        for sym in text.split(","):
            sym = sym.strip().upper()
            if not sym:
                continue
            m = re.match(r"^([A-Z]+)", sym)
            if m:
                main_map[m.group(1)] = sym
    return main_map


# =============================================================================
# 四、市场状态 / 趋势资格 / 趋势类型（连续合约，状态机；与命令行版一致）
# =============================================================================


def cross_between(a, b, t):
    """第t根K线是否发生 a 与 b 的金叉/死叉（任意方向）"""
    if t < 1:
        return False
    return (a[t] >= b[t] and a[t - 1] < b[t - 1]) or (a[t] <= b[t] and a[t - 1] > b[t - 1])


def classify_trend(df):
    """
    在（连续合约）数据上以"状态机"方式逐日判定市场状态、趋势资格、趋势类型。
    新增列（列名与格式不变）：
      trend_dir  : 1=多头趋势, -1=空头趋势, 0=无
      trend_type : '强'/'温和'/None（仅 trend_ok=1 的当天有效）
      trend_ok   : 1/0 趋势资格

    状态机逻辑：
    【确认（状态进入）】无趋势状态下，某一天同时满足：
        均线多头/空头排列（EMA25>EMA50>EMA144 / EMA25<EMA50<EMA144）
        + 价格在EMA144正确一侧（多头:收盘>EMA144；空头:收盘<EMA144）
        + EMA144方向要求（多头:向上或走平 e144[t]>=e144[t-1]；空头:向下或走平 e144[t]<=e144[t-1]）
        + ADX连续3天每天>20且逐日上升
      → 当日 trend_ok=1，记录趋势方向，进入维持状态。
    【维持（状态持续）】从确认日的下一天起，只要以下撤销条件全部未触发，
      就保持 trend_ok=1 且趋势方向不变；维持期间不再要求ADX连续3天>20且逐日上升，
      也不要求EMA144斜率持续满足，ADX可以下降、走平、甚至短暂低于20，均不影响趋势资格。
    【撤销（满足任一即失效，trend_ok=0，状态机复位后可重新确认）】
      a. EMA25与EMA50出现反向交叉（多头→死叉，空头→金叉）
      b. 收盘价反向突破EMA144（多头→跌破，空头→升破）
      c. 近15个交易日内出现过EMA25/50交叉 且 EMA144走平（5日变化<0.5%）
      d. 确认后已连续维持30个交易日未触发任何撤销条件，但均线排列已不明显
         （EMA25与EMA50间距<0.5%），视为钝化失效
    【趋势类型】仅在 trend_ok=1 的当天判定：
        强趋势 = ADX>20且今日>昨日 + ATR今日>昨日；否则为温和趋势。
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

    # ---- 状态机变量 ----
    state_dir = 0        # 当前状态：0=无趋势(未确认)，1=多头趋势维持中，-1=空头趋势维持中
    confirm_idx = -1     # 当前趋势的确认日K线位置（用于撤销条件d的30天钝化判定）

    for t in range(2, n):
        if state_dir == 0:
            # ================= 状态进入：趋势资格确认 =================
            # 均线多头/空头排列 + 价格在EMA144正确一侧 + EMA144斜率方向（仅确认日要求）
            bull = (e25[t] > e50[t] > e144[t] and close[t] > e144[t]
                    and e144[t] >= e144[t - 1])       # EMA144向上或走平
            bear = (e25[t] < e50[t] < e144[t] and close[t] < e144[t]
                    and e144[t] <= e144[t - 1])       # EMA144向下或走平
            if not (bull or bear):
                continue
            d = 1 if bull else -1
            # ADX连续3天每天>20且逐日上升
            if t < 3:
                continue
            if not (adx[t - 2] > ADX_MIN and adx[t - 1] > adx[t - 2] and adx[t] > adx[t - 1]):
                continue
            # 确认成功：进入维持状态，当日即生效
            state_dir = d
            confirm_idx = t
            dirs[t] = d
            oks[t] = 1
            strong = adx[t] > ADX_MIN and adx[t] > adx[t - 1] and atr[t] > atr[t - 1]
            types[t] = "强" if strong else "温和"
            continue

        # ================= 状态维持：逐条检查撤销条件（任一触发即撤销） =================
        d = state_dir
        revoke = False

        # a. EMA25与EMA50反向交叉（多头→死叉；空头→金叉）
        if d == 1:
            if e25[t] < e50[t] and e25[t - 1] >= e50[t - 1]:
                revoke = True
        else:
            if e25[t] > e50[t] and e25[t - 1] <= e50[t - 1]:
                revoke = True

        # b. 收盘价反向突破EMA144（多头→跌破；空头→升破）
        if not revoke:
            if (d == 1 and close[t] < e144[t]) or (d == -1 and close[t] > e144[t]):
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

        # d. 确认后连续维持30个交易日未触发任何撤销，但均线排列已不明显
        #    （EMA25与EMA50间距<0.5%）→ 钝化失效（防止长期钝化误判）
        if not revoke:
            hold_days = t - confirm_idx + 1        # 确认日算第1天
            if hold_days >= TREND_DULL_DAYS and e50[t] > 0:
                gap = abs(e25[t] - e50[t]) / e50[t]
                if gap < TREND_DULL_GAP_PCT:
                    revoke = True

        if revoke:
            # 撤销：trend_ok=0、方向归0，状态机复位；后续可再次满足确认条件重新进入
            state_dir = 0
            confirm_idx = -1
            continue

        # 维持：趋势方向不变、trend_ok=1；趋势类型按当天的ADX/ATR判定
        dirs[t] = d
        oks[t] = 1
        strong = adx[t] > ADX_MIN and adx[t] > adx[t - 1] and atr[t] > atr[t - 1]
        types[t] = "强" if strong else "温和"

    df = df.copy()
    df["trend_dir"] = dirs
    df["trend_type"] = types
    df["trend_ok"] = oks
    return df


def merge_trend(main_df, cont_df):
    """把连续合约的趋势方向/类型按日期合并到主力合约K线上（用于信号扫描与持仓监控）"""
    tmap = {}
    for d, dr, tp in zip(cont_df["date"], cont_df["trend_dir"], cont_df["trend_type"]):
        tmap[d] = (dr, tp)
    dirs = np.zeros(len(main_df), dtype=int)
    types = [None] * len(main_df)
    for i, d in enumerate(main_df["date"]):
        if d in tmap:
            dirs[i] = tmap[d][0]
            types[i] = tmap[d][1]
    out = main_df.copy()
    out["trend_dir"] = dirs
    out["trend_type"] = types
    return out


# =============================================================================
# 五、入场信号扫描（主力合约）与信号过滤（与命令行版一致）
# =============================================================================


def scan_signals(df, equity, multiplier, margin_ratio=None):
    """
    扫描最近 SIGNAL_LOOKBACK 个交易日的入场信号，返回信号字典列表。
    margin_ratio：实际保证金比例（交易所比例 + EXTRA_MARGIN），用于单品种保证金约束；
                  为 None 时不启用保证金约束（仅按风险手数）。
    手数计算（资金管理硬约束）：
      风险手数   = 权益×风险比例 ÷ (2×ATR×乘数)
      保证金上限手数 = 权益×20% ÷ (入场价×乘数×实际保证金比例)，向下取整
      建议手数   = min(风险手数, 保证金上限手数)
    """
    n = len(df)
    signals = []
    if n < DATA_MIN_BARS:
        return signals
    j = df["j"].to_numpy(dtype=float)
    hist = df["hist"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    e25 = df["ema25"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    tdir = df["trend_dir"].to_numpy(dtype=int)
    ttype = list(df["trend_type"])
    dates = df["date"]
    start = max(1, n - SIGNAL_LOOKBACK)
    for t in range(start, n):
        if tdir[t] == 0 or ttype[t] is None:
            continue                       # 无趋势资格不产生信号
        strong = (ttype[t] == "强")
        if tdir[t] == 1:
            # 做多：温和 J<20 拐头向上；强 J<30 拐头向上
            th = 30 if strong else 20
            if not (j[t - 1] < th and j[t] > j[t - 1]):
                continue
            if not (hist[t] < 0 and hist[t] > hist[t - 1]):   # MACD绿柱缩短
                continue
            if not j[t] < 80:
                continue
        else:
            # 做空：温和 J>80 拐头向下；强 J>70 拐头向下
            th = 70 if strong else 80
            if not (j[t - 1] > th and j[t] < j[t - 1]):
                continue
            if not (hist[t] > 0 and hist[t] < hist[t - 1]):   # MACD红柱缩短
                continue
            if not j[t] > 20:
                continue
        # ---- 5天确认窗口：信号日=第1天，收盘首次站上/跌破EMA25确认入场 ----
        confirm = None
        for k in range(t, min(t + SIGNAL_VALID_DAYS, n)):
            if tdir[t] == 1 and close[k] > e25[k]:
                confirm = k
                break
            if tdir[t] == -1 and close[k] < e25[k]:
                confirm = k
                break
        if confirm is not None:
            entry = float(close[confirm])
            entry_atr = float(atr[confirm])
            stop = entry - STOP_ATR_MULT * entry_atr if tdir[t] == 1 \
                else entry + STOP_ATR_MULT * entry_atr
            risk_pct = RISK_PCT_STRONG if strong else RISK_PCT
            per_lot_risk = STOP_ATR_MULT * entry_atr * multiplier
            lots_risk = int(equity * risk_pct / per_lot_risk) if per_lot_risk > 0 else 0
            # 硬约束1：单品种保证金约束 —— 每手保证金 = 现价×合约乘数×实际保证金比例
            #          最大可开仓手数（按保证金）= 账户权益×20% ÷ 每手保证金，向下取整
            margin_ratio = margin_ratio if margin_ratio and margin_ratio > 0 else 0.0
            margin_per_lot = entry * multiplier * margin_ratio
            lots_margin = int(equity * POS_MARGIN_CAP_PCT / margin_per_lot) \
                if margin_per_lot > 0 else 0
            # 硬约束3：建议手数 = min(风险手数, 保证金上限手数)
            lots = min(lots_risk, lots_margin)
            signals.append({
                "dir": tdir[t], "strong": strong,
                "signal_day": dates.iloc[t], "signal_idx": t,
                "status": "已确认", "confirm_day": dates.iloc[confirm],
                "day_no": confirm - t + 1,
                "entry": entry, "stop": stop, "atr": entry_atr,
                "lots": lots, "risk_amt": lots * per_lot_risk,
                # 资金管理硬约束相关字段
                "lots_risk": lots_risk,
                "lots_margin": lots_margin,
                "margin_per_lot": margin_per_lot,
                "margin_amt": lots * margin_per_lot,
                "margin_pct": (lots * margin_per_lot / equity) if equity > 0 else 0.0,
                "margin_capped": lots_margin < lots_risk,
            })
        else:
            # 窗口未走完 → 待确认；窗口已过 → 已失效
            status = "待确认" if t + SIGNAL_VALID_DAYS - 1 >= n - 1 else "已失效"
            signals.append({
                "dir": tdir[t], "strong": strong,
                "signal_day": dates.iloc[t], "signal_idx": t,
                "status": status, "day_no": n - t,
                "entry": None, "stop": None, "atr": None,
                "lots": 0, "risk_amt": 0.0,
                "lots_risk": 0, "lots_margin": 0,
                "margin_per_lot": 0.0, "margin_amt": 0.0,
                "margin_pct": 0.0, "margin_capped": False,
            })
    return signals


def filter_recent_signals(sigs, df):
    """
    信号显示过滤：
      · 只保留信号日（signal_day）为今日或昨日的信号（以数据最后两根K线为基准）
      · 已确认信号：确认日（confirm_day）早于昨日超过1个交易日的丢弃
      · 待确认信号：只保留信号日 = 今日或昨日
      · 其余历史信号全部丢弃
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
    return out


# =============================================================================
# 六、持仓监控（主力合约）—— 按优先级逐日回放（与命令行版一致）
# =============================================================================


def sar_status(main, direction, start_i, i):
    """
    SAR接管启动条件（全部满足才接管）：
      a) 持仓>=5天
      b) 持仓期间MACD柱曾连续>=3天同向（任意一段）
      c) 持仓期间ADX曾连续>=3天上升（任意一段，且每天>20）
      d) 当前MACD柱连续两日反向（多：连续两日回落；空：连续两日回升）
    返回各项条件满足情况的字典（ready=是否全部满足）。
    """
    st_ = {
        "days_ok": (i - start_i + 1) >= SAR_HOLD_DAYS,
        "macd_ok": False, "adx_ok": False, "rev_ok": False,
    }
    hist = main["hist"].to_numpy(dtype=float)
    adx = main["adx"].to_numpy(dtype=float)
    # b) MACD柱连续>=3天同向
    run = 1
    for k in range(start_i + 1, i + 1):
        if (hist[k] > 0 and hist[k - 1] > 0) or (hist[k] < 0 and hist[k - 1] < 0):
            run += 1
        else:
            run = 1
        if run >= 3:
            st_["macd_ok"] = True
            break
    # c) ADX连续>=3天上升（任意一段，且每天>20）
    run = 0
    for k in range(start_i + 1, i + 1):
        if adx[k] > adx[k - 1] and adx[k] > ADX_MIN and adx[k - 1] > ADX_MIN:
            run += 1
        else:
            run = 0
        if run >= 2:                     # 连续3天上升 = 2次递增
            st_["adx_ok"] = True
            break
    # d) 当前MACD柱连续两日反向
    if i - 2 >= start_i:
        if direction == 1:
            st_["rev_ok"] = hist[i] < hist[i - 1] and hist[i - 1] < hist[i - 2]
        else:
            st_["rev_ok"] = hist[i] > hist[i - 1] and hist[i - 1] > hist[i - 2]
    st_["ready"] = st_["days_ok"] and st_["macd_ok"] and st_["adx_ok"] and st_["rev_ok"]
    return st_


def monitor_position(main, pos):
    """
    按优先级逐日模拟持仓监控（main 为已含指标与 trend_type 的主力合约数据）。
    监控自入场日的下一个交易日开始；入场日ATR用于硬止损/保本计算。
    返回结果字典。
    """
    direction = pos["direction"]            # 1=多 -1=空
    entry_price = pos["price"]
    entry_date = pd.Timestamp(pos["date"])
    multiplier = pos["multiplier"]
    lots = pos["lots"]

    dates = main["date"]
    n = len(main)
    # 入场日对应K线（≤入场日的最后一根）
    idx = None
    for i in range(n):
        if dates.iloc[i] <= entry_date:
            idx = i
        else:
            break
    # 监控起点：入场日的下一个交易日
    start_i = None
    for i in range(n):
        if dates.iloc[i] > entry_date:
            start_i = i
            break

    high = main["high"].to_numpy(dtype=float)
    low = main["low"].to_numpy(dtype=float)
    close = main["close"].to_numpy(dtype=float)
    e25 = main["ema25"].to_numpy(dtype=float)
    e50 = main["ema50"].to_numpy(dtype=float)
    sar = main["sar"].to_numpy(dtype=float)
    ttype = list(main["trend_type"])
    last = n - 1

    # 入场日ATR（数据缺失时禁用ATR类规则）
    atr_entry = float(main["atr"].iloc[idx]) if idx is not None else 0.0
    if not math.isfinite(atr_entry) or atr_entry <= 0:
        atr_entry = 0.0
    hard_stop = None
    if atr_entry > 0:
        hard_stop = entry_price - STOP_ATR_MULT * atr_entry if direction == 1 \
            else entry_price + STOP_ATR_MULT * atr_entry
    be_price = entry_price * (1 + BE_BUFFER) if direction == 1 \
        else entry_price * (1 - BE_BUFFER)

    ext_high = entry_price        # 持仓期间最高价（多单用）
    ext_low = entry_price         # 持仓期间最低价（空单用）
    be_armed = False
    lock = None                   # 利润锁定线：None=尚未建立（核心修复：不得触发）
    lock_day = None
    sar_active = False
    sar_day = None
    events = []
    exit_info = None
    days = 0

    if start_i is None:
        # 无入场日之后的K线（当日新开仓或数据未更新）
        return {
            "direction": direction, "status": "持有（等待下一交易日数据）",
            "days": 0, "exit_date": None, "exit_price": None, "exit_reason": None,
            "hard_stop": hard_stop, "be_price": be_price, "be_armed": False,
            "lock": None, "lock_day": None, "sar_active": False, "sar_day": None,
            "atr_entry": atr_entry,
            "last_close": float(close[last]), "last_ema25": float(e25[last]),
            "last_ema50": float(e50[last]), "last_sar": float(sar[last]),
            "last_trend_type": ttype[last],
            "pnl": (float(close[last]) - entry_price) * direction * multiplier * lots,
            "sar_progress": None, "events": [],
        }

    for i in range(start_i, n):
        days += 1
        prev_ext_high = ext_high
        prev_ext_low = ext_low
        if direction == 1:
            ext_high = max(ext_high, high[i])
        else:
            ext_low = min(ext_low, low[i])

        # ---------- ① 硬止损（盘中触发）：多 最低<=开仓价-2×ATR；空 最高>=开仓价+2×ATR ----------
        if hard_stop is not None:
            if direction == 1 and low[i] <= hard_stop:
                exit_info = (dates.iloc[i], hard_stop, "硬止损（2×ATR，盘中触发）")
                break
            if direction == -1 and high[i] >= hard_stop:
                exit_info = (dates.iloc[i], hard_stop, "硬止损（2×ATR，盘中触发）")
                break

        # ---------- ② 保本止损（盘中触发）：浮盈曾达1×ATR后生效，保本价=开仓价×(1±0.0003) ----------
        if not be_armed and atr_entry > 0:
            if direction == 1 and ext_high >= entry_price + BE_ATR_MULT * atr_entry:
                be_armed = True
                events.append("%s 浮盈曾达1×ATR，保本止损生效" % dates.iloc[i].date())
            elif direction == -1 and ext_low <= entry_price - BE_ATR_MULT * atr_entry:
                be_armed = True
                events.append("%s 浮盈曾达1×ATR，保本止损生效" % dates.iloc[i].date())
        if be_armed:
            if direction == 1 and low[i] <= be_price:
                exit_info = (dates.iloc[i], be_price, "保本止损（盘中触发）")
                break
            if direction == -1 and high[i] >= be_price:
                exit_info = (dates.iloc[i], be_price, "保本止损（盘中触发）")
                break

        # ---------- ③ 均线死叉/金叉（收盘确认）：EMA25<EMA50(多)/EMA25>EMA50(空)，无条件离场 ----------
        if i > 0:
            if direction == 1 and e25[i] < e50[i] and e25[i - 1] >= e50[i - 1]:
                exit_info = (dates.iloc[i], close[i], "EMA25死叉EMA50，无条件离场")
                break
            if direction == -1 and e25[i] > e50[i] and e25[i - 1] <= e50[i - 1]:
                exit_info = (dates.iloc[i], close[i], "EMA25金叉EMA50，无条件离场")
                break

        # ---------- ④ 温和趋势止盈（收盘确认）：多 收盘跌破EMA50；空 收盘升破EMA50 ----------
        if ttype[i] == "温和":
            if direction == 1 and close[i] < e50[i]:
                exit_info = (dates.iloc[i], close[i], "温和趋势止盈（收盘跌破EMA50）")
                break
            if direction == -1 and close[i] > e50[i]:
                exit_info = (dates.iloc[i], close[i], "温和趋势止盈（收盘升破EMA50）")
                break

        # ---------- ⑤ 利润锁定线（收盘确认，核心修复） ----------
        # 持仓期间首次出现收盘跌破(多)/升破(空)EMA25时，锁定线=当日最低/最高价，当日不离场；
        # 后续收盘跌破(多)/升破(空)锁定线才离场；
        # 若从未出现收盘破EMA25，锁定线不存在（None），不得触发锁定线止盈；
        # 锁定线只上移不下移（多，创新高后上移）/ 只下移不上移（空，创新低后下移）。
        if lock is None:
            if direction == 1 and close[i] < e25[i]:
                lock = float(low[i])
                lock_day = dates.iloc[i]
                events.append("%s 收盘首次跌破EMA25，锁定线=当日最低价 %.2f（当日不离场）"
                              % (lock_day.date(), lock))
            elif direction == -1 and close[i] > e25[i]:
                lock = float(high[i])
                lock_day = dates.iloc[i]
                events.append("%s 收盘首次升破EMA25，锁定线=当日最高价 %.2f（当日不离场）"
                              % (lock_day.date(), lock))
        else:
            if direction == 1:
                if high[i] > prev_ext_high:          # 创新高 → 锁定线上移（不高于当日最低）
                    lock = max(lock, low[i])
                if close[i] < lock:                  # 收盘跌破锁定线 → 离场
                    exit_info = (dates.iloc[i], close[i], "利润锁定线止盈（收盘跌破锁定线）")
                    break
            else:
                if low[i] < prev_ext_low:            # 创新低 → 锁定线下移（不低于当日最高）
                    lock = min(lock, high[i])
                if close[i] > lock:                  # 收盘升破锁定线 → 离场
                    exit_info = (dates.iloc[i], close[i], "利润锁定线止盈（收盘升破锁定线）")
                    break

        # ---------- ⑥ 强趋势止盈（收盘确认，SAR未接管时）：收盘跌破/升破EMA25离场 ----------
        if ttype[i] == "强" and not sar_active:
            if direction == 1 and close[i] < e25[i]:
                exit_info = (dates.iloc[i], close[i], "强趋势止盈（收盘跌破EMA25）")
                break
            if direction == -1 and close[i] > e25[i]:
                exit_info = (dates.iloc[i], close[i], "强趋势止盈（收盘升破EMA25）")
                break

        # ---------- ⑦ SAR接管：条件全部满足后，SAR替代EMA25；收盘穿越SAR且跌破/升破EMA50才离场 ----------
        st_ = sar_status(main, direction, start_i, i)
        if not sar_active and st_["ready"]:
            sar_active = True
            sar_day = dates.iloc[i]
            events.append("%s 满足接管条件，SAR接管止盈（EMA25止盈规则停用）" % sar_day.date())
        if sar_active:
            if direction == 1 and close[i] < sar[i] and close[i] < e50[i]:
                exit_info = (dates.iloc[i], close[i], "SAR止盈（收盘穿越SAR且跌破EMA50）")
                break
            if direction == -1 and close[i] > sar[i] and close[i] > e50[i]:
                exit_info = (dates.iloc[i], close[i], "SAR止盈（收盘穿越SAR且升破EMA50）")
                break

    # ---- 汇总结果 ----
    res = {
        "direction": direction,
        "status": "持有" if exit_info is None else "已离场",
        "days": days,
        "exit_date": exit_info[0] if exit_info else None,
        "exit_price": exit_info[1] if exit_info else None,
        "exit_reason": exit_info[2] if exit_info else None,
        "hard_stop": hard_stop,
        "be_price": be_price,
        "be_armed": be_armed,
        "lock": lock,
        "lock_day": lock_day,
        "sar_active": sar_active,
        "sar_day": sar_day,
        "atr_entry": atr_entry,
        "last_close": float(close[last]),
        "last_ema25": float(e25[last]),
        "last_ema50": float(e50[last]),
        "last_sar": float(sar[last]),
        "last_trend_type": ttype[last],
        "pnl": (float(close[last]) - entry_price) * direction * multiplier * lots,
        "sar_progress": None if exit_info else sar_status(main, direction, start_i, last),
        "events": events,
    }
    return res


# =============================================================================
# 七、展示辅助（格式化文本）
# =============================================================================


def fmt(x, nd=2, dash="--"):
    """安全格式化：None/NaN → 短横线"""
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


def nextday_orders(p, res):
    """为持有中的持仓生成次日条件单文本（与命令行版一致）"""
    L = []
    d = p["direction"]
    if d == 1:
        trig = "最低价 ≤"
        updown = "收盘跌破"
        cross_name = "EMA25死叉EMA50"
    else:
        trig = "最高价 ≥"
        updown = "收盘升破"
        cross_name = "EMA25金叉EMA50"
    # ① 硬止损
    if res["hard_stop"] is not None:
        L.append("  ① 盘中硬止损：若%s %s（开仓价±2×ATR）→ 立即离场"
                 % (trig, fmt(res["hard_stop"])))
    else:
        L.append("  ① 盘中硬止损：ATR数据缺失，本条停用")
    # ② 保本
    if res["be_armed"]:
        L.append("  ② 盘中保本：若%s %s → 立即离场" % (trig, fmt(res["be_price"])))
    else:
        arm = p["price"] + res["atr_entry"] if d == 1 else p["price"] - res["atr_entry"]
        L.append("  ② 盘中保本：未生效。盘中%s价达 %s（浮盈1×ATR）后生效，生效价 %s"
                 % ("最高" if d == 1 else "最低", fmt(arm), fmt(res["be_price"])))
    # ③ 均线交叉
    L.append("  ③ 收盘确认：%s（当前EMA25=%s，EMA50=%s）→ 无条件离场"
             % (cross_name, fmt(res["last_ema25"]), fmt(res["last_ema50"])))
    # ④ / ⑥ / ⑦ 按当前趋势类型
    tt = res["last_trend_type"]
    if tt == "温和":
        L.append("  ④ 收盘确认（温和趋势）：若%s EMA50（%s）→ 离场"
                 % (updown, fmt(res["last_ema50"])))
    elif tt == "强":
        if res["sar_active"]:
            L.append("  ⑥⑦ 收盘确认（强趋势·SAR已接管）：若%s SAR（%s）且%s EMA50（%s）→ 离场"
                     % (updown, fmt(res["last_sar"]), updown, fmt(res["last_ema50"])))
        else:
            L.append("  ⑥ 收盘确认（强趋势·SAR未接管）：若%s EMA25（%s）→ 离场"
                     % (updown, fmt(res["last_ema25"])))
    else:
        L.append("  ④⑥ 收盘确认：当前趋势资格未确认，EMA止盈规则暂不适用"
                 "（止损/保本/死叉规则继续有效）")
    # ⑤ 利润锁定线
    if res["lock"] is not None:
        L.append("  ⑤ 收盘确认（利润锁定线）：若%s 锁定线 %s → 离场（锁定线只%s）"
                 % (updown, fmt(res["lock"]), "上移" if d == 1 else "下移"))
    else:
        L.append("  ⑤ 收盘确认（利润锁定线）：未建立。若收盘首次%s EMA25 → 当日建立锁定线"
                 "（=当日%s价），建立当日不离场；从未破EMA25则永不触发"
                 % (("跌破" if d == 1 else "升破"), "最低" if d == 1 else "最高"))
    # ⑦ SAR接管进度
    if res["sar_active"]:
        L.append("  ⑦ SAR接管：已接管（%s），SAR替代EMA25止盈" % res["sar_day"].date())
    else:
        sp = res.get("sar_progress")
        if sp is None:
            L.append("  ⑦ SAR接管：未接管")
        else:
            L.append("  ⑦ SAR接管进度：持仓≥5天[%s]；MACD柱曾连续3天同向[%s]；"
                     "ADX曾连续3天上升(>20)[%s]；柱连续两日反向[%s]"
                     % ("是" if sp["days_ok"] else "否",
                        "是" if sp["macd_ok"] else "否",
                        "是" if sp["adx_ok"] else "否",
                        "是" if sp["rev_ok"] else "否"))
    return L


def format_position_block(p, res, equity=None):
    """把单个持仓的监控结果格式化为多行文本（用于 st.markdown 展示）"""
    L = []
    d = "多" if p["direction"] == 1 else "空"
    margin_amt = p["price"] * p["multiplier"] * p["lots"] * p["margin"]
    L.append("#### 【%s %s单】开仓 %s × %d 手，%s，估算保证金占用约 %s 元（实际保证金 %.1f%%，%s）"
             % (p["name"], d, fmt(p["price"]), p["lots"], p["date"],
                fmt(margin_amt, 0), p["margin"] * 100, p.get("margin_note", "")))
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
        L.append("- 当前趋势类型：%s | EMA25=%s | EMA50=%s | SAR=%s"
                 % (res["last_trend_type"] or "未确认", fmt(res["last_ema25"]),
                    fmt(res["last_ema50"]), fmt(res["last_sar"])))
        L.append("- 硬止损价：%s | 保本价：%s（%s）"
                 % (fmt(res["hard_stop"]), fmt(res["be_price"]),
                    "已生效" if res["be_armed"] else "未生效"))
        if res["lock"] is not None:
            L.append("- 利润锁定线：%s（%s建立，只%s）"
                     % (fmt(res["lock"]), res["lock_day"].date(),
                        "上移" if p["direction"] == 1 else "下移"))
        else:
            L.append("- 利润锁定线：未建立（持仓期间尚未出现收盘破EMA25，锁定线止盈不触发）")
        L.append("- SAR接管：%s" % ("已接管" if res["sar_active"] else "未接管"))
    for ev in res.get("events", []):
        L.append("  · %s" % ev)
    # ---- 资金管理硬约束展示：单品种保证金占用及占比 / 20%红线 ----
    if equity and equity > 0:
        m_pct = margin_amt / equity * 100.0
        L.append("- 单品种保证金占用：%s 元（占权益 %.1f%%；单品种上限=权益×20%%=%s 元）"
                 % (fmt(margin_amt, 0), m_pct, fmt(equity * POS_MARGIN_CAP_PCT, 0)))
        if margin_amt > equity * POS_MARGIN_CAP_PCT:
            L.append("- ⚠️ 该品种保证金占用已触及单品种20%%红线（%.1f%% > 20%%）" % m_pct)
    # 结论行
    if res["status"].startswith("已离场"):
        L.append("**结论：平仓离场（触发原因：%s）**" % res["exit_reason"])
    elif res["status"].startswith("数据"):
        L.append("**结论：数据不足，无法判断（请核对合约与数据获取）**")
    else:
        L.append("**结论：继续持有（等待次日条件单触发离场）**")
    return "\n\n".join(L)


def build_orders_text(pos_pairs):
    """构建次日条件单可复制文本（仅持有中的持仓）"""
    L = []
    L.append("次日条件单（仅持有中的持仓，触发任一条件即离场）")
    L.append("=" * 60)
    open_cnt = 0
    for p, res in pos_pairs:
        if not res["status"].startswith("已离场") and not res["status"].startswith("数据"):
            open_cnt += 1
            L.append("")
            L.append("【%s %s单】" % (p["name"], "多" if p["direction"] == 1 else "空"))
            for line in nextday_orders(p, res):
                L.append(line)
            L.append("结论：继续持有（等待次日条件单触发离场）")
    if open_cnt == 0:
        L.append("无持有中的持仓，无需条件单。")
    return "\n".join(L)


def calc_total_margin(pos_pairs):
    """硬约束2：总隔夜风险度 —— 所有持仓的保证金占用之和（Σ 每手保证金×手数）"""
    total = 0.0
    for p, _ in pos_pairs:
        total += p["price"] * p["multiplier"] * p["lots"] * p["margin"]
    return total


def find_contract(contracts, s):
    """按中文名/品种代码/主力合约代码查找品种"""
    s = s.strip().upper()
    for c in contracts:
        if c["name"] == s.strip() or c["code"] == s or c["main"].upper() == s:
            return c
    return None


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


def parse_margin_text(s, auto_margin):
    """
    保证金比例文本解析（与命令行版 input_margin 规则一致）：
      输入 > 1（如 10）   → 自动除以100（得到 0.10）
      输入 <= 1 且 > 0    → 直接使用（如 0.10）
      输入 0 或留空       → 使用自动计算值（交易所比例 + EXTRA_MARGIN）
    """
    if s is None or str(s).strip() == "":
        return auto_margin
    try:
        v = float(str(s).strip())
    except ValueError:
        return auto_margin
    if v <= 0:
        return auto_margin
    if v > 1:
        v = v / 100.0
    return v


def build_contracts():
    """由 CONTRACTS 构建品种字典列表"""
    contracts = []
    for name, code, main, mult, margin in CONTRACTS:
        contracts.append({
            "name": name, "code": code.upper(), "main": main.upper(),
            "mult": mult,
            "margin": margin,                      # 交易所保证金比例
            "margin_actual": margin + EXTRA_MARGIN,  # 实际保证金比例（交易所+期货公司加收）
        })
    return contracts


def parse_positions_rows(rows, contracts, manual_margin):
    """
    把 st.data_editor 的持仓行解析为持仓字典列表。
    返回 (positions, 错误提示列表)。
    """
    positions = []
    errors = []
    for idx, row in rows.iterrows():
        name_or_code = str(row.get("品种") or "").strip()
        if not name_or_code:
            continue                       # 空行跳过
        c = find_contract(contracts, name_or_code)
        if c is None:
            errors.append("第%d行：未找到品种「%s」" % (idx + 1, name_or_code))
            continue
        direction_text = str(row.get("方向") or "做多").strip()
        direction = 1 if direction_text in ("做多", "多", "1", "long") else -1
        try:
            price = float(row.get("开仓价"))
        except (TypeError, ValueError):
            errors.append("第%d行：开仓价无效" % (idx + 1))
            continue
        if not math.isfinite(price) or price <= 0:
            errors.append("第%d行：开仓价需大于0" % (idx + 1))
            continue
        try:
            lots = int(row.get("手数"))
        except (TypeError, ValueError):
            errors.append("第%d行：手数无效" % (idx + 1))
            continue
        if lots < 1:
            errors.append("第%d行：手数需>=1" % (idx + 1))
            continue
        date = parse_date_text(row.get("开仓日期"))
        if date is None:
            errors.append("第%d行：开仓日期无效（应为YYYYMMDD）" % (idx + 1))
            continue
        # 保证金：手动模式下读行内值，否则自动
        margin_auto = c["margin_actual"]
        margin_note = "交易所 %.1f%% + %.1f%%（自动）" % (c["margin"] * 100, EXTRA_MARGIN * 100)
        if manual_margin:
            margin = parse_margin_text(row.get("保证金比例"), margin_auto)
            if abs(margin - margin_auto) < 1e-9:
                margin_note = "交易所 %.1f%% + %.1f%%（自动）" % (c["margin"] * 100, EXTRA_MARGIN * 100)
            else:
                margin_note = "手动设定 %.1f%%" % (margin * 100)
        else:
            margin = margin_auto
        positions.append({
            "name": c["name"], "code": c["code"], "main": c["main"],
            "direction": direction, "price": price, "lots": lots,
            "date": date, "multiplier": c["mult"],
            "margin": margin, "margin_note": margin_note,
        })
    return positions, errors


# =============================================================================
# 八、页面主体
# =============================================================================


def render_app():
    st.set_page_config(page_title="国内期货趋势回调策略", page_icon="📈", layout="wide")
    st.title("国内期货趋势回调策略")
    st.caption("数据：新浪优先（3次重试，退避2秒、4秒）｜主力合约自动识别｜"
               "趋势：连续合约状态机｜入场与持仓：主力合约")

    # ---------- 侧边栏：参数输入 ----------
    with st.sidebar:
        st.header("参数设置")
        equity = st.number_input("账户总权益（元）", value=100000.0, min_value=1000.0,
                                 step=10000.0, format="%.0f")
        risk_pct_input = st.number_input("单笔风险比例（%）", value=2.0, min_value=0.1,
                                         max_value=20.0, step=0.5)
        manual_margin = st.checkbox("是否手动设置保证金比例", value=False,
                                    help="勾选后按持仓行内填写的保证金比例计算（留空=自动：交易所比例+3%）")
        enable_positions = st.checkbox("启用持仓录入", value=False,
                                       help="勾选后在下方编辑持仓表格（可添加多行）")
        pos_rows = None
        if enable_positions:
            st.markdown("**持仓录入**：每行一个持仓；「保证金比例」留空=自动")
            pos_rows = st.data_editor(
                pd.DataFrame([{
                    "品种": "", "方向": "做多", "开仓价": 0.0,
                    "手数": 1, "开仓日期": "", "保证金比例": "",
                }]),
                num_rows="dynamic",
                use_container_width=True,
                key="futures_positions_editor",
                column_config={
                    "品种": st.column_config.TextColumn("品种（中文名/代码）"),
                    "方向": st.column_config.SelectboxColumn("方向", options=["做多", "做空"]),
                    "开仓价": st.column_config.NumberColumn("开仓价（元）", min_value=0.0, format="%.2f"),
                    "手数": st.column_config.NumberColumn("手数", min_value=1, step=1, format="%d"),
                    "开仓日期": st.column_config.TextColumn("开仓日期(YYYYMMDD)"),
                    "保证金比例": st.column_config.TextColumn("保证金比例(留空=自动)"),
                },
            )
        st.divider()
        st.caption("资金管理硬约束：单品种保证金占用 ≤ 权益×20%%（当前上限 %.0f 元）；"
                   "总隔夜保证金占用红线 = 权益×30%%（当前红线 %.0f 元），超限将在报告中红色警告。"
                   % (equity * POS_MARGIN_CAP_PCT, equity * TOTAL_MARGIN_CAP_PCT))
        st.caption("点击主区域的「运行策略扫描」按钮开始获取数据与计算；"
                   "页面加载时不会自动运行。")

    # ---------- 主区域：运行按钮 ----------
    st.markdown("---")
    if not st.button("🚀 运行策略扫描", type="primary", use_container_width=True):
        st.info("请在侧边栏设置参数（可启用持仓录入），然后点击「运行策略扫描」开始。")
        return

    global RISK_PCT, RISK_PCT_STRONG
    RISK_PCT = risk_pct_input / 100.0
    RISK_PCT_STRONG = RISK_PCT / 2.0          # 强趋势半仓：风险减半

    # ---------- 解析持仓 ----------
    positions = []
    pos_errors = []
    if enable_positions and pos_rows is not None:
        positions, pos_errors = parse_positions_rows(pos_rows, build_contracts(), manual_margin)
        if pos_errors:
            for e in pos_errors:
                st.warning("持仓录入：" + e)

    # ---------- 数据流水线 ----------
    st.info("开始运行：①识别主力合约 → ②获取连续合约 → ③计算趋势 → "
            "④获取主力合约 → ⑤信号扫描与持仓监控（约需1-2分钟）...")
    contracts = build_contracts()
    fetch_fails = []
    refresh_log = []
    refresh_summary = None

    # ① 自动识别最新主力合约
    if AUTO_REFRESH_MAIN:
        with st.spinner("正在识别最新主力合约（新浪接口，约需30秒）..."):
            try:
                m = refresh_main_codes(verbose=False)
            except Exception as e:
                m = {}
                fetch_fails.append("主力合约自动识别异常：%s" % e)
        if m:
            for c in contracts:
                if c["code"] in m and m[c["code"]] != c["main"]:
                    refresh_log.append("%s：%s → %s" % (c["name"], c["main"], m[c["code"]]))
                    c["main"] = m[c["code"]]
            refresh_summary = "成功识别 %d 个主力合约，其中 %d 个与备用代码不同" % (len(m), len(refresh_log))
        else:
            refresh_summary = "识别失败或结果为空，使用配置表备用主力合约代码"

    # ② 获取连续合约数据（趋势确认）
    st.write("**① 获取连续合约日线（新浪，3次重试）...**")
    cont_progress = st.progress(0.0)
    cont_dfs = {}
    for i, c in enumerate(contracts, 1):
        cont = c["code"] + "0"
        df, err = fetch_sina_daily(cont, desc="%s%s" % (c["name"], cont), quiet=True)
        # 数据陈旧保护：超过 DATA_STALE_DAYS 天未更新视为无交易（如退市/停牌品种）
        stale = False
        if df is not None and len(df) > 0:
            days_old = (dt.date.today() - df["date"].iloc[-1].date()).days
            stale = days_old > DATA_STALE_DAYS
        ok = df is not None and len(df) >= DATA_MIN_BARS and not stale
        cont_dfs[c["code"]] = df if ok else None
        if not ok:
            if stale:
                fetch_fails.append("%s 连续合约 %s（数据截止 %s，已超过%d天无更新，疑似无交易，已跳过）"
                                   % (c["name"], cont, str(df["date"].iloc[-1].date()), DATA_STALE_DAYS))
            else:
                fetch_fails.append("%s 连续合约 %s（%s）" % (c["name"], cont, err or "数据不足"))
        cont_progress.progress(i / len(contracts),
                               text="[%d/%d] %s %s" % (i, len(contracts), c["name"], cont))
    cont_progress.empty()

    # ③ 计算趋势，确定需要获取的主力合约
    st.write("**② 计算趋势资格（状态机：均线排列 + ADX连续3天>20 + EMA144斜率）...**")
    trends = {}
    need_main = set(p["code"] for p in positions)
    for c in contracts:
        df = cont_dfs.get(c["code"])
        if df is None or len(df) < DATA_MIN_BARS:
            trends[c["code"]] = (0, None, False)
            continue
        dfc = classify_trend(prepare_df(df))
        cont_dfs[c["code"]] = dfc
        last_row = dfc.iloc[-1]
        ok = bool(last_row["trend_ok"])
        trends[c["code"]] = (int(last_row["trend_dir"]), last_row["trend_type"], ok)
        if ok:
            need_main.add(c["code"])

    # ④ 获取主力合约数据（趋势资格品种 + 持仓品种）
    st.write("**③ 获取主力合约日线（按需：趋势资格品种 + 持仓品种）...**")
    main_dfs = {}
    need_main_list = [c for c in contracts if c["code"] in need_main]
    main_progress = st.progress(0.0)
    for i, c in enumerate(need_main_list, 1):
        df, err = fetch_sina_daily(c["main"], desc="%s%s" % (c["name"], c["main"]), quiet=True)
        if df is None:
            fetch_fails.append("%s 主力合约 %s（%s）" % (c["name"], c["main"], err or "未知错误"))
        else:
            main_dfs[c["code"]] = df
        main_progress.progress(i / max(1, len(need_main_list)),
                               text="[%d/%d] %s %s" % (i, len(need_main_list), c["name"], c["main"]))
    main_progress.empty()

    # 计算主力合约指标 + 合并连续合约趋势
    for code, df in main_dfs.items():
        dfp = prepare_df(df)
        cont_df = cont_dfs.get(code)
        if cont_df is not None and len(cont_df) >= DATA_MIN_BARS:
            dfp = merge_trend(dfp, cont_df)
        else:
            dfp = dfp.copy()
            dfp["trend_dir"] = np.zeros(len(dfp), dtype=int)
            dfp["trend_type"] = [None] * len(dfp)
        main_dfs[code] = dfp

    # ⑤ 信号扫描（已持仓品种跳过 + 只显示今日/昨日信号）
    held_codes = set(p["code"] for p in positions)
    signals = {}
    for c in contracts:
        if c["code"] in held_codes:
            continue                        # 已持仓品种自动跳过
        df = main_dfs.get(c["code"])
        if df is None or len(df) < DATA_MIN_BARS:
            continue
        sigs = scan_signals(df, equity, c["mult"], c["margin_actual"])
        sigs = filter_recent_signals(sigs, df)
        if sigs:
            signals[c["code"]] = sigs

    # 持仓监控
    pos_pairs = []
    for p in positions:
        df = main_dfs.get(p["code"])
        if df is None or len(df) < MONITOR_MIN_BARS:
            res = {
                "direction": p["direction"], "status": "数据不足，无法监控",
                "days": 0, "exit_date": None, "exit_price": None, "exit_reason": None,
                "hard_stop": None, "be_price": None, "be_armed": False,
                "lock": None, "lock_day": None, "sar_active": False, "sar_day": None,
                "atr_entry": 0.0, "last_close": 0.0, "last_ema25": 0.0,
                "last_ema50": 0.0, "last_sar": 0.0, "last_trend_type": None,
                "pnl": 0.0, "sar_progress": None, "events": [],
            }
        else:
            res = monitor_position(df, p)
        pos_pairs.append((p, res))

    st.success("扫描完成，正在生成报告...")

    # ================= 结果展示 =================
    st.markdown("---")
    st.subheader("一、趋势资格确认品种列表")
    quals = [c for c in contracts
             if cont_dfs.get(c["code"]) is not None and len(cont_dfs[c["code"]]) >= DATA_MIN_BARS
             and bool(cont_dfs[c["code"]].iloc[-1]["trend_ok"])]
    if quals:
        st.write("、".join(
            "%s(%s·%s)" % (c["name"], "多" if cont_dfs[c["code"]].iloc[-1]["trend_dir"] == 1 else "空",
                           cont_dfs[c["code"]].iloc[-1]["trend_type"])
            for c in quals))
    else:
        st.write("当前无趋势资格确认的品种（空仓观望）。")

    # 新开仓信号表
    st.subheader("二、新开仓信号表（仅显示今日或昨日新出现的信号；已持仓品种自动跳过）")
    rows = []
    for c in contracts:
        for sig in signals.get(c["code"], []):
            if sig["status"] == "已确认":
                remark = "信号后第%d天收盘确认%sEMA25" % (
                    sig["day_no"], "站上" if sig["dir"] == 1 else "跌破")
                # 资金管理硬约束提示：建议手数被单品种保证金上限约束时注明
                if sig.get("margin_capped"):
                    remark += "；受单品种20%%保证金约束（风险%d手→上限%d手）" % (
                        sig.get("lots_risk", 0), sig.get("lots_margin", 0))
            elif sig["status"] == "待确认":
                remark = "窗口第%d天，等待收盘%sEMA25" % (
                    sig["day_no"], "站上" if sig["dir"] == 1 else "跌破")
            else:
                remark = "5天窗口内未确认，已失效"
            rows.append({
                "品种": c["name"],
                "方向": "多" if sig["dir"] == 1 else "空",
                "趋势": "强" if sig["strong"] else "温和",
                "信号日": str(sig["signal_day"].date()),
                "状态": sig["status"],
                "入场价": fmt(sig["entry"]),
                "止损价": fmt(sig["stop"]),
                "ATR": fmt(sig["atr"]),
                "手数": fmt(sig["lots"], 0) if sig["lots"] > 0 else "资金不足",
                "风险金额": fmt(sig["risk_amt"], 0),
                "保证金占用": fmt(sig.get("margin_amt"), 0),
                "单品种占比": "%.1f%%" % (sig.get("margin_pct", 0.0) * 100),
                "备注": remark,
            })
    rows.sort(key=lambda r: r["信号日"])
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.write("最近%d个交易日内无新开仓信号。" % SIGNAL_LOOKBACK)

    # 持仓监控结果
    st.subheader("三、持仓监控结果")
    if not pos_pairs:
        st.write("当前无持仓录入。")
    for p, res in pos_pairs:
        st.markdown(format_position_block(p, res, equity))
        # 单品种20%红线：超限用红色警告
        m_amt = p["price"] * p["multiplier"] * p["lots"] * p["margin"]
        if equity > 0 and m_amt > equity * POS_MARGIN_CAP_PCT:
            st.error("⚠️ %s 单品种保证金占用 %.1f%%，已触及单品种20%%红线"
                     % (p["name"], m_amt / equity * 100))

    # 资金占用与隔夜风险（硬约束汇总）
    st.subheader("四、资金占用与隔夜风险（硬约束）")
    total_margin = calc_total_margin(pos_pairs)
    total_pct = total_margin / equity * 100.0 if equity > 0 else 0.0
    c1, c2, c3 = st.columns(3)
    c1.metric("单品种保证金上限（权益×20%）", "%s 元" % fmt(equity * POS_MARGIN_CAP_PCT, 0))
    c2.metric("总隔夜红线（权益×30%）", "%s 元" % fmt(equity * TOTAL_MARGIN_CAP_PCT, 0))
    c3.metric("总保证金占用", "%s 元" % fmt(total_margin, 0), "占权益 %.1f%%" % total_pct)
    if pos_pairs and total_margin > equity * TOTAL_MARGIN_CAP_PCT:
        # 硬约束2：总隔夜风险度超30%红线 → 红色警告
        st.error("⚠️ 总保证金占用 %s 元，占总权益 %.1f%%，已超过30%%红线，请减少持仓"
                 % (fmt(total_margin, 0), total_pct))
    elif pos_pairs:
        st.success("总保证金占用 %s 元，占总权益 %.1f%%，处于30%%红线以内"
                   % (fmt(total_margin, 0), total_pct))
    else:
        st.write("当前无持仓，无隔夜保证金占用。")

    # 次日条件单（可复制文本）
    st.subheader("五、次日条件单（可复制）")
    st.text_area("次日条件单文本", value=build_orders_text(pos_pairs),
                 height=280, label_visibility="collapsed")

    # 数据获取统计
    st.subheader("六、数据获取统计")
    cont_ok = sum(1 for c in contracts
                  if cont_dfs.get(c["code"]) is not None and len(cont_dfs[c["code"]]) >= DATA_MIN_BARS)
    main_ok = len(main_dfs)
    st.markdown(
        "**数据源**：新浪财经日线（akshare futures_zh_daily_sina，3次重试，退避2秒、4秒）\n\n"
        "**主力合约自动识别**：%s\n\n"
        "**连续合约获取**：%d/%d 个有效；**主力合约获取（按需）**：%d 个\n\n"
        "**扫描账户权益**：%.0f 元；**单笔风险比例**：温和 %.1f%% / 强趋势 %.1f%%"
        % (refresh_summary or "未启用", cont_ok, len(contracts), main_ok,
           equity, RISK_PCT * 100, RISK_PCT_STRONG * 100)
    )
    if refresh_log:
        st.write("主力合约更新：" + "；".join(refresh_log))
    if fetch_fails:
        st.markdown("**获取失败/数据不足**：")
        for f in fetch_fails:
            st.warning(f)
    st.caption("免责声明：本报告由程序自动生成，仅供研究参考，不构成投资建议。")


if __name__ == "__main__":
    render_app()
