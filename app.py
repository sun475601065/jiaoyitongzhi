# -*- coding: utf-8 -*-
'''
国内期货趋势回调策略 · Streamlit 网页应用
====================================================
基于 futures_interactive.py 的策略逻辑(品种配置/指标/入场信号/持仓监控/
手数计算/次日条件单完全一致), 适配 Streamlit Cloud 部署。

部署方式(Streamlit Cloud):
  1. 本文件命名为 app.py 放入仓库根目录
  2. requirements.txt 包含: streamlit akshare pandas numpy
  3. 在 Streamlit Cloud 新建应用并指向该仓库即可

运行方式(本地预览): streamlit run app.py
====================================================
'''

import re
import time
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

# ===========================================================================
# 一、品种-主力合约对照表(与 futures_interactive.py 完全一致, 可手动维护)
# ===========================================================================
CONTRACTS = {
    # ---- 黑色系 ----
    "螺纹钢": {"code": "RB2610", "multiplier": 10, "margin": 0.13},
    "热卷": {"code": "HC2610", "multiplier": 10, "margin": 0.13},
    "铁矿石": {"code": "I2701", "multiplier": 100, "margin": 0.15},
    "焦炭": {"code": "J2701", "multiplier": 100, "margin": 0.20},
    "焦煤": {"code": "JM2701", "multiplier": 60, "margin": 0.20},
    # ---- 能源化工 ----
    "甲醇": {"code": "MA2610", "multiplier": 10, "margin": 0.12},
    "PTA": {"code": "TA2701", "multiplier": 5, "margin": 0.12},
    "纯碱": {"code": "SA2701", "multiplier": 20, "margin": 0.15},
    "玻璃": {"code": "FG2701", "multiplier": 20, "margin": 0.15},
    "尿素": {"code": "UR2701", "multiplier": 20, "margin": 0.12},
    "短纤": {"code": "PF2701", "multiplier": 5, "margin": 0.10},
    "乙二醇": {"code": "EG2610", "multiplier": 10, "margin": 0.10},
    "苯乙烯": {"code": "EB2701", "multiplier": 5, "margin": 0.10},
    "液化气": {"code": "PG2701", "multiplier": 20, "margin": 0.12},
    "PVC": {"code": "V2701", "multiplier": 5, "margin": 0.12},
    "PP": {"code": "PP2701", "multiplier": 5, "margin": 0.10},
    "塑料": {"code": "L2701", "multiplier": 5, "margin": 0.10},
    "原油": {"code": "SC2610", "multiplier": 1000, "margin": 0.15},
    "燃油": {"code": "FU2610", "multiplier": 10, "margin": 0.15},
    "低硫燃料油": {"code": "LU2610", "multiplier": 10, "margin": 0.15},
    "沥青": {"code": "BU2611", "multiplier": 10, "margin": 0.10},
    "橡胶": {"code": "RU2701", "multiplier": 10, "margin": 0.10},
    "20号胶": {"code": "NR2610", "multiplier": 10, "margin": 0.10},
    "纸浆": {"code": "SP2701", "multiplier": 10, "margin": 0.10},
    # ---- 农产品 ----
    "棉花": {"code": "CF2701", "multiplier": 5, "margin": 0.12},
    "白糖": {"code": "SR2701", "multiplier": 10, "margin": 0.10},
    "豆粕": {"code": "M2701", "multiplier": 10, "margin": 0.10},
    "菜粕": {"code": "RM2611", "multiplier": 10, "margin": 0.10},
    "豆油": {"code": "Y2701", "multiplier": 10, "margin": 0.10},
    "棕榈油": {"code": "P2701", "multiplier": 10, "margin": 0.12},
    "玉米": {"code": "C2701", "multiplier": 10, "margin": 0.10},
    "淀粉": {"code": "CS2701", "multiplier": 10, "margin": 0.10},
    "鸡蛋": {"code": "JD2701", "multiplier": 5, "margin": 0.10},
    "生猪": {"code": "LH2701", "multiplier": 16, "margin": 0.12},
    "苹果": {"code": "AP2611", "multiplier": 10, "margin": 0.10},
    "红枣": {"code": "CJ2701", "multiplier": 5, "margin": 0.10},
    # ---- 有色金属 ----
    "沪铜": {"code": "CU2610", "multiplier": 5, "margin": 0.12},
    "国际铜": {"code": "BC2610", "multiplier": 5, "margin": 0.12},
    "沪铝": {"code": "AL2610", "multiplier": 5, "margin": 0.12},
    "沪锌": {"code": "ZN2610", "multiplier": 5, "margin": 0.12},
    "沪镍": {"code": "NI2610", "multiplier": 1, "margin": 0.15},
    "沪铅": {"code": "PB2610", "multiplier": 5, "margin": 0.12},
    "沪锡": {"code": "SN2610", "multiplier": 1, "margin": 0.15},
    "黄金": {"code": "AU2610", "multiplier": 1000, "margin": 0.10},
    "白银": {"code": "AG2610", "multiplier": 15, "margin": 0.12},
    # ---- 其他 ----
    "集运指数(欧线)": {"code": "EC2610", "multiplier": 50, "margin": 0.18},
}

# 常用英文/简称别名 -> 中文名
NAME_ALIASES = {
    "rb": "螺纹钢", "hc": "热卷", "ma": "甲醇", "ta": "PTA", "pta": "PTA",
    "i": "铁矿石", "sa": "纯碱", "fg": "玻璃", "cf": "棉花", "sr": "白糖",
    "m": "豆粕", "rm": "菜粕", "y": "豆油", "p": "棕榈油", "c": "玉米",
    "cs": "淀粉", "jd": "鸡蛋", "lh": "生猪", "j": "焦炭", "jm": "焦煤",
    "cu": "沪铜", "bc": "国际铜", "al": "沪铝", "zn": "沪锌", "pb": "沪铅",
    "ni": "沪镍", "sn": "沪锡", "au": "黄金", "ag": "白银",
    "sc": "原油", "fu": "燃油", "lu": "低硫燃料油", "bu": "沥青", "ru": "橡胶",
    "nr": "20号胶", "sp": "纸浆", "ap": "苹果", "cj": "红枣", "ur": "尿素",
    "pf": "短纤", "eg": "乙二醇", "eb": "苯乙烯", "pg": "液化气", "lpg": "液化气",
    "v": "PVC", "pvc": "PVC", "pp": "PP", "l": "塑料", "ec": "集运指数(欧线)",
    "集运": "集运指数(欧线)", "欧线": "集运指数(欧线)", "集运指数": "集运指数(欧线)",
}

