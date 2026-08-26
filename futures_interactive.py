


# -*- coding: utf-8 -*-
'''
国内期货趋势回调策略 - 交互式运行脚本
================================================
功能:
  1. 手动配置"品种-主力合约对照表"(CONTRACTS): 中文名 / 主力合约代码 / 合约乘数 / 保证金比例
  2. 数据获取: 使用配置中的主力合约代码, 通过 akshare futures_zh_daily_sina
     获取具体合约日线(如 symbol="RB2610"); 失败时用连续合约备用并标注
     "连续合约，仅供参考"
  3. 趋势确认: 使用连续合约(如 RB0), 输出中标注
  4. 持仓监控与信号计算: 使用具体主力合约数据
  5. 持仓录入: 只需输入中文品种名称, 自动匹配配置中的主力合约代码
  6. 报告同时显示中文名称与主力合约代码
  7. 保证金与仓位计算:
     - 每手保证金 = 当前价 × 合约乘数 × 保证金比例
     - 最大可开仓手数 = 账户总权益 ÷ 每手保证金
     - 风险手数 = 账户总权益 × 单笔风险比例 ÷ (2×ATR × 合约乘数)
     - 建议手数 = min(最大可开仓手数, 风险手数), 向下取整
  8. 交互录入持仓 -> 逐项输出持仓状态(继续持有 / 触发离场及原因)
  9. 输出报告: 【今日新开仓信号】【持仓监控】【次日下单提示单】
     并自动保存到 reports/report_日期.txt

策略规则:
  * 趋势资格(多): EMA25 > EMA50 > EMA144 且 ADX 连续3天 > 20 且逐日上升
  * 趋势类型: ADX>20 且今日>昨日 且 ATR上升 -> 强趋势; 否则温和趋势
  * 多头入场(温和趋势): J昨日<20 且今日>昨日, MACD绿柱缩短, 收盘>EMA25, J<80
  * 多头入场(强趋势):   J昨日<30 且今日>昨日, 其余同上
  * 空头入场: 空头排列, ADX 连续3天>20 且逐日上升,
    温和趋势 J>80 拐头向下 / 强趋势 J>70 拐头向下,
    MACD红柱缩短, 收盘<EMA25, J>20
  * 硬止损: 开仓价 ± 2×ATR, 盘中触发(用当日最低/最高价判断)
  * 保本: 浮盈 >= 1×ATR 后, 止损移至 开仓价 ± 0.03%
  * 温和趋势止盈: 收盘跌破/升破 EMA50, 或触发利润锁定线
  * 强趋势止盈: 收盘跌破/升破 EMA25
  * SAR 接管: 持仓>=5天 + 持仓期间MACD曾连续3天同向 + ADX曾连续3天上升
    + 当前MACD连续两日反向 时, 强趋势止盈改用 SAR+EMA50(跌破SAR或EMA50即离场)
  * 均线死叉(多) / 金叉(空): 无条件离场

依赖: pip install akshare pandas numpy
运行: python futures_interactive.py
================================================
'''

import os
import re
import sys
import time
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

# 让 Windows 控制台能正常显示/输入中文(避免 GBK 编码报错)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 一、★ 品种-主力合约对照表(手动维护, 本脚本的数据源配置) ★
# ---------------------------------------------------------------------------
# 每项: 中文名称 -> {"code": 主力合约代码, "multiplier": 合约乘数, "margin": 保证金比例}
# 说明:
#   1. 主力合约代码会随月份轮换, 请按实际行情定期更新本表(如 螺纹钢 从 RB2610 换为 RB2701)。
#   2. 合约乘数与保证金比例请以交易所/期货公司最新公告为准, 可自行修改。
#   3. 趋势确认使用连续合约(由主力合约代码自动推导, 如 RB2610 -> RB0), 无需单独配置。
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

# 常用英文/简称别名 -> 中文名(输入更便捷, 如 pta/pvc/rb)
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

# 风控与仓位参数
DEFAULT_EQUITY = 100000.0   # 默认账户权益(元), 回车使用该值
DEFAULT_RISK = 0.01         # 默认单笔风险比例(1%)
BREAKEVEN_OFFSET = 0.0003   # 保本价 = 开仓价 ± 0.03%
MIN_HISTORY = 200           # 连续合约最少需要的K线数量
CONTRACT_MIN_HISTORY = 144  # 具体月份合约最少K线数(EMA144需要144根预热)
REQUEST_INTERVAL = 0.3      # 批量请求间隔(秒), 避免触发数据源限流


# ---------------------------------------------------------------------------
# 二、品种/合约解析(基于 CONTRACTS 配置)
# ---------------------------------------------------------------------------
def cont_of(main_code):
    '''由主力合约代码推导连续合约代码: RB2610 -> RB0, V2701 -> V0'''
    return re.sub(r"\d+$", "", main_code).upper() + "0"