# 风控与仓位参数(与 futures_interactive.py 一致)
DEFAULT_RISK = 0.01         # 默认单笔风险比例(1%)
BREAKEVEN_OFFSET = 0.0003   # 保本价 = 开仓价 ± 0.03%
MIN_HISTORY = 200           # 连续合约最少需要的K线数量
CONTRACT_MIN_HISTORY = 144  # 具体月份合约最少K线数(EMA144需要144根预热)
REQUEST_INTERVAL = 0.3      # 批量请求间隔(秒), 避免触发数据源限流

# 默认扫描的常用活跃品种(Streamlit 界面默认勾选, 可自行增减)
DEFAULT_SCAN_NAMES = [
    "螺纹钢", "热卷", "甲醇", "PTA", "铁矿石", "纯碱", "玻璃", "棉花",
    "白糖", "豆粕", "菜粕", "豆油", "棕榈油", "沪铜", "沪铝", "黄金",
    "白银", "原油", "PVC", "PP",
]


# ===========================================================================
# 二、品种/合约解析(与 futures_interactive.py 一致)
# ===========================================================================
def cont_of(main_code):
    '''由主力合约代码推导连续合约代码: RB2610 -> RB0, V2701 -> V0'''
    return re.sub(r"\d+$", "", main_code).upper() + "0"


def normalize_name(text):
    '''名称归一化: 全角转半角、去空格、转小写(用于模糊匹配)'''
    s = str(text or "")
    try:
        import unicodedata
        s = unicodedata.normalize("NFKC", s)
    except Exception:
        pass
    s = "".join(ch for ch in s if not ch.isspace())
    return s.lower()


def resolve_commodity(text):
    '''
    把用户输入解析为 (中文名, 主力合约代码, 连续合约代码)。
    支持: 中文名(螺纹钢) / 主力合约代码(RB2610) / 连续合约代码(RB0) /
          英文缩写(rb/pta) / 任意具体月份合约(V2701, 按字母前缀匹配品种)
    '''
    s = (text or "").strip()
    if not s:
        return None, None, None
    if s in CONTRACTS:
        info = CONTRACTS[s]
        return s, info["code"], cont_of(info["code"])
    up = s.upper()
    for name, info in CONTRACTS.items():
        if info["code"].upper() == up:
            return name, info["code"], cont_of(info["code"])
        if cont_of(info["code"]) == up:
            return name, info["code"], cont_of(info["code"])
    norm = normalize_name(s)
    if norm in NAME_ALIASES:
        name = NAME_ALIASES[norm]
        return name, CONTRACTS[name]["code"], cont_of(CONTRACTS[name]["code"])
    letters = re.sub(r"\d+$", "", up)
    if letters:
        for name, info in CONTRACTS.items():
            if re.sub(r"\d+$", "", info["code"]).upper() == letters:
                return name, info["code"], cont_of(info["code"])
    candidates = []
    for name in CONTRACTS:
        nname = normalize_name(name)
        if nname == norm:
            return name, CONTRACTS[name]["code"], cont_of(CONTRACTS[name]["code"])
        if nname.startswith(norm):
            candidates.append(name)
    if len(candidates) == 1:
        name = candidates[0]
        return name, CONTRACTS[name]["code"], cont_of(CONTRACTS[name]["code"])
    return None, None, None


def choose_position_code(text, main_code):
    '''
    确定持仓监控使用的合约代码:
      1. 用户输入具体月份合约(如 V2701) -> 直接使用
      2. 输入中文名/连续代码 -> 使用配置主力合约
    '''
    s = (text or "").strip()
    m = re.match(r"^([A-Za-z]+)(\d+)$", s)
    if m and m.group(2) != "0":
        return m.group(1).upper() + m.group(2), True
    return main_code, False


def direction_cn(direction):
    '''方向英文 -> 中文显示'''
    return "做多" if direction == "long" else "做空"


# ===========================================================================
# 三、数据获取(akshare, 带重试; 与 futures_interactive.py 一致)
# ===========================================================================
def normalize_columns(df):
    '''统一 akshare 各接口的列名为 date/open/high/low/close/volume'''
    df = df.copy()
    rename_map = {}
    for col in df.columns:
        c = str(col)
        if "日期" in c or c.lower() in ("date", "trade_date", "datetime"):
            rename_map[col] = "date"
        elif "开盘" in c or c.lower() == "open":
            rename_map[col] = "open"
        elif "最高" in c or c.lower() == "high":
            rename_map[col] = "high"
        elif "最低" in c or c.lower() == "low":
            rename_map[col] = "low"
        elif "收盘" in c or c.lower() == "close":
            rename_map[col] = "close"
        elif "成交量" in c or c.lower() in ("volume", "vol"):
            rename_map[col] = "volume"
    df = df.rename(columns=rename_map)
    need = ["date", "open", "high", "low", "close"]
    if not all(x in df.columns for x in need):
        raise RuntimeError("列名无法识别, 实际列: " + str(list(df.columns)))
    df = df[["date", "open", "high", "low", "close"]
            + (["volume"] if "volume" in df.columns else [])]
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _fetch_sina_daily(symbol, retries=3, progress_cb=None):
    '''
    通过 akshare 获取期货日线(升序), 带重试退避。
    symbol: 具体主力合约(如 RB2610) 或 连续合约(如 RB0)。
    '''
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("未安装 akshare, 请执行: pip install akshare")
    attempts = [
        ("futures_zh_daily_sina", {"symbol": symbol}),
        ("futures_main_sina", {"symbol": symbol}),
        ("futures_hist_sina", {"symbol": symbol}),
    ]
    errors = []
    for attempt in range(retries):
        for func_name, kwargs in attempts:
            try:
                raw = getattr(ak, func_name)(**kwargs)
                if raw is None or len(raw) == 0:
                    raise RuntimeError("返回空数据")
                df = normalize_columns(raw)
                if len(df) < 30:
                    raise RuntimeError("数据量过少(" + str(len(df)) + "行)")
                return df
            except Exception as e:
                errors.append("%s(第%d次): %s" % (func_name, attempt + 1, str(e)[:60]))
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("akshare 接口均失败 —— " + " | ".join(errors))


def fetch_contract_daily(code):
    '''获取具体主力合约日线(如 RB2610), 返回 (df, 数据源说明)'''
    df = _fetch_sina_daily(code)
    return df, "具体合约 %s" % code


def fetch_continuous_daily(cont_code):
    '''获取连续合约日线(如 RB0), 返回 (df, 数据源说明)'''
    df = _fetch_sina_daily(cont_code)
    return df, "连续合约 %s" % cont_code


def _fetch_with_continuous_fallback(name, main_code):
    '''
    统一取数逻辑(信号扫描与持仓监控共用):
      1) 具体合约 main_code 优先
      2) 失败/历史不足 -> 连续合约备用(标注"连续合约，仅供参考")
    '''
    cont_code = cont_of(main_code)
    try:
        df, src = fetch_contract_daily(main_code)
        if len(df) < CONTRACT_MIN_HISTORY:
            raise RuntimeError("合约历史数据不足(%d根, 需要%d)"
                               % (len(df), CONTRACT_MIN_HISTORY))
        return compute_indicators(df), src
    except Exception as e:
        st.caption("  ⚠ %s: 合约 %s 获取失败(%s), 改用连续合约 %s(仅供参考)"
                   % (name, main_code, str(e)[:50], cont_code))
    df, src = fetch_continuous_daily(cont_code)
    if len(df) < MIN_HISTORY:
        raise RuntimeError("连续合约历史数据不足(%d根, 需要%d)"
                           % (len(df), MIN_HISTORY))
    return compute_indicators(df), src + "（连续合约，仅供参考）"


def fetch_scan_data(name, info):
    '''扫描用数据: 配置主力合约优先, 失败用连续合约备用'''
    return _fetch_with_continuous_fallback(name, info["code"])


def fetch_trend_daily(cont_code):
    '''趋势确认用日线: 连续合约(如 RB0), 输出标注'''
    df, src = fetch_continuous_daily(cont_code)
    if len(df) < MIN_HISTORY:
        raise RuntimeError("趋势数据不足(%d根)" % len(df))
    return df, "趋势确认: " + src


def fetch_for_position(pos):
    '''持仓监控取数(与扫描共用统一逻辑): 用户合约/配置主力 -> 连续合约备用'''
    return _fetch_with_continuous_fallback(pos["name"], pos["code"])


# ===========================================================================
# 四、技术指标计算(与 futures_interactive.py 一致, 含 SAR)
# ===========================================================================
def wilder_smooth(series, n):
    '''Wilder 平滑: 前 n 个取简单平均, 之后 (prev*(n-1)+cur)/n'''
    s = series.astype(float)
    out = pd.Series(np.nan, index=s.index)
    if len(s) < n:
        return out
    out.iloc[n - 1] = s.iloc[:n].mean()
    for i in range(n, len(s)):
        out.iloc[i] = (out.iloc[i - 1] * (n - 1) + s.iloc[i]) / n
    return out


def calc_ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def calc_atr(df, n=14):
    '''ATR(平均真实波幅), Wilder 平滑'''
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return wilder_smooth(tr, n)


def calc_adx(df, n=14):
    '''ADX(平均趋向指数), 含 +DI/-DI'''
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -l.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = wilder_smooth(tr, n)
    plus_di = 100 * wilder_smooth(plus_dm, n) / atr
    minus_di = 100 * wilder_smooth(minus_dm, n) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = wilder_smooth(dx, n)
    return adx, plus_di, minus_di


def calc_kdj(df, n=9):
    '''KDJ(9,3,3): K/D 初值 50, J = 3K - 2D'''
    h, l, c = df["high"], df["low"], df["close"]
    ll = l.rolling(n, min_periods=n).min()
    hh = h.rolling(n, min_periods=n).max()
    rsv = (c - ll) / (hh - ll) * 100.0
    rsv = rsv.fillna(50.0)
    k = np.zeros(len(df))
    d = np.zeros(len(df))
    j = np.zeros(len(df))
    k[0], d[0] = 50.0, 50.0
    for i in range(1, len(df)):
        k[i] = 2.0 / 3.0 * k[i - 1] + 1.0 / 3.0 * rsv.iloc[i]
        d[i] = 2.0 / 3.0 * d[i - 1] + 1.0 / 3.0 * k[i]
        j[i] = 3.0 * k[i] - 2.0 * d[i]
    return pd.Series(k, index=df.index), pd.Series(d, index=df.index), pd.Series(j, index=df.index)


def calc_macd(df, fast=12, slow=26, signal=9):
    '''MACD: DIF = EMA(fast)-EMA(slow), DEA = EMA(DIF), 柱 = 2*(DIF-DEA)'''
    c = df["close"]
    dif = calc_ema(c, fast) - calc_ema(c, slow)
    dea = calc_ema(dif, signal)
    hist = 2.0 * (dif - dea)
    return dif, dea, hist


def calc_sar(df, af_step=0.02, af_max=0.2):
    '''
    Parabolic SAR(抛物线转向), 标准算法。
    返回带方向的 SAR 序列: sar>0 多头状态, sar<0 空头状态, 绝对值为 SAR 值。
    '''
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(df)
    sar = np.zeros(n)
    af = np.zeros(n)
    trend = np.zeros(n)
    if n < 3:
        return pd.Series(np.nan, index=df.index)
    if high[0] + low[0] <= high[1] + low[1]:
        trend[0], trend[1] = 1, 1
        sar[0], sar[1] = low[0], low[0]
        ep = high[1]
        af[0], af[1] = af_step, af_step
    else:
        trend[0], trend[1] = -1, -1
        sar[0], sar[1] = high[0], high[0]
        ep = low[1]
        af[0], af[1] = af_step, af_step
    for i in range(2, n):
        prev_sar, prev_af, prev_ep = sar[i - 1], af[i - 1], ep
        if trend[i - 1] == 1:
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            sar[i] = min(sar[i], low[i - 1], low[i - 2])
            if low[i] < sar[i]:
                trend[i] = -1
                sar[i] = prev_ep
                ep = low[i]
                af[i] = af_step
            else:
                trend[i] = 1
                if high[i] > prev_ep:
                    ep = high[i]
                    af[i] = min(prev_af + af_step, af_max)
                else:
                    ep = prev_ep
                    af[i] = prev_af
        else:
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            sar[i] = max(sar[i], high[i - 1], high[i - 2])
            if high[i] > sar[i]:
                trend[i] = 1
                sar[i] = prev_ep
                ep = high[i]
                af[i] = af_step
            else:
                trend[i] = -1
                if low[i] < prev_ep:
                    ep = low[i]
                    af[i] = min(prev_af + af_step, af_max)
                else:
                    ep = prev_ep
                    af[i] = prev_af
    return pd.Series(sar * trend, index=df.index)