def normalize_name(text):
    '''名称归一化: 全角转半角、去空格、转小写(用于模糊匹配)'''
    s = unicodedata.normalize("NFKC", text or "")
    s = "".join(ch for ch in s if not ch.isspace())
    return s.lower()


def resolve_commodity(text):
    '''
    把用户输入解析为 (中文名, 主力合约代码, 连续合约代码)。
    支持: 中文名(螺纹钢) / 主力合约代码(RB2610) / 连续合约代码(RB0) /
          英文缩写(rb/pta) / 任意具体月份合约(V2701, 按字母前缀匹配品种)
    无法识别返回 (None, None, None)。
    '''
    s = (text or "").strip()
    if not s:
        return None, None, None
    # 1) 精确中文名
    if s in CONTRACTS:
        info = CONTRACTS[s]
        return s, info["code"], cont_of(info["code"])
    up = s.upper()
    # 2) 主力合约代码 或 连续合约代码 精确匹配
    for name, info in CONTRACTS.items():
        if info["code"].upper() == up:
            return name, info["code"], cont_of(info["code"])
        if cont_of(info["code"]) == up:
            return name, info["code"], cont_of(info["code"])
    # 3) 英文缩写 / 别名
    norm = normalize_name(s)
    if norm in NAME_ALIASES:
        name = NAME_ALIASES[norm]
        return name, CONTRACTS[name]["code"], cont_of(CONTRACTS[name]["code"])
    # 4) 任意具体月份合约: 按字母前缀匹配品种(如 V2609 -> PVC)
    letters = re.sub(r"\d+$", "", up)
    if letters:
        for name, info in CONTRACTS.items():
            if re.sub(r"\d+$", "", info["code"]).upper() == letters:
                return name, info["code"], cont_of(info["code"])
    # 5) 归一化模糊匹配(如 "集运指数" -> "集运指数(欧线)")
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


def list_available_names():
    '''返回全部配置品种中文名(每行8个, 便于提示)'''
    names = sorted(CONTRACTS.keys())
    lines = []
    for i in range(0, len(names), 8):
        lines.append("  " + "、".join(names[i:i + 8]))
    return "\n".join(lines)


def round_tick(price, tick):
    '''把价格取整到最小变动价位的整数倍(条件单需要合法价位); tick为空则不取整'''
    if not tick or tick <= 0:
        return price
    return round(price / tick) * tick


# ---------------------------------------------------------------------------
# 三、数据获取(akshare)
# ---------------------------------------------------------------------------
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