def compute_indicators(df):
    '''在原始 OHLC 上追加全部指标列, 返回以日期为索引的新 DataFrame'''
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date", drop=False)
    df["ema25"] = calc_ema(df["close"], 25)
    df["ema50"] = calc_ema(df["close"], 50)
    df["ema144"] = calc_ema(df["close"], 144)
    df["atr"] = calc_atr(df, 14)
    df["adx"], df["plus_di"], df["minus_di"] = calc_adx(df, 14)
    df["k"], df["d"], df["j"] = calc_kdj(df, 9)
    df["dif"], df["dea"], df["macd_hist"] = calc_macd(df, 12, 26, 9)
    df["sar"] = calc_sar(df)
    return df


def align_common(dfi_a, dfi_b):
    '''把两个数据框按日期取交集(用于主力合约与趋势数据对齐)'''
    common = dfi_a.index.intersection(dfi_b.index)
    if len(common) == 0:
        return dfi_a, dfi_b
    return dfi_a.loc[common], dfi_b.loc[common]


# ===========================================================================
# 五、策略判定(与 futures_interactive.py 一致, 含 SAR 接管)
# ===========================================================================
def is_strong_trend(dfi, i):
    '''强趋势: ADX>20 且今日>昨日, 且 ATR 线上升'''
    if i < 1:
        return False
    row, prev = dfi.iloc[i], dfi.iloc[i - 1]
    return (row["adx"] > 20 and row["adx"] > prev["adx"]
            and row["atr"] > prev["atr"])


def trend_qualification(dfi, i):
    '''趋势资格判定(均线排列 + ADX), 返回 (方向, 是否满足, 描述)'''
    if i < 2:
        return None, False, "历史数据不足"
    row = dfi.iloc[i]
    bull = row["ema25"] > row["ema50"] > row["ema144"]
    bear = row["ema25"] < row["ema50"] < row["ema144"]
    if not (bull or bear):
        return None, False, "均线未形成多头/空头排列"
    adx = dfi["adx"]
    v = (adx.iloc[i - 2], adx.iloc[i - 1], adx.iloc[i])
    if not (v[0] > 20 and v[1] > 20 and v[2] > 20):
        return ("bull" if bull else "bear"), False, \
            "ADX 未连续3天>20 (三日值 %.1f/%.1f/%.1f)" % v
    if not (v[0] < v[1] < v[2]):
        return ("bull" if bull else "bear"), False, \
            "ADX 未逐日上升 (三日值 %.1f/%.1f/%.1f)" % v
    return ("bull" if bull else "bear"), True, \
        ("趋势资格满足: 均线" + ("多头" if bull else "空头") + "排列, "
         "ADX 三日 %.1f/%.1f/%.1f" % v)


def check_entry_signal(main_dfi, trend_dfi):
    '''
    在最后一根K线上判定入场信号。
    - 趋势方向/资格/类型(均线排列+ADX)使用 trend_dfi(连续合约)
    - 入场价格条件(J/MACD/EMA25/收盘价)使用 main_dfi(主力合约)
    '''
    main_dfi, trend_dfi = align_common(main_dfi, trend_dfi)
    i = len(trend_dfi) - 1
    result = {"direction": None, "trend_type": "", "reasons": []}
    if i < 3:
        result["reasons"] = ["历史数据不足"]
        return result
    mrow, mprev = main_dfi.iloc[i], main_dfi.iloc[i - 1]
    direction, qualified, desc = trend_qualification(trend_dfi, i)
    if not qualified:
        result["reasons"] = [desc]
        return result
    strong = is_strong_trend(trend_dfi, i)
    trend_type = "强趋势" if strong else "温和趋势"
    result["trend_type"] = trend_type
    reasons = ["趋势类型: " + trend_type + "(以连续合约趋势数据判定)", desc]

    if direction == "bull":
        j_threshold = 30 if strong else 20
        j_ok = mprev["j"] < j_threshold and mrow["j"] > mprev["j"]
        macd_ok = mrow["macd_hist"] < 0 and mrow["macd_hist"] > mprev["macd_hist"]
        close_ok = mrow["close"] > mrow["ema25"]
        j_upper_ok = mrow["j"] < 80
        reasons.append("J: %.1f->%.1f (昨日<%d且拐头向上, 主力合约)"
                       % (mprev["j"], mrow["j"], j_threshold))
        reasons.append("MACD柱: %.2f->%.2f (%s, 主力合约)" % (
            mprev["macd_hist"], mrow["macd_hist"],
            "绿柱缩短" if macd_ok else "未缩短"))
        reasons.append("收盘 %.1f vs EMA25 %.1f (主力合约)"
                       % (mrow["close"], mrow["ema25"]))
        if j_ok and macd_ok and close_ok and j_upper_ok:
            result["direction"] = "long"
            result["reasons"] = reasons
            return result
        result["reasons"] = reasons + ["条件未全部满足"]
        return result

    j_threshold = 70 if strong else 80
    j_ok = mprev["j"] > j_threshold and mrow["j"] < mprev["j"]
    macd_ok = mrow["macd_hist"] > 0 and mrow["macd_hist"] < mprev["macd_hist"]
    close_ok = mrow["close"] < mrow["ema25"]
    j_lower_ok = mrow["j"] > 20
    reasons.append("J: %.1f->%.1f (昨日>%d且拐头向下, 主力合约)"
                   % (mprev["j"], mrow["j"], j_threshold))
    reasons.append("MACD柱: %.2f->%.2f (%s, 主力合约)" % (
        mprev["macd_hist"], mrow["macd_hist"],
        "红柱缩短" if macd_ok else "未缩短"))
    reasons.append("收盘 %.1f vs EMA25 %.1f (主力合约)"
                   % (mrow["close"], mrow["ema25"]))
    if j_ok and macd_ok and close_ok and j_lower_ok:
        result["direction"] = "short"
        result["reasons"] = reasons
        return result
    result["reasons"] = reasons + ["条件未全部满足"]
    return result


def profit_lock_line(pos, dfi, entry_idx, atr, direction):
    '''利润锁定线: 有开仓日期用持仓以来最高/最低收盘 -/+ 1×ATR; 否则开仓价 ± 1×ATR'''
    entry_price = pos["entry_price"]
    if direction == "long":
        if pos.get("entry_date"):
            return float(dfi["close"].iloc[entry_idx:].max()) - atr
        return entry_price + atr
    else:
        if pos.get("entry_date"):
            return float(dfi["close"].iloc[entry_idx:].min()) + atr
        return entry_price - atr


def profit_lock_gate(pos, dfi, entry_idx, atr, direction):
    '''利润锁定线门槛: 盈利曾达到 1×ATR 后才启用锁定线'''
    entry_price = pos["entry_price"]
    if direction == "long":
        if pos.get("entry_date"):
            return (float(dfi["close"].iloc[entry_idx:].max()) - entry_price) >= atr
        return (dfi["close"].iloc[-1] - entry_price) >= atr
    else:
        if pos.get("entry_date"):
            return (entry_price - float(dfi["close"].iloc[entry_idx:].min())) >= atr
        return (entry_price - dfi["close"].iloc[-1]) >= atr


def sar_takeover_active(pos, dfi, entry_idx):
    '''
    SAR 接管条件(强趋势止盈改用 SAR+EMA50):
      1) 持仓 >= 5 天(需录入开仓日期)
      2) 持仓期间 MACD 柱曾连续 3 天同向
      3) 持仓期间 ADX 曾连续 3 天上升
      4) 当前 MACD 连续两日反向
    '''
    i = len(dfi) - 1
    notes = []
    if not pos.get("entry_date"):
        notes.append("未记录开仓日期, 无法确认持仓天数, 不启动SAR接管")
        return False, notes
    days = i - entry_idx + 1
    if days < 5:
        notes.append("持仓不足5天(%d天), 不启动SAR接管" % days)
        return False, notes
    hist = dfi["macd_hist"].iloc[entry_idx:i + 1].values
    dirs = np.sign(np.diff(hist))
    same_dirs = set()
    for t in range(2, len(dirs)):
        if dirs[t - 2] == dirs[t - 1] == dirs[t] != 0:
            same_dirs.add(int(dirs[t]))
    if not same_dirs:
        notes.append("持仓期间MACD未出现连续3天同向, 不启动SAR接管")
        return False, notes
    adx = dfi["adx"].iloc[entry_idx:i + 1].values
    adx_rise3 = False
    for t in range(2, len(adx)):
        if not np.isnan(adx[t - 2]) and adx[t - 2] < adx[t - 1] < adx[t]:
            adx_rise3 = True
            break
    if not adx_rise3:
        notes.append("持仓期间ADX未出现连续3天上升, 不启动SAR接管")
        return False, notes
    if len(dirs) >= 2:
        reverse2 = any(d in same_dirs
                       and dirs[-1] == -d and dirs[-2] == -d for d in same_dirs)
        if not reverse2:
            notes.append("当前MACD未连续两日反向, 不启动SAR接管")
            return False, notes
    else:
        notes.append("历史数据不足, 不启动SAR接管")
        return False, notes
    notes.append("SAR接管条件满足: 持仓%d天, MACD曾连续3天同向, "
                 "ADX曾连续3天上升, 当前MACD连续两日反向" % days)
    return True, notes


def monitor_position(pos, dfi):
    '''
    用最新一根K线(今日)判断单个持仓是否离场。
    返回 dict: name/code/direction/.../status/reasons/sar_takeover/sar_notes
    '''
    i = len(dfi) - 1
    row = dfi.iloc[i]
    direction = pos["direction"]
    entry_price = pos["entry_price"]
    lots = pos.get("lots") or 1
    atr = row["atr"]
    close, high, low = row["close"], row["high"], row["low"]
    reasons = []

    entry_idx = 0
    if pos.get("entry_date"):
        try:
            entry_idx = int(dfi.index.searchsorted(pd.Timestamp(pos["entry_date"])))
        except Exception:
            entry_idx = 0
        entry_idx = min(max(entry_idx, 0), len(dfi) - 1)

    # 1) 硬止损 / 保本止损(盘中触发)
    stop_dist = 2.0 * atr
    hard_stop = entry_price - stop_dist if direction == "long" else entry_price + stop_dist
    breakeven_price = (entry_price * (1 + BREAKEVEN_OFFSET) if direction == "long"
                       else entry_price * (1 - BREAKEVEN_OFFSET))
    pnl = (close - entry_price) if direction == "long" else (entry_price - close)
    breakeven_active = pnl >= atr

    if direction == "long":
        if low <= hard_stop:
            reasons.append("硬止损: 盘中最低 %.1f 触及开仓价-2×ATR(%.1f)" % (low, hard_stop))
        if breakeven_active and low <= breakeven_price:
            reasons.append("保本止损: 浮盈>=1×ATR(%.1f), 盘中最低 %.1f 触及保本价 %.2f"
                           % (atr, low, breakeven_price))
    else:
        if high >= hard_stop:
            reasons.append("硬止损: 盘中最高 %.1f 触及开仓价+2×ATR(%.1f)" % (high, hard_stop))
        if breakeven_active and high >= breakeven_price:
            reasons.append("保本止损: 浮盈>=1×ATR(%.1f), 盘中最高 %.1f 触及保本价 %.2f"
                           % (atr, high, breakeven_price))

    # 2) 均线死叉/金叉: 无条件离场
    if direction == "long" and row["ema25"] < row["ema50"]:
        reasons.append("均线死叉: EMA25(%.1f) < EMA50(%.1f), 无条件离场"
                       % (row["ema25"], row["ema50"]))
    if direction == "short" and row["ema25"] > row["ema50"]:
        reasons.append("均线金叉: EMA25(%.1f) > EMA50(%.1f), 无条件离场"
                       % (row["ema25"], row["ema50"]))

    # 3) 止盈(收盘价判断) + SAR 接管
    sar_takeover, sar_notes = sar_takeover_active(pos, dfi, entry_idx)
    strong = is_strong_trend(dfi, i)
    if direction == "long":
        if strong:
            if sar_takeover and row["sar"] > 0:
                if close < row["ema50"] or close < row["sar"]:
                    reasons.append("强趋势止盈(SAR接管): 收盘 %.1f 跌破 SAR(%.1f)或EMA50(%.1f)"
                                   % (close, row["sar"], row["ema50"]))
            else:
                if close < row["ema25"]:
                    reasons.append("强趋势止盈: 收盘 %.1f 跌破EMA25(%.1f)"
                                   % (close, row["ema25"]))
        else:
            lock = profit_lock_line(pos, dfi, entry_idx, atr, direction)
            gate = profit_lock_gate(pos, dfi, entry_idx, atr, direction)
            if close < row["ema50"]:
                reasons.append("温和趋势止盈: 收盘 %.1f 跌破EMA50(%.1f)"
                               % (close, row["ema50"]))
            if gate and close < lock:
                reasons.append("利润锁定线止盈: 收盘 %.1f 跌破锁定线 %.1f"
                               % (close, lock))
    else:
        if strong:
            if sar_takeover and row["sar"] < 0:
                if close > row["ema50"] or close > abs(row["sar"]):
                    reasons.append("强趋势止盈(SAR接管): 收盘 %.1f 上破 SAR(%.1f)或EMA50(%.1f)"
                                   % (close, abs(row["sar"]), row["ema50"]))
            else:
                if close > row["ema25"]:
                    reasons.append("强趋势止盈: 收盘 %.1f 上破EMA25(%.1f)"
                                   % (close, row["ema25"]))
        else:
            lock = profit_lock_line(pos, dfi, entry_idx, atr, direction)
            gate = profit_lock_gate(pos, dfi, entry_idx, atr, direction)
            if close > row["ema50"]:
                reasons.append("温和趋势止盈: 收盘 %.1f 上破EMA50(%.1f)"
                               % (close, row["ema50"]))
            if gate and close > lock:
                reasons.append("利润锁定线止盈: 收盘 %.1f 上破锁定线 %.1f"
                               % (close, lock))

    # 4) 状态汇总
    status = "触发离场，建议次日开盘平仓" if reasons else "继续持有"
    mult = CONTRACTS.get(pos.get("name"), {}).get("multiplier", 10)
    pnl_money = pnl * mult * lots
    return {
        "name": pos.get("name", pos["code"]),
        "code": pos["code"], "direction": direction,
        "entry_price": entry_price, "lots": lots,
        "close": close, "atr": atr,
        "pnl": pnl, "pnl_pct": pnl / entry_price * 100.0, "pnl_money": pnl_money,
        "hard_stop": hard_stop, "breakeven_price": breakeven_price,
        "breakeven_active": breakeven_active,
        "ema25": row["ema25"], "ema50": row["ema50"], "j": row["j"],
        "adx": row["adx"], "sar": row["sar"],
        "sar_takeover": sar_takeover, "sar_notes": sar_notes,
        "status": status, "reasons": reasons,
    }