def _fetch_sina_daily(symbol, retries=3):
    '''
    通过 akshare 获取期货日线(升序)。
    symbol: 具体主力合约(如 RB2610) 或 连续合约(如 RB0)。
    依次尝试 futures_zh_daily_sina / futures_main_sina / futures_hist_sina;
    新浪接口偶发异常(限流/返回异常), 因此整体重试 retries 次, 退避间隔递增。
    全部失败抛出 RuntimeError。
    '''
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("未安装 akshare, 请先执行: pip install akshare pandas numpy")
    attempts = [
        ("futures_zh_daily_sina", {"symbol": symbol}),  # 新浪-日线(具体合约/连续均可)
        ("futures_main_sina", {"symbol": symbol}),      # 新浪-主力连续
        ("futures_hist_sina", {"symbol": symbol}),      # 新浪-历史(备用)
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
            time.sleep(2 * (attempt + 1))  # 退避: 2秒, 4秒
    raise RuntimeError("akshare 接口均失败 —— " + " | ".join(errors))


def fetch_contract_daily(code):
    '''
    获取具体主力合约日线(如 RB2610), 返回 (df, 数据源说明)。
    失败抛出 RuntimeError(由调用方决定是否用连续合约备用)。
    '''
    df = _fetch_sina_daily(code)
    return df, "具体合约 %s" % code


def fetch_continuous_daily(cont_code):
    '''
    获取连续合约日线(如 RB0), 用于趋势确认或数据备用。
    返回 (df, 数据源说明)。
    '''
    df = _fetch_sina_daily(cont_code)
    return df, "连续合约 %s" % cont_code


def _fetch_with_continuous_fallback(name, main_code):
    '''
    统一取数逻辑(信号扫描与持仓监控共用, 保证完全一致):
      1) 用具体合约 main_code 通过 akshare futures_zh_daily_sina 获取(优先)
      2) 失败/历史不足 -> 改用连续合约备用, 标注"连续合约，仅供参考"
    返回 (dfi, 数据源说明); 全部失败抛出 RuntimeError。
    '''
    cont_code = cont_of(main_code)
    # 1) 具体合约优先
    try:
        df, src = fetch_contract_daily(main_code)
        if len(df) < CONTRACT_MIN_HISTORY:
            raise RuntimeError("合约历史数据不足(%d根, 需要%d)"
                               % (len(df), CONTRACT_MIN_HISTORY))
        return compute_indicators(df), src
    except Exception as e:
        print("  [提示] %s: 合约 %s 数据获取失败(%s), 改用连续合约 %s, 仅供参考"
              % (name, main_code, str(e)[:80], cont_code))
    # 2) 连续合约备用
    df, src = fetch_continuous_daily(cont_code)
    if len(df) < MIN_HISTORY:
        raise RuntimeError("连续合约历史数据不足(%d根, 需要%d)"
                           % (len(df), MIN_HISTORY))
    return compute_indicators(df), src + "（连续合约，仅供参考）"


def fetch_scan_data(name, info):
    '''
    扫描用数据: 配置的主力合约优先, 失败时用连续合约备用并标注
    "连续合约，仅供参考"。与持仓监控共用 _fetch_with_continuous_fallback。
    返回 (dfi, 数据源说明)。
    '''
    return _fetch_with_continuous_fallback(name, info["code"])


def fetch_trend_daily(cont_code):
    '''
    趋势确认用日线: 连续合约(如 RB0), 输出中标注。
    返回 (df, 数据源说明)。
    '''
    df, src = fetch_continuous_daily(cont_code)
    if len(df) < MIN_HISTORY:
        raise RuntimeError("趋势数据不足(%d根)" % len(df))
    return df, "趋势确认: " + src


def fetch_for_position(pos):
    '''
    为单个持仓获取监控用指标数据。
    与信号扫描共用 _fetch_with_continuous_fallback, 保证取数逻辑完全一致。
    code: 用户输入的具体合约(如 V2701); 未输入时用 CONTRACTS 配置主力合约。
    返回 (dfi, 数据源说明); 全部失败抛出 RuntimeError。
    '''
    code = pos["code"]   # 持仓合约代码(用户输入的具体合约 或 配置主力合约)
    name = pos["name"]
    return _fetch_with_continuous_fallback(name, code)


# ---------------------------------------------------------------------------
# 四、技术指标计算
# ---------------------------------------------------------------------------
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
    返回带方向的 SAR 序列:
      sar > 0 表示多头状态(SAR 在价格下方, 支撑多头);
      sar < 0 表示空头状态(SAR 在价格上方, 压制空头);
      绝对值为 SAR 值。
    '''
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    n = len(df)
    sar = np.zeros(n)
    af = np.zeros(n)
    trend = np.zeros(n)  # 1=多, -1=空

    if n < 3:
        return pd.Series(np.nan, index=df.index)

    # 用前两根K线判定初始方向
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
        if trend[i - 1] == 1:  # 多头
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            sar[i] = min(sar[i], low[i - 1], low[i - 2])  # SAR 不高于最近两日低点
            if low[i] < sar[i]:  # 反转向空
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
        else:  # 空头
            sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
            sar[i] = max(sar[i], high[i - 1], high[i - 2])  # SAR 不低于最近两日高点
            if high[i] > sar[i]:  # 反转向多
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

    # 返回带方向的 SAR: 多头为正, 空头为负
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
    df["sar"] = calc_sar(df)  # 带方向符号的 Parabolic SAR
    return df


def align_common(dfi_a, dfi_b):
    '''把两个数据框按日期取交集(用于主力合约与趋势数据对齐)'''
    common = dfi_a.index.intersection(dfi_b.index)
    if len(common) == 0:
        return dfi_a, dfi_b
    return dfi_a.loc[common], dfi_b.loc[common]


# ---------------------------------------------------------------------------
# 五、策略判定
# ---------------------------------------------------------------------------
def is_strong_trend(dfi, i):
    '''强趋势: ADX>20 且今日>昨日, 且 ATR 线上升'''
    if i < 1:
        return False
    row, prev = dfi.iloc[i], dfi.iloc[i - 1]
    return (row["adx"] > 20 and row["adx"] > prev["adx"]
            and row["atr"] > prev["atr"])


def trend_qualification(dfi, i):
    '''
    趋势资格判定(均线排列 + ADX)。
    返回: (方向 'bull'/'bear'/None, 是否满足 bool, 描述字符串)
    '''
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
    - 趋势方向/资格/类型(均线排列 + ADX)使用 trend_dfi(连续合约)
    - 入场价格条件(J/MACD/EMA25/收盘价)使用 main_dfi(主力合约)
    两数据源按日期对齐后取最后一天。
    返回 dict: direction('long'/'short'/None), trend_type, reasons列表
    '''
    main_dfi, trend_dfi = align_common(main_dfi, trend_dfi)
    i = len(trend_dfi) - 1
    result = {"direction": None, "trend_type": "", "reasons": []}
    if i < 3:
        result["reasons"] = ["历史数据不足"]
        return result
    trow, tprev = trend_dfi.iloc[i], trend_dfi.iloc[i - 1]   # 趋势判断(连续合约)
    mrow, mprev = main_dfi.iloc[i], main_dfi.iloc[i - 1]     # 价格/指标(主力合约)

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

    # 空头
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
    '''
    利润锁定线:
      * 有开仓日期: 持仓以来最高/最低收盘价 -/+ 1×ATR(移动锁定线)
      * 无开仓日期: 简化为 开仓价 ± 1×ATR(固定锁定线)
    '''
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
    SAR 接管条件判定(满足后, 强趋势止盈改用 SAR+EMA50 而非只用 EMA25):
      1) 持仓 >= 5 天(以开仓日期 entry_date 为准; 未记录开仓日期则无法确认, 不启用)
      2) 持仓期间 MACD 柱曾连续 3 天同向(连续3天放大 或 连续3天缩小)
      3) 持仓期间 ADX 曾连续 3 天上升
      4) 当前 MACD 连续两日反向(最近两日柱的方向与"曾同向方向"相反)
    返回 (是否接管, 说明列表)。
    '''
    i = len(dfi) - 1
    notes = []
    # 1) 持仓天数
    if not pos.get("entry_date"):
        notes.append("未记录开仓日期, 无法确认持仓天数, 不启动SAR接管")
        return False, notes
    days = i - entry_idx + 1
    if days < 5:
        notes.append("持仓不足5天(%d天), 不启动SAR接管" % days)
        return False, notes
    # 2) MACD 柱方向序列(持仓期间)
    hist = dfi["macd_hist"].iloc[entry_idx:i + 1].values
    dirs = np.sign(np.diff(hist))  # +1放大 -1缩小 0不变
    same_dirs = set()
    for t in range(2, len(dirs)):
        if dirs[t - 2] == dirs[t - 1] == dirs[t] != 0:
            same_dirs.add(int(dirs[t]))
    if not same_dirs:
        notes.append("持仓期间MACD未出现连续3天同向, 不启动SAR接管")
        return False, notes
    # 3) ADX 曾连续 3 天上升(持仓期间)
    adx = dfi["adx"].iloc[entry_idx:i + 1].values
    adx_rise3 = False
    for t in range(2, len(adx)):
        if not np.isnan(adx[t - 2]) and adx[t - 2] < adx[t - 1] < adx[t]:
            adx_rise3 = True
            break
    if not adx_rise3:
        notes.append("持仓期间ADX未出现连续3天上升, 不启动SAR接管")
        return False, notes
    # 4) 当前 MACD 连续两日反向(与曾同向方向相反)
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
    全部指标基于持仓数据源(具体主力合约 / 连续合约备用)。
    返回 dict: name/code/direction/.../status/reasons
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

    # ---- 1) 硬止损 / 保本止损(盘中触发) ----
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

    # ---- 2) 均线死叉/金叉: 无条件离场 ----
    if direction == "long" and row["ema25"] < row["ema50"]:
        reasons.append("均线死叉: EMA25(%.1f) < EMA50(%.1f), 无条件离场"
                       % (row["ema25"], row["ema50"]))
    if direction == "short" and row["ema25"] > row["ema50"]:
        reasons.append("均线金叉: EMA25(%.1f) > EMA50(%.1f), 无条件离场"
                       % (row["ema25"], row["ema50"]))

    # ---- 3) 止盈(收盘价判断) ----
    # SAR 接管判定(持仓>=5天 + MACD曾连续3天同向 + ADX曾连续3天上升 + 当前MACD连续两日反向)
    sar_takeover, sar_notes = sar_takeover_active(pos, dfi, entry_idx)
    strong = is_strong_trend(dfi, i)
    if direction == "long":
        if strong:
            # SAR 接管且 SAR 处于多头状态: 止盈改用 SAR + EMA50
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
            # SAR 接管且 SAR 处于空头状态: 止盈改用 SAR + EMA50
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

    # ---- 4) 状态汇总 ----
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
    detail_lines = []
    detail_lines.append(
        "每手保证金 = %.2f × %d × %.1f%% = %s 元" % (
            price, mult, margin_rate * 100,
            format(int(per_lot_margin), ",")))
    detail_lines.append(
        "最大可开仓(按权益) = %s ÷ %s = %d 手" % (
            format(int(account_equity), ","), format(int(per_lot_margin), ","), max_lots))
    detail_lines.append(
        "风险手数 = %s × %.1f%% ÷ (2×%.2f×%d) = %d 手" % (
            format(int(account_equity), ","), DEFAULT_RISK * 100, atr, mult, risk_lots))
    detail_lines.append(
        "建议手数 = min(%d, %d) = %d 手" % (max_lots, risk_lots, lots))
    return lots, "\n".join(detail_lines)


# ---------------------------------------------------------------------------
# 六、交互输入
# ---------------------------------------------------------------------------
def normalize_direction(text):
    '''把用户输入归一化为 long/short; 无法识别返回 None'''
    s = (text or "").strip().lower()
    if s in ("多", "做多", "long", "l", "buy", "duo", "1"):
        return "long"
    if s in ("空", "做空", "short", "s", "sell", "kong", "-1"):
        return "short"
    return None


def direction_cn(direction):
    '''方向英文 -> 中文显示'''
    return "做多" if direction == "long" else "做空"


def input_int(prompt, minimum=None):
    '''读取整数输入, 容错重试'''
    while True:
        s = input(prompt).strip()
        try:
            v = int(s)
            if minimum is not None and v < minimum:
                print("输入不能小于 %d, 请重新输入。" % minimum)
                continue
            return v
        except ValueError:
            print("输入无效, 请输入整数。")


def input_float(prompt):
    '''读取浮点数输入, 容错重试'''
    while True:
        s = input(prompt).strip()
        try:
            v = float(s)
            if v <= 0:
                print("请输入大于 0 的数字。")
                continue
            return v
        except ValueError:
            print("输入无效, 请输入数字。")


def input_account_equity():
    '''
    询问账户总权益(用于仓位计算)。
    回车 -> 默认 100000; 输入 0 -> 不计算建议手数(条件单手数留空)。
    '''
    s = input("请输入账户总权益（元）[回车使用默认100000，输入0表示不计算建议手数]：").strip()
    if not s:
        return DEFAULT_EQUITY
    try:
        v = float(s)
        if v < 0:
            print("输入无效, 使用默认100000。")
            return DEFAULT_EQUITY
        return v
    except ValueError:
        print("输入无效, 使用默认100000。")
        return DEFAULT_EQUITY


def choose_position_code(text, main_code):
    '''
    确定持仓监控使用的合约代码(与扫描信号同一套代码逻辑)。
    规则:
      1. 用户输入的是具体月份合约(如 V2701 / v2701) -> 直接使用该代码
         (通过 akshare futures_zh_daily_sina(symbol="V2701") 获取数据)
      2. 用户输入中文名/连续代码(如 PVC / V0 / RB0) -> 使用 CONTRACTS 配置主力合约
    返回 (持仓合约代码, 是否用户指定具体合约)。
    '''
    s = (text or "").strip()
    m = re.match(r"^([A-Za-z]+)(\d+)$", s)
    if m and m.group(2) != "0":
        # 用户输入的具体月份合约, 统一大写后直接使用
        return m.group(1).upper() + m.group(2), True
    # 未输入具体合约 -> 用配置主力合约(扫描与持仓监控一致)
    return main_code, False


def input_positions():
    '''
    交互录入持仓列表。
    用户只需输入中文品种名称(如 螺纹钢), 脚本自动匹配配置中的主力合约代码;
    也可直接输入具体合约代码(如 V2701), 持仓监控直接使用该合约获取数据。
    返回: [{"name","code","contract","direction","entry_price","lots","entry_date"}, ...]
    '''
    n = input_int("请输入持仓品种数量（没有持仓请输入0）：", minimum=0)
    positions = []
    for k in range(1, n + 1):
        print("---- 录入持仓 %d/%d ----" % (k, n))
        while True:
            text = input("品种名称（中文名如 螺纹钢, 或合约代码如 RB0 / V2701）：").strip()
            name, main_code, cont_code = resolve_commodity(text)
            if name:
                break
            print("未找到品种「%s」，请重新输入。配置中的品种如下：" % text)
            print(list_available_names())
        direction = normalize_direction(input("方向（做多/做空）："))
        while direction is None:
            direction = normalize_direction(input("方向输入无效, 请输入 做多 或 做空："))
        entry_price = input_float("开仓价：")
        lots = input_int("手数：", minimum=1)
        entry_date = input("开仓日期（格式 2026-08-18, 可回车跳过）：").strip()
        # 持仓监控合约: 用户输入的具体合约优先, 否则用配置主力合约
        code, user_specified = choose_position_code(text, main_code)
        contract = code if user_specified else None
        if user_specified:
            print("  [提示] %s: 使用用户输入的具体合约 %s 进行持仓监控(与扫描同一取数逻辑)。"
                  % (name, code))
        else:
            print("  [提示] %s: 已自动匹配配置主力合约 %s。"
                  % (name, main_code))
        positions.append({
            "name": name, "code": code, "contract": contract,
            "direction": direction,
            "entry_price": entry_price, "lots": lots, "entry_date": entry_date,
        })
    return positions


# ---------------------------------------------------------------------------
# 七、条件单生成
# ---------------------------------------------------------------------------
def build_condition_order(np_, account_equity, risk_pct):
    '''
    生成单个信号品种的"次日条件单"文本, 方便复制到同花顺期货通等云条件单。
    np_: 新开仓信号 dict(含 name/code/direction/price/stop/breakeven/lots/lots_detail)
    '''
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

    lines = []
    lines.append("【次日条件单】%s %s" % (name, dir_cn))
    lines.append("  品种: %s（%s）" % (name, code))
    lines.append("  主力合约: %s（信号基于主力合约日线, 请到交易软件核对）" % main_contract)
    lines.append("  方向: %s" % dir_cn)
    lines.append("  开仓触发价: %.2f（参考主力合约今日收盘, 建议设为次日开盘价附近）" % trigger)
    lines.append("  触发条件: %s" % trigger_txt)
    lines.append("  止损价: %.2f（开仓价 - 2×ATR, 盘中触发即平仓）" % stop)
    lines.append("  保本价: %.2f（浮盈 >= 1×ATR 后, 止损移至该价）" % breakeven)
    lines.append("  建议手数: %s" % lots_txt)
    if np_.get("lots_detail"):
        lines.append("  手数计算过程:")
        for d in np_["lots_detail"].split("\n"):
            lines.append("    " + d)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 八、报告渲染
# ---------------------------------------------------------------------------
def fmt_price(x):
    try:
        return "%.2f" % float(x)
    except (TypeError, ValueError):
        return "-"


def render_report(main_data, trend_data, new_positions, monitor_results,
                  fetch_errors, account_equity, risk_pct):
    '''
    生成最终文本报告(四部分), 同时显示中文名称与主力合约代码。
    main_data:  {品种中文名: (dfi, 数据源说明)}
    trend_data: {品种中文名: (dfi, 数据源说明)}
    '''
    L = []
    L.append("=" * 68)
    L.append(" 国内期货趋势回调策略 · 交互式报告")
    L.append(" 报告生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("=" * 68)

    # ---- 一、今日新开仓信号 ----
    L.append("")
    L.append("【一】今日新开仓信号")
    if new_positions:
        L.append("%-8s%-6s%-10s%10s%10s%10s%6s" % (
            "品种", "方向", "主力合约", "参考开仓价", "止损价", "保本价", "手数"))
        L.append("-" * 72)
        for np_ in new_positions:
            lots_txt = "-" if np_["lots"] is None else str(np_["lots"])
            L.append("%-8s%-6s%-10s%10s%10s%10s%6s" % (
                np_["name"], direction_cn(np_["direction"]),
                np_.get("main_contract", np_["code"]),
                fmt_price(np_["price"]), fmt_price(np_["stop"]),
                fmt_price(np_["breakeven"]), lots_txt))
        L.append("-" * 72)
        L.append("注: 开仓价为主力合约今日收盘参考价; 止损=开仓价±2×ATR; 主力合约代码以交易软件为准。")
        for np_ in new_positions:
            L.append("")
            L.append("  [%s] %s 信号依据:" % (np_["name"], direction_cn(np_["direction"])))
            for r in np_["reasons"]:
                L.append("    - " + r)
            L.append("    数据源: %s" % np_.get("main_source", np_["code"]))
            L.append("    %s" % np_.get("trend_source", ""))
    else:
        L.append("  今日无满足条件的新开仓信号。")

    # ---- 二、持仓监控 ----
    L.append("")
    L.append("【二】持仓监控")
    if not monitor_results:
        L.append("  未录入持仓。")
    else:
        for m in monitor_results:
            name = m["name"]
            if m.get("error"):
                L.append("")
                L.append("  [%s] —— %s" % (name, m["error"]))
                continue
            L.append("")
            L.append("  [%s] %s  持仓合约: %s  开仓价: %s  手数: %d" % (
                name, direction_cn(m["direction"]), m["code"],
                fmt_price(m["entry_price"]), m["lots"]))
            L.append("    现价: %s  ATR(14): %s  浮盈: %+.1f (%+.2f%%)  "
                     "约 %s 元" % (fmt_price(m["close"]), fmt_price(m["atr"]),
                                   m["pnl"], m["pnl_pct"],
                                   format(int(m["pnl_money"]), ",")))
            be_mark = " (已生效)" if m["breakeven_active"] else ""
            L.append("    硬止损(2×ATR): %s  保本价: %s%s" % (
                fmt_price(m["hard_stop"]), fmt_price(m["breakeven_price"]), be_mark))
            L.append("    EMA25: %s  EMA50: %s  J: %.1f  ADX: %.1f  SAR: %s" % (
                fmt_price(m["ema25"]), fmt_price(m["ema50"]), m["j"], m["adx"],
                fmt_price(m.get("sar"))))
            # SAR 接管状态(强趋势止盈: 接管后使用 SAR+EMA50, 否则用 EMA25)
            if m.get("sar_takeover"):
                L.append("    SAR接管: 已启用(强趋势止盈改用 SAR+EMA50)")
            elif m.get("sar_notes"):
                L.append("    SAR接管: 未启用(%s)" % "；".join(m["sar_notes"]))
            L.append("    数据源: %s" % m.get("data_source", "连续合约 %s" % m["code"]))
            if m["status"] == "继续持有":
                L.append("    -> 状态: 继续持有")
                if m["breakeven_active"]:
                    L.append("      · 浮盈>=1×ATR, 止损已移至保本价 %s"
                             % fmt_price(m["breakeven_price"]))
            else:
                L.append("    -> 状态: 触发离场，建议次日开盘平仓")
                for r in m["reasons"]:
                    L.append("      · " + r)

    # ---- 三、次日下单提示单 + 条件单 ----
    L.append("")
    L.append("【三】次日下单提示单")
    if new_positions:
        L.append("%-8s%-6s%-10s%10s%10s%10s%6s" % (
            "品种", "方向", "主力合约", "参考开仓价", "止损价", "保本价", "手数"))
        L.append("-" * 72)
        for np_ in new_positions:
            lots_txt = "-" if np_["lots"] is None else str(np_["lots"])
            L.append("%-8s%-6s%-10s%10s%10s%10s%6s" % (
                np_["name"], direction_cn(np_["direction"]),
                np_.get("main_contract", np_["code"]),
                fmt_price(np_["price"]), fmt_price(np_["stop"]),
                fmt_price(np_["breakeven"]), lots_txt))
        L.append("")
        L.append("---- 次日条件单(可复制到同花顺期货通等软件的云条件单) ----")
        for k, np_ in enumerate(new_positions, start=1):
            L.append("")
            L.append("  ========== 条件单 %d/%d ==========" % (k, len(new_positions)))
            for line in build_condition_order(np_, account_equity, risk_pct).split("\n"):
                L.append("  " + line)
            L.append("  ====================================")
    else:
        L.append("  无。")
    L.append("")
    L.append("  操作说明:")
    L.append("    1. 信号基于主力合约收盘数据, 于次日开盘执行; 开仓价以实际成交为准, 止损价随之平移。")
    L.append("    2. 持仓监控: 硬止损 2×ATR 盘中触发即离场; 浮盈>=1×ATR 后止损移至保本价(开仓价±0.03%)。")
    L.append("    3. 均线死叉(多)/金叉(空)无条件离场; 温和趋势止盈看EMA50/利润锁定线; 强趋势止盈看EMA25。")
    L.append("    4. 建议手数 = min(按权益可开仓手数, 按风险手数), 请结合自身资金与交易所最新保证金调整。")

    # ---- 四、数据信息 ----
    L.append("")
    L.append("【四】数据与运行信息")
    for name, (dfi, source) in main_data.items():
        info = CONTRACTS.get(name, {})
        L.append("  %-10s 主力合约 %-8s OK  %s | 数据 %d 根, 最新 %s" % (
            name, info.get("code", "?"), source, len(dfi), dfi["date"].iloc[-1].date()))
        if name in trend_data:
            _, tsource = trend_data[name]
            L.append("            %s" % tsource)
    for name, err in fetch_errors.items():
        L.append("  %-10s 失败  %s" % (name, err))
    L.append("")
    L.append("  数据源: akshare(新浪财经) 具体主力合约 + 连续合约(趋势确认/备用)")
    L.append("  * 主力合约代码来自脚本顶部 CONTRACTS 配置, 请随月份轮换及时更新。")
    L.append("  * 本报告仅为策略信号参考, 不构成投资建议。")
    L.append("=" * 68)
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 九、主流程
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print(" 国内期货趋势回调策略 - 交互式运行")
    print("=" * 60)
    print(" 监控品种 %d 个(来自脚本顶部 CONTRACTS 配置)。" % len(CONTRACTS))

    # ---- 1) 获取数据(主力合约 + 趋势连续合约), 逐个容错 ----
    print("")
    print("正在获取行情数据(akshare, 共 %d 个品种, 约需1-3分钟) ..."
          % len(CONTRACTS))
    main_data = {}    # 中文名 -> (dfi, 数据源说明)
    trend_data = {}   # 中文名 -> (dfi, 数据源说明)
    fetch_errors = {}
    for name, info in CONTRACTS.items():
        time.sleep(REQUEST_INTERVAL)  # 控制请求频率
        # 1.1 主力合约数据(信号/止损/止盈/监控用), 失败自动用连续合约备用
        try:
            dfi, source = fetch_scan_data(name, info)
            main_data[name] = (dfi, source)
            tag = "（连续合约备用）" if "仅供参考" in source else ""
            print("  [OK] %s: %s | %d 根K线%s" % (name, source, len(dfi), tag))
        except Exception as e:
            fetch_errors[name] = str(e)
            print("  [失败] %s: %s" % (name, e))
            continue
        # 1.2 趋势确认数据(连续合约, 输出标注)
        try:
            tdf, tsource = fetch_trend_daily(cont_of(info["code"]))
            trend_data[name] = (compute_indicators(tdf), tsource)
        except Exception as e:
            trend_data[name] = (main_data[name][0],
                                main_data[name][1] + "（趋势确认同源）")
            print("  [提示] %s: 趋势数据获取失败(%s), 趋势确认使用主力合约数据"
                  % (name, str(e)[:60]))

    if not main_data:
        print("")
        print("[错误] 所有品种数据获取失败, 无法生成报告。")
        print("       请检查网络后重试。")
        sys.exit(1)

    # ---- 2) 判断今日新开仓信号(趋势用连续合约, 价格用主力合约) ----
    print("")
    print("正在计算指标与信号 ...")
    new_positions = []
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
        new_positions.append({
            "name": name, "code": info["code"], "direction": sig["direction"],
            "price": price, "stop": stop, "breakeven": be, "atr": atr,
            "main_contract": info["code"],
            "main_source": main_source, "trend_source": trend_source,
            "margin_rate": info.get("margin", 0.10),
            "lots": None, "lots_detail": "",
            "reasons": sig["reasons"], "trend_type": sig["trend_type"],
        })
    if new_positions:
        print("  检测到 %d 个新开仓信号(详见报告)。" % len(new_positions))
    else:
        print("  今日无满足条件的新开仓信号。")

    # ---- 3) 询问账户权益 + 可选手动保证金 ----
    print("")
    account_equity = input_account_equity()
    override_margin = None
    ans = input("是否手动指定保证金比例（回车N使用CONTRACTS配置）[y/N]：").strip().lower()
    if ans in ("y", "yes"):
        m = input_float("请输入保证金比例（%，如 13 表示 13%）：")
        override_margin = m / 100.0
        print("  已手动指定保证金比例 %.1f%%, 用于全部品种手数计算。" % (override_margin * 100))
    for np_ in new_positions:
        margin = override_margin if override_margin else np_.get("margin_rate", 0.10)
        np_["lots"], np_["lots_detail"] = suggest_lots(
            account_equity, np_["price"], np_["atr"], np_["name"], margin)

    # ---- 4) 交互录入持仓(输入中文名自动匹配主力合约) ----
    print("")
    print("---- 持仓录入(输入中文品种名称, 自动匹配配置主力合约) ----")
    positions = input_positions()

    # ---- 5) 持仓监控(与扫描信号同一套取数逻辑) ----
    # 持仓合约 = 配置主力合约时, 直接复用扫描已获取的数据(完全一致, 且避免重复请求);
    # 用户指定了其他具体合约(如 V2609)时才重新获取。
    monitor_results = []
    for pos in positions:
        config_code = CONTRACTS.get(pos["name"], {}).get("code")
        try:
            if pos["code"] == config_code and pos["name"] in main_data:
                # 复用扫描数据: 与扫描信号使用完全相同的数据与标注
                dfi, data_source = main_data[pos["name"]]
                print("  [持仓] %s %s: 复用扫描数据, 合约 %s, 数据源 %s"
                      % (pos["name"], direction_cn(pos["direction"]),
                         pos["code"], data_source))
            else:
                # 用户指定了与配置不同的具体合约 -> 重新获取该合约
                dfi, data_source = fetch_for_position(pos)
                print("  [持仓] %s %s: 已获取用户指定合约 %s, 数据源 %s"
                      % (pos["name"], direction_cn(pos["direction"]),
                         pos["code"], data_source))
        except Exception as e:
            monitor_results.append({"name": pos["name"], "error": "数据获取失败: %s" % e})
            print("  [持仓] %s: 数据获取失败, 无法评估 | 合约 %s | %s"
                  % (pos["name"], pos["code"], e))
            continue
        m = monitor_position(pos, dfi)
        m["data_source"] = data_source
        monitor_results.append(m)
        print("  [持仓] %s %s: %s%s" % (
            m["name"], direction_cn(m["direction"]), m["status"],
            (" | " + "；".join(m["reasons"])) if m["reasons"] else ""))

    # ---- 6) 渲染并保存报告 ----
    report = render_report(main_data, trend_data, new_positions, monitor_results,
                           fetch_errors, account_equity, DEFAULT_RISK)
    print("")
    print(report)
    try:
        os.makedirs("reports", exist_ok=True)
        fname = os.path.join("reports",
                             "report_%s.txt" % datetime.now().strftime("%Y-%m-%d"))
        with open(fname, "w", encoding="utf-8") as f:
            f.write(report)
        print("")
        print("[已保存] 报告 -> %s" % fname)
    except Exception as e:
        print("[警告] 报告保存失败: %s" % e)


if __name__ == "__main__":
    main()