def suggest_lots(account_equity, price, atr, name, margin_rate):
    '''
    计算建议手数:
      每手保证金 = 价格 × 合约乘数 × 保证金比例
      最大可开仓手数(按权益) = 账户总权益 ÷ 每手保证金
      风险手数 = 账户总权益 × 单笔风险比例 ÷ (2×ATR × 合约乘数)
      建议手数 = min(最大可开仓手数, 风险手数), 向下取整
    返回 (手数或None, 计算过程说明字符串)。
    '''
    if account_equity is None or account_equity <= 0:
        return None, ""
    mult = CONTRACTS.get(name, {}).get("multiplier", 10)
    margin_rate = margin_rate or CONTRACTS.get(name, {}).get("margin", 0.10)
    per_lot_margin = price * mult * margin_rate
    max_lots = int(account_equity // per_lot_margin) if per_lot_margin > 0 else 0
    per_lot_risk = 2.0 * atr * mult
    risk_lots = int(account_equity * DEFAULT_RISK // per_lot_risk) if per_lot_risk > 0 else 0
    lots = min(max_lots, risk_lots)
    detail_lines = [
        "每手保证金 = %.2f × %d × %.1f%% = %s 元" % (
            price, mult, margin_rate * 100, format(int(per_lot_margin), ",")),
        "最大可开仓(按权益) = %s ÷ %s = %d 手" % (
            format(int(account_equity), ","), format(int(per_lot_margin), ","), max_lots),
        "风险手数 = %s × %.1f%% ÷ (2×%.2f×%d) = %d 手" % (
            format(int(account_equity), ","), DEFAULT_RISK * 100, atr, mult, risk_lots),
        "建议手数 = min(%d, %d) = %d 手" % (max_lots, risk_lots, lots),
    ]
    return lots, "\n".join(detail_lines)


def build_condition_order(np_, account_equity, risk_pct):
    '''生成单个信号品种的"次日条件单"文本(方便复制到同花顺期货通等云条件单)'''
    name = np_["name"]
    code = np_["code"]
    main_contract = np_.get("main_contract") or code
    dir_cn = direction_cn(np_["direction"])
    trigger = np_["price"]
    stop = np_["stop"]
    breakeven = np_["breakeven"]
    if np_["lots"] is None:
        lots_txt = "（未提供账户权益, 请自行计算手数）"
    else:
        lots_txt = "%d 手" % np_["lots"]
    if dir_cn == "做多":
        trigger_txt = "价格 >= %s 买入开仓" % ("%.2f" % trigger)
    else:
        trigger_txt = "价格 <= %s 卖出开仓" % ("%.2f" % trigger)
    lines = [
        "【次日条件单】%s %s" % (name, dir_cn),
        "  品种: %s（%s）" % (name, code),
        "  主力合约: %s（信号基于主力合约日线, 请到交易软件核对）" % main_contract,
        "  方向: %s" % dir_cn,
        "  开仓触发价: %.2f（参考主力合约今日收盘, 建议设为次日开盘价附近）" % trigger,
        "  触发条件: %s" % trigger_txt,
        "  止损价: %.2f（开仓价 - 2×ATR, 盘中触发即平仓）" % stop,
        "  保本价: %.2f（浮盈 >= 1×ATR 后, 止损移至该价）" % breakeven,
        "  建议手数: %s" % lots_txt,
    ]
    if np_.get("lots_detail"):
        lines.append("  手数计算过程:")
        for d in np_["lots_detail"].split("\n"):
            lines.append("    " + d)
    return "\n".join(lines)


# ===========================================================================
# 六、Streamlit 界面
# ===========================================================================
def parse_positions_table(editor_df):
    '''
    把 st.data_editor 的持仓表格解析为持仓字典列表。
    返回 (positions, 错误提示列表)。
    '''
    positions = []
    errors = []
    if editor_df is None or editor_df.empty:
        return positions, errors
    for _, row in editor_df.iterrows():
        text = str(row.get("品种") or "").strip()
        if not text:
            continue  # 空行跳过
        name, main_code, cont_code = resolve_commodity(text)
        if not name:
            errors.append("无法识别品种「%s」" % text)
            continue
        dir_raw = str(row.get("方向") or "").strip()
        if dir_raw in ("多", "做多"):
            direction = "long"
        elif dir_raw in ("空", "做空"):
            direction = "short"
        else:
            errors.append("品种 %s 方向无效: %s" % (name, dir_raw or "空"))
            continue
        try:
            entry_price = float(row.get("开仓价"))
        except (TypeError, ValueError):
            errors.append("品种 %s 开仓价无效" % name)
            continue
        try:
            lots = int(float(row.get("手数")))
        except (TypeError, ValueError):
            lots = 1
        entry_date = str(row.get("开仓日期(可空)") or "").strip()
        code, user_specified = choose_position_code(text, main_code)
        positions.append({
            "name": name, "code": code,
            "contract": code if user_specified else None,
            "direction": direction,
            "entry_price": entry_price, "lots": lots, "entry_date": entry_date,
        })
    return positions, errors


def render_results(result):
    '''渲染扫描结果(signals / monitor / condition orders / data info)'''
    signals = result["signals"]
    monitor = result["monitor"]
    condition_text = result["condition_text"]
    main_info = result["main_info"]
    fetch_errors = result["fetch_errors"]

    # ---- 今日新开仓信号 ----
    st.subheader("📈 今日新开仓信号")
    if signals:
        sig_df = pd.DataFrame([{
            "品种": s["name"], "方向": direction_cn(s["direction"]),
            "主力合约": s["main_contract"],
            "参考开仓价": round(s["price"], 2),
            "止损价": round(s["stop"], 2),
            "保本价": round(s["breakeven"], 2),
            "建议手数": s["lots"] if s["lots"] is not None else "-",
        } for s in signals])
        st.dataframe(sig_df, use_container_width=True, hide_index=True)
        with st.expander("查看信号依据"):
            for s in signals:
                st.markdown("**%s %s**（%s）" % (
                    s["name"], direction_cn(s["direction"]), s["main_contract"]))
                for r in s["reasons"]:
                    st.markdown("- " + r)
                st.caption("数据源: %s | 趋势确认: %s"
                           % (s.get("main_source", ""), s.get("trend_source", "")))
    else:
        st.info("今日无满足条件的新开仓信号。")

    # ---- 持仓监控 ----
    st.subheader("📋 持仓监控")
    if monitor:
        mon_df = pd.DataFrame([{
            "品种": m["name"], "方向": direction_cn(m["direction"]),
            "持仓合约": m["code"], "开仓价": round(m["entry_price"], 2),
            "现价": round(m["close"], 2),
            "浮盈(元)": round(m["pnl_money"]),
            "浮盈%": round(m["pnl_pct"], 2),
            "状态": m["status"],
        } for m in monitor])
        st.dataframe(mon_df, use_container_width=True, hide_index=True)
        with st.expander("查看离场原因 / SAR 状态"):
            for m in monitor:
                st.markdown("**%s %s** 持仓合约 %s" % (
                    m["name"], direction_cn(m["direction"]), m["code"]))
                st.caption("数据源: %s" % m.get("data_source", ""))
                if m.get("sar_takeover"):
                    st.caption("SAR接管: 已启用(强趋势止盈改用 SAR+EMA50)")
                elif m.get("sar_notes"):
                    st.caption("SAR接管: 未启用(%s)" % "；".join(m["sar_notes"]))
                if m["reasons"]:
                    for r in m["reasons"]:
                        st.markdown("- " + r)
                else:
                    st.markdown("- 继续持有, 无离场信号")
    else:
        st.info("未录入持仓或持仓解析失败。")

    # ---- 次日条件单 ----
    st.subheader("📝 次日条件单(可复制)")
    if condition_text.strip():
        st.text_area("条件单文本", condition_text, height=400,
                     label_visibility="collapsed")
        st.download_button("下载条件单", condition_text,
                           file_name="条件单.txt", mime="text/plain")
    else:
        st.info("无新开仓信号, 无次日条件单。")

    # ---- 数据信息 ----
    st.subheader("🔧 数据与运行信息")
    if main_info:
        info_df = pd.DataFrame([{
            "品种": name, "主力合约": CONTRACTS.get(name, {}).get("code", "?"),
            "数据源": src, "K线数": len(dfi),
            "最新日期": str(dfi["date"].iloc[-1].date()),
        } for name, (dfi, src) in main_info.items()])
        st.dataframe(info_df, use_container_width=True, hide_index=True)
    if fetch_errors:
        st.warning("以下品种数据获取失败: " + "；".join(
            "%s(%s)" % (k, v) for k, v in fetch_errors.items()))
    st.caption("数据源: akshare(新浪财经) 具体主力合约 + 连续合约(趋势确认/备用)")


def main():
    st.set_page_config(page_title="国内期货趋势回调策略", page_icon="📈",
                       layout="wide")
    st.title("国内期货趋势回调策略 · 网页版")
    st.caption("基于 futures_interactive.py 相同策略逻辑; "
               "数据获取仅在点击「运行策略扫描」后执行。")

    # ---- 侧边栏参数 ----
    st.sidebar.header("参数设置")
    account_equity = st.sidebar.number_input(
        "账户总权益（元）", min_value=0.0, value=100000.0, step=10000.0, format="%.0f")
    risk_pct = st.sidebar.number_input(
        "单笔风险比例（%）", min_value=0.1, max_value=10.0, value=1.0, step=0.1,
        format="%.1f")
    risk_rate = risk_pct / 100.0
    st.sidebar.caption("止损/保本/止盈规则与 SAR 接管逻辑与命令行版一致。")

    # ---- 扫描品种选择 ----
    st.subheader("① 选择扫描品种")
    col1, col2 = st.columns([1, 3])
    with col1:
        select_all = st.checkbox("全选", value=False)
    with col2:
        all_names = list(CONTRACTS.keys())
        default_names = [n for n in DEFAULT_SCAN_NAMES if n in all_names]
        scan_names = st.multiselect(
            "扫描品种(数据获取耗时与品种数成正比)", options=all_names,
            default=all_names if select_all else default_names)
    if select_all:
        scan_names = all_names
    st.caption("持仓监控使用你录入的具体合约或对应配置主力合约数据; "
               "未录入持仓时仅进行信号扫描。")

    # ---- 持仓录入(data_editor 动态多行) ----
    st.subheader("② 持仓录入(可添加多行)")
    pos_cols = ["品种", "方向", "开仓价", "手数", "开仓日期(可空)"]
    editor_df = st.data_editor(
        pd.DataFrame(columns=pos_cols),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "品种": st.column_config.TextColumn(
                "品种(中文名如 螺纹钢, 或合约代码如 V2701)", width="medium"),
            "方向": st.column_config.SelectboxColumn(
                "方向", options=["多", "空"], width="small"),
            "开仓价": st.column_config.NumberColumn(
                "开仓价", min_value=0.0, format="%.2f", width="small"),
            "手数": st.column_config.NumberColumn(
                "手数", min_value=1, step=1, width="small"),
            "开仓日期(可空)": st.column_config.TextColumn(
                "开仓日期(格式2026-08-18, 可空)", width="small"),
        },
        height=180,
    )

    # ---- 运行按钮(点击后才取数) ----
    st.subheader("③ 运行")
    if st.button("运行策略扫描", type="primary"):
        if not scan_names:
            st.error("请至少选择一个扫描品种。")
            st.stop()
        positions, pos_errors = parse_positions_table(editor_df)
        # 运行扫描(带进度)
        with st.status("正在获取行情数据并计算指标 ...", expanded=True) as status:
            run_result = run_scan(scan_names, positions, account_equity, risk_rate)
            status.update(label="扫描完成", state="complete")
        if pos_errors:
            st.warning("持仓解析提示: " + "；".join(pos_errors))
        # 存入 session_state, 供后续交互直接展示
        st.session_state["result"] = run_result
        st.session_state["result_meta"] = {
            "equity": account_equity, "risk": risk_rate,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ---- 展示结果(点击按钮后或有缓存结果时) ----
    if "result" in st.session_state and st.session_state["result"] is not None:
        meta = st.session_state.get("result_meta", {})
        st.caption("上次运行: %s | 账户权益 %.0f 元 | 单笔风险 %.1f%%"
                   % (meta.get("time", "-"), meta.get("equity", 0),
                      meta.get("risk", 0) * 100))
        render_results(st.session_state["result"])
    else:
        st.info("👈 设置参数与持仓后, 点击「运行策略扫描」开始。")


def run_scan(scan_names, positions, account_equity, risk_rate):
    '''
    执行数据获取与信号/持仓监控计算(点击按钮后调用)。
    返回结果字典, 供界面渲染。
    '''
    main_data = {}
    trend_data = {}
    fetch_errors = {}
    total = len(scan_names)
    progress = st.progress(0.0, text="准备获取数据 ...")
    for idx, name in enumerate(scan_names):
        info = CONTRACTS.get(name)
        if not info:
            fetch_errors[name] = "配置中不存在"
            continue
        progress.progress((idx + 1) / total,
                          text="正在获取 %s(%s) ..." % (name, info["code"]))
        time.sleep(REQUEST_INTERVAL)
        # 主力合约数据(信号/止损/止盈/监控用), 失败自动用连续合约备用
        try:
            dfi, source = fetch_scan_data(name, info)
            main_data[name] = (dfi, source)
        except Exception as e:
            fetch_errors[name] = str(e)[:100]
            continue
        # 趋势确认数据(连续合约)
        try:
            tdf, tsource = fetch_trend_daily(cont_of(info["code"]))
            trend_data[name] = (compute_indicators(tdf), tsource)
        except Exception as e:
            trend_data[name] = (main_data[name][0], main_data[name][1] + "（趋势确认同源）")
    progress.empty()

    # ---- 新开仓信号 ----
    signals = []
    for name, (main_dfi, main_source) in main_data.items():
        trend_dfi, trend_source = trend_data.get(name, (main_dfi, main_source))
        sig = check_entry_signal(main_dfi, trend_dfi)
        if sig["direction"] is None:
            continue
        info = CONTRACTS[name]
        row = main_dfi.iloc[-1]
        price = float(row["close"])
        atr = float(row["atr"])
        stop = (price - 2 * atr) if sig["direction"] == "long" else (price + 2 * atr)
        be = (price * (1 + BREAKEVEN_OFFSET) if sig["direction"] == "long"
              else price * (1 - BREAKEVEN_OFFSET))
        margin = info.get("margin", 0.10)
        lots, lots_detail = suggest_lots(account_equity, price, atr, name, margin)
        signals.append({
            "name": name, "code": info["code"], "direction": sig["direction"],
            "price": price, "stop": stop, "breakeven": be, "atr": atr,
            "main_contract": info["code"],
            "main_source": main_source, "trend_source": trend_source,
            "margin_rate": margin, "lots": lots, "lots_detail": lots_detail,
            "reasons": sig["reasons"], "trend_type": sig["trend_type"],
        })

    # ---- 持仓监控(与扫描共用统一取数逻辑; 持仓合约=配置主力时复用扫描数据) ----
    monitor = []
    for pos in positions:
        config_code = CONTRACTS.get(pos["name"], {}).get("code")
        try:
            if pos["code"] == config_code and pos["name"] in main_data:
                dfi, data_source = main_data[pos["name"]]  # 复用扫描数据
            else:
                dfi, data_source = fetch_for_position(pos)  # 用户指定其他合约
        except Exception as e:
            monitor.append({"name": pos["name"], "code": pos["code"],
                            "direction": pos["direction"],
                            "error": "数据获取失败: %s" % str(e)[:100]})
            continue
        m = monitor_position(pos, dfi)
        m["data_source"] = data_source
        monitor.append(m)

    # ---- 条件单文本 ----
    condition_text = "\n\n".join(
        build_condition_order(s, account_equity, risk_rate) for s in signals)

    return {
        "signals": signals,
        "monitor": monitor,
        "condition_text": condition_text,
        "main_info": main_data,
        "fetch_errors": fetch_errors,
    }


if __name__ == "__main__":
    main()
