#!/usr/bin/env python3
"""
Global Liquidity Index — Weekly Proxy (CrossBorder Capital-style)
Regenerates global_liquidity_gli_proxy.html from an empty directory.
All sources keyless: DBnomics (Fed H.4.1, H.8), ECB SDMX (ILM, EXR),
BoJ official API (BS01), East Money (China M2).

Usage: python3 rebuild.py [--out DIR] [--name FILE.html]

Requires FRED_API_KEY in the environment (free: https://fredaccount.stlouisfed.org/apikeys).
"""
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (research; contact: acheron-insights)"}
OUT_DIR = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "/mnt/user-data/outputs"
OUT_NAME = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else "global_liquidity_monitor.html"
VERSION = "v7"
OUT_HTML = f"{OUT_DIR}/{OUT_NAME}"


def _get(url, timeout=90, tries=4):
    req = urllib.request.Request(url, headers=UA)
    for k in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            code = getattr(e, "code", None)
            if k == tries - 1 or code in (400, 403, 404):
                raise
            time.sleep(3 * (k + 1))


def fetch_dbnomics(provider, dataset, code):
    """FRB H.4.1 / H.8 via DBnomics mirror. Returns (Series[date->float], last_update_str)."""
    u = f"https://api.db.nomics.world/v22/series/{provider}/{dataset}/{urllib.parse.quote(code)}?observations=1&format=json"
    d = json.loads(_get(u))
    doc = d["series"]["docs"][0]
    s = pd.Series(doc["value"], index=pd.to_datetime(doc["period"]), dtype="float64")
    s = s.replace(-1e15, np.nan).dropna()
    return s.sort_index(), str(doc.get("indexed_at", ""))[:10]


def fetch_ecb_ilm():
    """Eurosystem weekly financial statement, total assets, EUR millions. ISO-week stamped."""
    u = ("https://data-api.ecb.europa.eu/service/data/ILM/W.U2.C.T000000.Z5.Z01"
         "?format=csvdata&startPeriod=2002-12-01")
    rows = _get(u).decode("utf-8").splitlines()
    hdr = rows[0].split(",")
    it, iv = hdr.index("TIME_PERIOD"), hdr.index("OBS_VALUE")
    idx, val = [], []
    for r in rows[1:]:
        c = r.split(",")
        if len(c) <= max(it, iv) or not c[iv]:
            continue
        y, w = c[it].split("-W")
        # Eurosystem WFS reference date is the Friday of the ISO week
        idx.append(dt.date.fromisocalendar(int(y), int(w), 5))
        val.append(float(c[iv]))
    s = pd.Series(val, index=pd.to_datetime(idx)).sort_index()
    return s, str(s.index[-1].date())


def fetch_ecb_fx(cur):
    """ECB daily reference rate: CUR per EUR."""
    u = (f"https://data-api.ecb.europa.eu/service/data/EXR/D.{cur}.EUR.SP00.A"
         "?format=csvdata&startPeriod=1999-01-01")
    rows = _get(u).decode("utf-8").splitlines()
    hdr = rows[0].split(",")
    it, iv = hdr.index("TIME_PERIOD"), hdr.index("OBS_VALUE")
    idx, val = [], []
    for r in rows[1:]:
        c = r.split(",")
        if len(c) <= max(it, iv) or not c[iv]:
            continue
        idx.append(c[it]); val.append(float(c[iv]))
    return pd.Series(val, index=pd.to_datetime(idx)).sort_index()


def fetch_boj_assets():
    """BoJ total assets via official API (Feb 2026). BS01'MABJMTA, monthly, 100mn yen.
    Stamped at month end (average-of-month series; ~1 month publication lag)."""
    u = ("https://www.stat-search.boj.or.jp/api/v1/getDataCode"
         "?format=json&lang=en&db=BS01&startDate=199801&code=MABJMTA")  # no endDate -> full history
    d = json.loads(_get(u))
    rs = d["RESULTSET"][0]
    vals = rs["VALUES"]
    dates = vals["SURVEY_DATES"]
    dkey = [k for k in vals.keys() if k != "SURVEY_DATES"][0]
    obs = vals[dkey]
    idx = [pd.Timestamp(int(str(x)[:4]), int(str(x)[4:6]), 1) + pd.offsets.MonthEnd(0) for x in dates]
    s = pd.Series([float(v) if v not in (None, "") else np.nan for v in obs], index=idx).dropna()
    return s.sort_index(), str(rs.get("LAST_UPDATE", ""))


def fetch_china_m2():
    """China M2 (货币和准货币), East Money, monthly, 100mn CNY. Month-end stamped."""
    u = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
         "?reportName=RPT_ECONOMY_CURRENCY_SUPPLY&columns=ALL&pageSize=1000"
         "&sortColumns=REPORT_DATE&sortTypes=-1")
    d = json.loads(_get(u))
    rows = d["result"]["data"]
    idx = [pd.Timestamp(r["REPORT_DATE"][:10]) + pd.offsets.MonthEnd(0) for r in rows]
    s = pd.Series([float(r["BASIC_CURRENCY"]) for r in rows], index=idx).sort_index()
    return s, str(s.index[-1].date())




# ------------------------------------------------- monthly module (M2 + lead)
FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
if not FRED_KEY:
    sys.exit("FRED_API_KEY is not set. Export it locally, or add it as a repository secret "
             "(Settings -> Secrets and variables -> Actions). Free key: https://fredaccount.stlouisfed.org/apikeys")


def fetch_fred(sid, start="1959-01-01"):
    """FRED monthly/daily series -> Series stamped at obs date (monthlies at period start)."""
    u = (f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
         f"&api_key={FRED_KEY}&file_type=json&observation_start={start}")
    d = json.loads(_get(u))
    idx, val = [], []
    for o in d["observations"]:
        if o["value"] in (".", ""):
            continue
        idx.append(pd.Timestamp(o["date"])); val.append(float(o["value"]))
    return pd.Series(val, index=idx).sort_index()


def fetch_yahoo_daily(tk, start="1990-01-01"):
    """Daily closes. Chunked period1/period2 requests: Yahoo silently degrades
    granularity on very long range= queries, so never use range=max."""
    out = []
    t0 = int(pd.Timestamp(start).timestamp())
    tend = int(pd.Timestamp.now().timestamp())
    step = 8 * 365 * 86400
    while t0 < tend:
        t1 = min(t0 + step, tend)
        u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(tk)}"
             f"?interval=1d&period1={t0}&period2={t1}")
        d = json.loads(_get(u))
        r = d["chart"]["result"][0]
        if r.get("timestamp"):
            idx = [pd.Timestamp(t, unit="s").normalize() for t in r["timestamp"]]
            out.append(pd.Series(r["indicators"]["quote"][0]["close"], index=idx, dtype="float64"))
        t0 = t1
        time.sleep(0.4)
    s = pd.concat(out).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def fetch_boj_m2():
    """Japan M2, SA, avg outstanding (MD02'MAM1XAM2M2MO, from 2003-04), back-spliced
    with the discontinued M2+CDs SA series (MAMS3AAM2CX12, 1998-04\u21922008-04)."""
    u = ("https://www.stat-search.boj.or.jp/api/v1/getDataCode"
         "?format=json&lang=en&db=MD02&startDate=199801&code=MAM1XAM2M2MO,MAMS3AAM2CX12")
    d = json.loads(_get(u))
    ser = {}
    for rs in d["RESULTSET"]:
        vals = rs["VALUES"]
        dk = [k for k in vals.keys() if k != "SURVEY_DATES"][0]
        idx = [pd.Timestamp(int(str(x)[:4]), int(str(x)[4:6]), 1) + pd.offsets.MonthEnd(0) for x in vals["SURVEY_DATES"]]
        ser[rs["SERIES_CODE"]] = pd.Series(
            [float(v) if v not in (None, "") else np.nan for v in vals[dk]], index=idx).dropna().sort_index()
    new, old = ser["MAM1XAM2M2MO"], ser["MAMS3AAM2CX12"]
    ratio = float(new.iloc[0]) / float(old.asof(new.index[0]))
    return pd.concat([old[old.index < new.index[0]] * ratio, new]).sort_index()


def fetch_ecb_bsi_m2():
    """Euro area M2 outstanding, SA, EUR millions."""
    u = ("https://data-api.ecb.europa.eu/service/data/BSI/M.U2.Y.V.M20.X.1.U2.2300.Z01.E"
         "?format=csvdata&startPeriod=1980-01")
    rows = _get(u).decode("utf-8").splitlines()
    hdr = rows[0].split(",")
    it, iv = hdr.index("TIME_PERIOD"), hdr.index("OBS_VALUE")
    idx, val = [], []
    for r in rows[1:]:
        c = r.split(",")
        if len(c) <= max(it, iv) or not c[iv]:
            continue
        idx.append(pd.Timestamp(c[it] + "-01") + pd.offsets.MonthEnd(0)); val.append(float(c[iv]))
    return pd.Series(val, index=idx).sort_index()


def fetch_oecd_bci_us():
    """OECD amplitude-adjusted US business confidence (ISM stand-in), monthly, ~100-centred."""
    u = ("https://api.db.nomics.world/v22/series/OECD/DSD_STES%40DF_CLI/"
         "USA.M.BCICP.IX._Z.AA.IX._Z.H?observations=1&format=json")
    d = json.loads(_get(u))
    doc = d["series"]["docs"][0]
    s = pd.Series(doc["value"], index=pd.PeriodIndex(doc["period"], freq="M").to_timestamp(how="end").normalize(),
                  dtype="float64").replace(-1e15, np.nan).dropna()
    return s.sort_index()


def zexp(s, minp=36):
    mu = s.expanding(minp).mean()
    sd = s.expanding(minp).std()
    return (s - mu) / sd


def lead_scan(z, tgt, kmin=2, kmax=24):
    """Best lead k* (z at t vs target at t+k) and correlation profile."""
    prof = []
    for k in range(kmin, kmax + 1):
        pair = pd.concat([z, tgt.shift(-k)], axis=1).dropna()
        prof.append(round(float(pair.iloc[:, 0].corr(pair.iloc[:, 1])), 3) if len(pair) > 60 else None)
    valid = [(i + kmin, r) for i, r in enumerate(prof) if r is not None]
    if not valid:
        return None, None, prof
    kstar, rstar = max(valid, key=lambda t: t[1])
    return kstar, rstar, prof


def build_monthly(fx_usd_eur, fx_jpy_eur, fx_cny_eur):
    us = fetch_fred("M2SL")                                   # $bn, SA, month start
    us.index = us.index + pd.offsets.MonthEnd(0)
    ea_eur = fetch_ecb_bsi_m2()                               # €mn, SA
    jp = fetch_boj_m2()                                       # ¥100mn, SA
    cn_em, _ = fetch_china_m2()                               # CNY 100mn, from 2008
    cn_fred = fetch_fred("MYAGM2CNM189N", "1996-01-01") / 1e8  # raw CNY -> 100mn
    cn_fred.index = cn_fred.index + pd.offsets.MonthEnd(0)
    first = cn_em.index[0]
    ratio = float(cn_em.iloc[0]) / float(cn_fred.asof(first))
    cn = pd.concat([cn_fred[cn_fred.index < first] * ratio, cn_em]).sort_index()

    dxy = fetch_yahoo_daily("DX-Y.NYB")
    y10d = fetch_fred("DGS10", "1990-01-01")
    y2d = fetch_fred("DGS2", "1990-01-01")
    oil = fetch_fred("WTISPLC", "1990-01-01")
    oil.index = oil.index + pd.offsets.MonthEnd(0)
    bci = fetch_oecd_bci_us()

    end = min(us.index[-1], ea_eur.index[-1], jp.index[-1], cn.index[-1])
    mg = pd.date_range("1996-01-31", end, freq="ME")

    def mlast(s):   # daily -> month-end last, then align
        r = s.resample("ME").last()
        return r.reindex(r.index.union(mg)).ffill().reindex(mg)

    def malign(s):  # monthly month-end stamped -> align
        return s.reindex(s.index.union(mg)).ffill(limit=2).reindex(mg)

    eur = mlast(fx_usd_eur)                       # USD per EUR
    jpyusd = mlast(fx_jpy_eur) / eur              # JPY per USD
    cnyusd = (mlast(fx_cny_eur) / eur).fillna(8.2765)  # pegged pre-2005 ECB history

    m_us = malign(us)
    m_ea = malign(ea_eur) / 1000.0 * eur
    m_jp = malign(jp) * 0.1 / jpyusd
    m_cn = malign(cn) * 0.1 / cnyusd
    glob = m_us + m_ea + m_jp + m_cn                       # $bn
    tgt = (glob / glob.shift(12) - 1) * 100.0

    # FX-constant growth: local YoY weighted by 12m-lagged USD shares
    loc = {"us": malign(us), "ea": malign(ea_eur), "jp": malign(jp), "cn": malign(cn)}
    usd = {"us": m_us, "ea": m_ea, "jp": m_jp, "cn": m_cn}
    fxc = sum((usd[k] / glob).shift(12) * (loc[k] / loc[k].shift(12) - 1) for k in loc) * 100.0

    y2m = y2d.resample("ME").mean().reindex(mg)
    vol = (y10d.diff().resample("ME").std() * np.sqrt(252)).reindex(mg)
    drivers = {
        "US dollar (inv YoY)":        -(mlast(dxy) / mlast(dxy).shift(12) - 1) * 100.0,
        "Bond vol (inv, 10y realised)": -vol,
        "Oil (inv YoY)":              -(malign(oil) / malign(oil).shift(12) - 1) * 100.0,
        "ISM proxy (inv, OECD BCI)":  -malign(bci),
        "2y rate impulse (inv 12m Δ)": -(y2m - y2m.shift(12)),
    }
    dz = {k: zexp(v).clip(-3, 3) for k, v in drivers.items()}

    score, sel = [], []
    for name, z in dz.items():
        k_u, r_u, prof_u = lead_scan(z, tgt)
        _, _, prof_c = lead_scan(z, fxc)
        r_c = prof_c[k_u - 2] if (k_u is not None and 0 <= k_u - 2 < len(prof_c)) else None
        ok = r_u is not None and r_u >= 0.30
        if ok:
            sel.append((name, k_u, r_u))
        if not ok:
            verdict = "Fails r≥0.30"
        elif r_c is not None and r_c >= 0.30:
            verdict = "Validated (both targets)"
        elif r_c is not None and r_c < 0.15:
            verdict = "FX-mechanical"
        else:
            verdict = "Partial"
        score.append({"n": name, "k": k_u, "ru": r_u, "rc": r_c, "v": verdict, "w": 0.0})

    wsum = sum(r for _, _, r in sel)
    wmap = {n: r / wsum for n, _, r in sel}
    for row in score:
        row["w"] = round(wmap.get(row["n"], 0.0) * 100.0, 1)
    comp = sum(dz[n] * w for n, w in wmap.items())
    keff = int(round(sum(wmap[n] * k for n, k, _ in sel)))

    pair = pd.concat([comp, tgt.shift(-keff)], axis=1).dropna()
    b, a = np.polyfit(pair.iloc[:, 0], pair.iloc[:, 1], 1)
    fit_r = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
    resid_sd = float((pair.iloc[:, 1] - (a + b * pair.iloc[:, 0])).std())

    xg = pd.date_range(mg[0], mg[-1] + pd.offsets.MonthEnd(keff), freq="ME")
    implied = (a + b * comp.reindex(xg)).shift(keff)   # comp(t) -> target timing t+k
    fwd = xg > tgt.dropna().index[-1]

    def mser(s, nd=2, ix=mg):
        return [None if (i >= len(s) or pd.isna(s.iloc[i])) else round(float(s.iloc[i]), nd)
                for i in range(len(ix))]

    ml = [d.strftime("%Y-%m") for d in mg]
    xl = [d.strftime("%Y-%m") for d in xg]
    li = mg.get_indexer([tgt.dropna().index[-1]])[0]
    c3 = comp.dropna()
    msnap = {
        "m2_tn": round(float(glob.iloc[-1]) / 1000.0, 1),
        "m2_date": ml[-1],
        "m2_yoy": None if pd.isna(tgt.iloc[li]) else round(float(tgt.iloc[li]), 1),
        "fxc_yoy": None if pd.isna(fxc.iloc[li]) else round(float(fxc.iloc[li]), 1),
        "cn_share": round(float(m_cn.iloc[-1] / glob.iloc[-1]) * 100.0, 1),
        "comp": round(float(c3.iloc[-1]), 2),
        "comp_d3": round(float(c3.iloc[-1] - c3.iloc[-4]), 2) if len(c3) > 3 else None,
        "keff": keff, "fit_r": round(fit_r, 2), "band": round(resid_sd, 1),
        "imp_end": round(float(implied.iloc[-1]), 1) if not pd.isna(implied.iloc[-1]) else None,
        "imp_date": xl[-1],
    }
    return {
        "ml": ml, "xl": xl,
        "m2_us": mser(m_us / 1000.0), "m2_ea": mser(m_ea / 1000.0),
        "m2_jp": mser(m_jp / 1000.0), "m2_cn": mser(m_cn / 1000.0),
        "m2_yoy": mser(tgt, 2), "m2_fxc": mser(fxc, 2),
        "comp": mser(comp, 2),
        "dz": {k: mser(v, 2) for k, v in dz.items()},
        "act_x": [None if pd.isna(v) else round(float(v), 2) for v in tgt.reindex(xg)],
        "imp_x": [None if pd.isna(v) else round(float(v), 2) for v in implied],
        "fwd0": int(np.argmax(fwd)) if fwd.any() else len(xg),
        "score": score, "msnap": msnap,
        "_raw": {"mg": mg, "comp": comp, "tgt": tgt, "keff": keff},
    }


# ---------------------------------------------------------------- transforms
def zroll(s, win=260, minp=156, clip=3.0):
    m = s.rolling(win, min_periods=minp).mean()
    sd = s.rolling(win, min_periods=minp).std()
    return ((s - m) / sd).clip(-clip, clip)


def ann13(s):
    return (s / s.shift(13)) ** 4 - 1


def yoy(s):
    return s / s.shift(52) - 1


def mom_z(s):
    return 0.5 * zroll(ann13(s)) + 0.5 * zroll(yoy(s))


def phi(z):
    return 50.0 * (1.0 + math.erf(z / math.sqrt(2.0))) if not (z is None or np.isnan(z)) else np.nan


def wavg(parts):
    """Weighted mean over available (non-NaN) components, weights renormalized."""
    out = []
    for i in range(len(parts[0][0])):
        num = den = 0.0
        for s, w in parts:
            v = s.iloc[i]
            if not np.isnan(v):
                num += w * v
                den += w
        out.append(num / den if den > 0 else np.nan)
    return pd.Series(out, index=parts[0][0].index)


def main():
    print("Fetching …")
    fed_assets, _ = fetch_dbnomics("FED", "H41", "RESPPMA_N.WW")      # $mn, Wed
    tga, _ = fetch_dbnomics("FED", "H41", "RESPPLLDT_N.WW")           # $mn, Wed
    rrp, _ = fetch_dbnomics("FED", "H41", "RESPPLLRD_N.WW")           # $mn, Wed (domestic 'Others')
    h8, _ = fetch_dbnomics("FED", "H8", "B1001NCBA")                  # $mn, Wed, SA
    ecb_eur, ecb_last = fetch_ecb_ilm()                                # €mn, Fri
    boj_100my, boj_upd = fetch_boj_assets()                            # ¥100mn, M
    cn_100mc, cn_last = fetch_china_m2()                               # CNY 100mn, M
    fx_usd_eur = fetch_ecb_fx("USD")                                   # USD per EUR
    fx_jpy_eur = fetch_ecb_fx("JPY")
    fx_cny_eur = fetch_ecb_fx("CNY")
    mon = build_monthly(fx_usd_eur, fx_jpy_eur, fx_cny_eur)
    print("  fetched.")

    # Weekly Wednesday master grid
    grid = pd.date_range("2003-01-01", fed_assets.index.max(), freq="W-WED")

    def asof(s):
        return s.reindex(s.index.union(grid)).ffill().reindex(grid)

    usd_eur = asof(fx_usd_eur)                    # USD per EUR
    jpy_usd = asof(fx_jpy_eur) / usd_eur          # JPY per USD
    cny_usd = asof(fx_cny_eur) / usd_eur          # CNY per USD

    fed_net = (asof(fed_assets) - asof(tga) - asof(rrp)) / 1000.0        # $bn
    ecb_usd = asof(ecb_eur) / 1000.0 * usd_eur                            # $bn
    boj_usd = asof(boj_100my) * 0.1 / jpy_usd                             # $bn
    cn_usd = asof(cn_100mc) * 0.1 / cny_usd                               # $bn
    h8_bn = asof(h8) / 1000.0                                             # $bn
    g3 = fed_net + ecb_usd + boj_usd                                      # $bn
    tot = g3 + cn_usd                                                     # $bn, Big-4 official + China M2

    # Pillars → composite → 0-100 index
    z_g3, z_cn, z_h8 = mom_z(g3), mom_z(cn_usd), mom_z(h8_bn)
    z_cb = wavg([(z_g3, 0.75), (z_cn, 0.25)])
    z_comp = wavg([(z_cb, 0.70), (z_h8, 0.30)])
    gli = z_comp.map(phi)

    slope = (gli - gli.shift(13)).rolling(4, min_periods=1).mean()
    phase = []
    for g, sl in zip(gli, slope):
        if np.isnan(g) or np.isnan(sl):
            phase.append(None)
        elif g < 50 and sl >= 0:
            phase.append("Rebound")
        elif g >= 50 and sl >= 0:
            phase.append("Calm")
        elif g >= 50:
            phase.append("Speculation")
        else:
            phase.append("Turbulence")

    # USD proxy index (context only; excluded from composite): trade-ish weights
    w = {"EUR": 0.45, "JPY": 0.30, "CNY": 0.25}
    base = grid[grid.searchsorted(pd.Timestamp("2015-01-07"))]
    usdx = 100.0 * np.exp(
        w["EUR"] * np.log((1.0 / usd_eur) / (1.0 / usd_eur[base]))
        + w["JPY"] * np.log(jpy_usd / jpy_usd[base])
        + w["CNY"] * np.log(cny_usd / cny_usd[base]))

    # 52w USD contribution by component (sampled every 13 weeks for readable bars)
    def d52(s):
        return s - s.shift(52)
    csample = grid[::-13][::-1]
    csample = csample[csample >= pd.Timestamp("2011-01-01")]

    def take(s, ix):
        return [None if np.isnan(v) else round(float(v), 1) for v in s.reindex(ix)]

    def ser(s, nd=1):
        return [None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(float(v), nd) for v in s]

    labels = [d.strftime("%Y-%m-%d") for d in grid]
    latest = grid[-1]
    li = -1
    snap = {
        "date": labels[-1],
        "gli": None if np.isnan(gli.iloc[li]) else round(float(gli.iloc[li]), 1),
        "gli_13w": None if np.isnan(gli.iloc[li]) or np.isnan(gli.iloc[li - 13]) else round(float(gli.iloc[li] - gli.iloc[li - 13]), 1),
        "phase": phase[li],
        "g3_tn": round(float(g3.iloc[li]) / 1000.0, 2),
        "g3_yoy": round(float(yoy(g3).iloc[li]) * 100.0, 1),
        "g3_13w_ann": round(float(ann13(g3).iloc[li]) * 100.0, 1),
        "cn_tn": round(float(cn_usd.iloc[li]) / 1000.0, 2),
        "cn_yoy_lcl": round(float(yoy(asof(cn_100mc)).iloc[li]) * 100.0, 1),
        "fed_net_tn": round(float(fed_net.iloc[li]) / 1000.0, 2),
        "tot_tn": None if np.isnan(tot.iloc[li]) else round(float(tot.iloc[li]) / 1000.0, 1),
        "tot_yoy": None if np.isnan(yoy(tot).iloc[li]) else round(float(yoy(tot).iloc[li]) * 100.0, 1),
        "z_comp": None if np.isnan(z_comp.iloc[li]) else round(float(z_comp.iloc[li]), 2),
    }

    sources = [
        {"s": "Fed total assets (H.4.1, Wed level)", "code": "FED/H41 RESPPMA_N.WW", "src": "DBnomics ← Federal Reserve", "cad": "Weekly", "unit": "$mn", "last": str(fed_assets.index[-1].date()), "val": f"${fed_assets.iloc[-1]/1e6:,.2f}tn"},
        {"s": "Treasury General Account (Wed level)", "code": "FED/H41 RESPPLLDT_N.WW", "src": "DBnomics ← Federal Reserve", "cad": "Weekly", "unit": "$mn", "last": str(tga.index[-1].date()), "val": f"${tga.iloc[-1]/1e3:,.0f}bn"},
        {"s": "Reverse repos, domestic (Wed level)", "code": "FED/H41 RESPPLLRD_N.WW", "src": "DBnomics ← Federal Reserve", "cad": "Weekly", "unit": "$mn", "last": str(rrp.index[-1].date()), "val": f"${rrp.iloc[-1]/1e3:,.1f}bn"},
        {"s": "Eurosystem total assets (WFS)", "code": "ILM.W.U2.C.T000000.Z5.Z01", "src": "ECB SDMX", "cad": "Weekly (Fri)", "unit": "€mn", "last": ecb_last, "val": f"€{ecb_eur.iloc[-1]/1e6:,.2f}tn"},
        {"s": "BoJ total assets (accounts, avg)", "code": "BS01 MABJMTA", "src": "BoJ official API", "cad": "Monthly", "unit": "¥100mn", "last": str(boj_100my.index[-1].date()), "val": f"¥{boj_100my.iloc[-1]/1e4:,.0f}tn"},
        {"s": "China M2 (货币和准货币)", "code": "RPT_ECONOMY_CURRENCY_SUPPLY", "src": "East Money ← PBoC", "cad": "Monthly", "unit": "CNY 100mn", "last": cn_last, "val": f"¥{cn_100mc.iloc[-1]/1e4:,.0f}tn"},
        {"s": "US bank credit, all comm. banks SA", "code": "FED/H8 B1001NCBA", "src": "DBnomics ← Federal Reserve", "cad": "Weekly", "unit": "$mn", "last": str(h8.index[-1].date()), "val": f"${h8.iloc[-1]/1e6:,.2f}tn"},
        {"s": "FX reference rates (EUR, JPY, CNY)", "code": "EXR.D.*.EUR.SP00.A", "src": "ECB SDMX", "cad": "Daily", "unit": "per EUR", "last": str(fx_usd_eur.index[-1].date()), "val": f"EURUSD {usd_eur.iloc[-1]:.4f}"},
        {"s": "US M2, SA", "code": "M2SL", "src": "FRED \u2190 Federal Reserve", "cad": "Monthly", "unit": "$bn", "last": mon["msnap"]["m2_date"], "val": "\u2014"},
        {"s": "Euro area M2, SA (outst.)", "code": "BSI.M.U2.Y.V.M20.X.1.U2.2300.Z01.E", "src": "ECB SDMX", "cad": "Monthly", "unit": "\u20acmn", "last": "\u2014", "val": "\u2014"},
        {"s": "Japan M2, SA (avg outst.)", "code": "MD02 MAM1XAM2M2MO", "src": "BoJ official API", "cad": "Monthly", "unit": "\u00a5100mn", "last": "\u2014", "val": "\u2014"},
        {"s": "China M2 pre-2008 splice", "code": "MYAGM2CNM189N", "src": "FRED \u2190 IMF IFS", "cad": "Monthly", "unit": "CNY", "last": "2008 splice", "val": "\u2014"},
        {"s": "US dollar index (DXY)", "code": "DX-Y.NYB", "src": "Yahoo Finance", "cad": "Daily", "unit": "index", "last": "\u2014", "val": "\u2014"},
        {"s": "2-year Treasury yield", "code": "DGS2", "src": "FRED", "cad": "Daily", "unit": "%", "last": "\u2014", "val": "\u2014"},
        {"s": "WTI spot", "code": "WTISPLC", "src": "FRED", "cad": "Monthly", "unit": "$/bbl", "last": "\u2014", "val": "\u2014"},
        {"s": "10y realised vol (\u2190 DGS10)", "code": "DGS10 daily \u0394y \u03c3", "src": "FRED", "cad": "Daily", "unit": "ann. pts", "last": "\u2014", "val": "\u2014"},
        {"s": "ISM stand-in: US bus. confidence", "code": "OECD BCICP (AA)", "src": "DBnomics \u2190 OECD", "cad": "Monthly", "unit": "idx", "last": "\u2014", "val": "\u2014"},
    ]

    data = {
        "labels": labels,
        "gli": ser(gli),
        "phase": phase,
        "z_cb": ser(z_cb, 2), "z_pvt": ser(z_h8, 2), "z_comp": ser(z_comp, 2),
        "g3": ser(g3 / 1000.0, 3), "fed_net": ser(fed_net / 1000.0, 3),
        "ecb": ser(ecb_usd / 1000.0, 3), "boj": ser(boj_usd / 1000.0, 3),
        "cn": ser(cn_usd / 1000.0, 3),
        "fed_assets": ser(asof(fed_assets) / 1e6, 3), "tga": ser(asof(tga) / 1e6, 3), "rrp": ser(asof(rrp) / 1e6, 3),
        "g3_a13": ser(ann13(g3) * 100.0), "g3_yoy": ser(yoy(g3) * 100.0),
        "tot_yoy": ser(yoy(tot) * 100.0),
        "cn_yoy_lcl": ser(yoy(asof(cn_100mc)) * 100.0),
        "h8": ser(h8_bn / 1000.0, 3), "h8_yoy": ser(yoy(h8_bn) * 100.0),
        "usdx": ser(usdx),
        "clabels": [d.strftime("%Y-%m") for d in csample],
        "c_fed": take(d52(fed_net), csample), "c_ecb": take(d52(ecb_usd), csample),
        "c_boj": take(d52(boj_usd), csample), "c_cn": take(d52(cn_usd), csample),
        "snap": snap,
        "sources": sources,
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + f" \u00b7 {VERSION} self-contained",
    }
    raw = mon.pop("_raw")
    data.update(mon)

    # ---- cross-frequency alignment: weekly GLI resampled to monthly grid ----
    mg, cmp_m, tgt_m, keff_m = raw["mg"], raw["comp"], raw["tgt"], raw["keff"]
    gli_me = gli.resample("ME").last()                       # weekly -> month-end
    gli_m = gli_me.reindex(gli_me.index.union(mg)).ffill(limit=1).reindex(mg)
    gli_slope_m = (gli_m - gli_m.shift(3))                    # 3-month change, index points

    def mround(s, nd=2):
        return [None if (i >= len(s) or pd.isna(s.iloc[i])) else round(float(s.iloc[i]), nd)
                for i in range(len(mg))]

    # lead-lag: composite(t) vs M2 YoY(t+k), and GLI(t) vs M2 YoY(t+k)
    def profile(z, tgt, kmin=-12, kmax=24):
        ks, rs = [], []
        for k in range(kmin, kmax + 1):
            pair = pd.concat([z, tgt.shift(-k)], axis=1).dropna()
            ks.append(k)
            rs.append(round(float(pair.iloc[:, 0].corr(pair.iloc[:, 1])), 3) if len(pair) > 60 else None)
        return ks, rs
    kk, r_cmp = profile(cmp_m, tgt_m)
    _, r_gli = profile((gli_m - 50.0), tgt_m)
    best = max([(k, r) for k, r in zip(kk, r_cmp) if r is not None], key=lambda t: t[1])
    best_gli = max([(k, r) for k, r in zip(kk, r_gli) if r is not None], key=lambda t: t[1])

    data["cf"] = {
        "gli_m": mround(gli_m, 1),
        "gli_slope_m": mround(gli_slope_m, 1),
        "kk": kk, "r_cmp": r_cmp, "r_gli": r_gli,
        "kbest_cmp": best[0], "rbest_cmp": best[1],
        "kbest_gli": best_gli[0], "rbest_gli": best_gli[1],
        "keff": keff_m,
    }

    payload = json.dumps(data, separators=(",", ":"))
    html, n = re.subn(r"/\*__DATA__\*/null", lambda m: payload, TEMPLATE, count=1)
    assert n == 1
    # Vendor Chart.js inline: the dashboard must work with no network access
    # (corporate CDN blocks / sandboxed previews / offline). ~196 KB.
    cjs = _get("https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js").decode("utf-8")
    cjs = cjs.replace("</script", "<\\/script")  # defensive; not present in 4.4.1
    html = html.replace("/*__CHARTJS__*/", cjs, 1)
    assert "/*__CHARTJS__*/" not in html
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n=== SANITY {snap['date']} ===")
    print(f"GLI {snap['gli']} ({snap['phase']}), 13wΔ {snap['gli_13w']}  z={snap['z_comp']}")
    print(f"G3 CB liquidity ${snap['g3_tn']}tn  YoY {snap['g3_yoy']}%  13w ann {snap['g3_13w_ann']}%")
    print(f"Global liquidity (G3+CN M2) ${snap['tot_tn']}tn  YoY {snap['tot_yoy']}%")
    print(f"Fed net ${snap['fed_net_tn']}tn | ECB ${ecb_usd.iloc[-1]/1000:.2f}tn | BoJ ${boj_usd.iloc[-1]/1000:.2f}tn | CN M2 ${snap['cn_tn']}tn ({snap['cn_yoy_lcl']}% yoy lcl)")
    ms = mon["msnap"]
    print(f"Global M2 (4-econ) ${ms['m2_tn']}tn  YoY {ms['m2_yoy']}% (FX-const {ms['fxc_yoy']}%)  CN share {ms['cn_share']}%")
    print(f"Lead composite z {ms['comp']} (3m \u0394 {ms['comp_d3']}), k_eff {ms['keff']}m, fit r {ms['fit_r']}, implied {ms['imp_end']}% by {ms['imp_date']} \u00b1{ms['band']}pp")
    for r in mon["score"]:
        print(f"  {r['n']:34s} k*={r['k']:>2}m  rUSD={r['ru']}  rFXc={r['rc']}  w={r['w']}%  {r['v']}")
    cf = data["cf"]
    print(f"Cross-freq lead-lag: composite peaks k={cf['kbest_cmp']}m (r {cf['rbest_cmp']}), GLI peaks k={cf['kbest_gli']}m (r {cf['rbest_gli']})")
    print(f"Wrote {OUT_HTML} ({len(html)/1024:.0f} KB)")


TEMPLATE = None  # replaced below


if __name__ == "__main__" and TEMPLATE:
    main()

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Global Liquidity Index — Weekly Proxy</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script>/*__CHARTJS__*/</script>
<style>
:root{--paper:#F7F1E6;--card:#FDFAF3;--ink:#221C14;--orange:#D2622A;--teal:#0E756C;--ochre:#A67A22;--plum:#7A4A66;--slate:#4E6577;--line:rgba(34,28,20,.14);--muted:rgba(34,28,20,.62)}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:'IBM Plex Sans',sans-serif;font-size:14.5px;line-height:1.55}
.wrap{max-width:1160px;margin:0 auto;padding:26px 20px 60px}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;color:var(--muted);text-transform:uppercase}
h1{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:30px;letter-spacing:-.01em;margin:6px 0 2px}
.sub{color:var(--muted);max-width:760px}
header{display:flex;flex-wrap:wrap;gap:18px;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--ink);padding-bottom:18px}
.stamp{text-align:right}
.stamp .asof{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--muted)}
.bigread{display:flex;gap:10px;align-items:baseline;justify-content:flex-end;margin-top:4px}
.bigread .v{font-family:'Space Grotesk',sans-serif;font-size:44px;font-weight:700;line-height:1}
.pill{font-family:'IBM Plex Mono',monospace;font-size:11.5px;font-weight:500;color:#FDFAF3;padding:4px 10px;border-radius:3px;letter-spacing:.06em;text-transform:uppercase}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin:20px 0 22px;border-bottom:1px solid var(--line)}
.dlbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:-6px 0 22px}
.dlbar .lab{font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-right:2px}
.dlbtn{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:3px;padding:5px 11px;cursor:pointer;transition:background .12s,border-color .12s}
.dlbtn:hover{background:#F1E7D4;border-color:var(--orange)}
.dlbtn:disabled{opacity:.55;cursor:progress}
.rgbar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:-8px 0 20px}
.rgbar .lab{font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-right:2px}
.rgbtn{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:3px;padding:4px 12px;cursor:pointer;transition:background .12s,border-color .12s,color .12s}
.rgbtn:hover{background:#F1E7D4}
.rgbtn.on{background:var(--ink);color:#FDFAF3;border-color:var(--ink)}
.tab{font-family:'Space Grotesk',sans-serif;font-weight:500;font-size:14px;background:none;border:none;color:var(--muted);padding:9px 14px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tab.active{color:var(--ink);border-bottom-color:var(--orange)}
.tab:focus-visible{outline:2px solid var(--orange);outline-offset:2px}
.panel{display:none}.panel.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:10px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.kpi .l{font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.kpi .n{font-family:'IBM Plex Mono',monospace;font-size:22px;font-weight:500;margin-top:4px}
.kpi .d{font-size:11.5px;color:var(--muted);margin-top:2px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:16px 18px 12px;margin-bottom:16px}
.card h3{font-family:'Space Grotesk',sans-serif;font-size:16px;font-weight:700;margin-bottom:2px}
.card .note{font-size:12px;color:var(--muted);margin-bottom:10px}
.ch{position:relative;height:330px}.ch.tall{height:360px}.ch.mid{height:290px}.ch.clock{height:340px}
.grid2{display:grid;grid-template-columns:1.55fr 1fr;gap:16px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}.bigread .v{font-size:36px}}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{font-family:'IBM Plex Mono',monospace;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid var(--ink)}
td{padding:7px 10px 7px 0;border-bottom:1px solid var(--line);vertical-align:top}
td.mono{font-family:'IBM Plex Mono',monospace;font-size:11.5px;white-space:nowrap}
.meth p{max-width:860px;margin-bottom:11px}
.meth h4{font-family:'Space Grotesk',sans-serif;font-size:14.5px;margin:18px 0 7px}
.legendline{display:flex;gap:16px;flex-wrap:wrap;font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--muted);margin:2px 0 8px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
footer{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted);max-width:900px}
.mono{font-family:'IBM Plex Mono',monospace}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <div class="eyebrow">Acheron Insights · Systematic Macro · Weekly</div>
    <h1>Global Liquidity Monitor</h1>
    <div class="sub">Weekly CrossBorder-style GLI proxy (0–100, mean 50) alongside the four-economy global M2 aggregate and its lead-indicator composite — the full pipeline from conditions to base liquidity to broad money.</div>
  </div>
  <div class="stamp">
    <div class="asof" id="asof"></div>
    <div class="bigread"><span class="v" id="gliBig"></span><span class="pill" id="phasePill"></span></div>
  </div>
</header>

<nav class="tabs" role="tablist">
  <button class="tab active" data-t="p1">Headline</button>
  <button class="tab" data-t="p2">Central banks</button>
  <button class="tab" data-t="p3">Momentum &amp; pillars</button>
  <button class="tab" data-t="p4">China &amp; the dollar</button>
  <button class="tab" data-t="p6">Global M2</button>
  <button class="tab" data-t="p7">Lead index</button>
  <button class="tab" data-t="p8">Cross-frequency</button>
  <button class="tab" data-t="p5">Methodology</button>
</nav>

<div class="dlbar">
  <span class="lab">Download</span>
  <button class="dlbtn" id="dlWeekly">Weekly CSV</button>
  <button class="dlbtn" id="dlMonthly">Monthly CSV</button>
  <button class="dlbtn" id="dlXlsx">Excel workbook</button>
</div>

<div class="rgbar">
  <span class="lab">Range</span>
  <button class="rgbtn on" data-r="all">Inception</button>
  <button class="rgbtn" data-r="5y">5Y</button>
  <button class="rgbtn" data-r="2y">2Y</button>
  <button class="rgbtn" data-r="1y">1Y</button>
</div>

<section class="panel active" id="p1">
  <div class="cards" id="kpis"></div>
  <div class="grid2">
    <div class="card">
      <h3>Global Liquidity Index (proxy)</h3>
      <div class="note">Weekly, 0–100 normalised. Ribbon shades the liquidity-cycle phase; dashed line marks the 50 mean.</div>
      <div class="ch tall"><canvas id="cGli"></canvas></div>
      <div class="legendline" id="phaseLegend"></div>
    </div>
    <div class="card">
      <h3>Liquidity cycle clock</h3>
      <div class="note">Index level vs 13-week change, trailing 2 years. Cycle runs anticlockwise from Rebound.</div>
      <div class="ch clock"><canvas id="cClock"></canvas></div>
    </div>
  </div>
  <div class="card">
    <h3>Global liquidity growth, % YoY</h3>
    <div class="note">USD-converted, 52-week change. Global liquidity = Fed net + Eurosystem + BoJ + China M2; US bank credit shown for the private pillar. Level-weighted, so China's broad-money stock (~3/4 of the sum) dominates — the composite index instead uses fixed 75/25 pillar weights. FX valuation effects are embedded.</div>
    <div class="ch mid"><canvas id="cYoY"></canvas></div>
  </div>
</section>

<section class="panel" id="p2">
  <div class="card">
    <h3>Big-4 central bank liquidity, USD terms</h3>
    <div class="note">G3 aggregate = Fed net liquidity + Eurosystem + BoJ total assets, converted at spot. China tracked separately via M2 (see Methodology).</div>
    <div class="ch"><canvas id="cCb"></canvas></div>
  </div>
  <div class="card">
    <h3>Fed net liquidity decomposition</h3>
    <div class="note">Net = total assets − Treasury General Account − domestic reverse repos (H.4.1 Wednesday levels).</div>
    <div class="ch mid"><canvas id="cFed"></canvas></div>
  </div>
  <div class="card">
    <h3>52-week change in USD liquidity, by source</h3>
    <div class="note">Includes FX valuation effects — a weaker dollar mechanically expands non-US liquidity in USD terms, which is the CrossBorder transmission channel.</div>
    <div class="ch mid"><canvas id="cContrib"></canvas></div>
  </div>
</section>

<section class="panel" id="p3">
  <div class="card">
    <h3>G3 central bank liquidity momentum</h3>
    <div class="note">13-week annualised vs 52-week growth of the USD aggregate.</div>
    <div class="ch"><canvas id="cMom"></canvas></div>
  </div>
  <div class="card">
    <h3>Pillar z-scores</h3>
    <div class="note">Rolling 5-year z-scores of blended momentum. Composite = 0.70 × central-bank pillar + 0.30 × private-credit pillar.</div>
    <div class="ch"><canvas id="cZ"></canvas></div>
  </div>
</section>

<section class="panel" id="p4">
  <div class="card">
    <h3>China M2</h3>
    <div class="note">USD-converted level (left) and local-currency YoY (right). Enters the composite at 25% of the central-bank pillar.</div>
    <div class="ch"><canvas id="cCn"></canvas></div>
  </div>
  <div class="card">
    <h3>The dollar valve — context only</h3>
    <div class="note">Proxy USD index (45% EUR / 30% JPY / 25% CNY, Jan 2015 = 100), axis inverted so dollar weakness plots upward alongside the GLI. Excluded from the composite: FX conversion of the aggregates already carries this channel.</div>
    <div class="ch"><canvas id="cUsd"></canvas></div>
  </div>
</section>

<section class="panel" id="p6">
  <div class="card">
    <h3>Global M2 — US + euro area + Japan + China, USD terms</h3>
    <div class="note">Stacked broad-money levels at spot FX. US M2SL and China via East Money (FRED splice pre-2008); Japan and the euro area now via the BoJ official API and ECB BSI. <span id="m2note"></span></div>
    <div class="ch tall"><canvas id="cM2L"></canvas></div>
  </div>
  <div class="card">
    <h3>Global M2 growth — USD terms vs FX-constant</h3>
    <div class="note">The gap between the two lines is the dollar-denominator effect: USD-terms growth embeds currency revaluation, FX-constant weights local-currency growth by lagged USD shares. The lead index targets the USD-terms series; the FX-constant series is the acid test for whether a driver is monetary or mechanical.</div>
    <div class="ch mid"><canvas id="cM2Y"></canvas></div>
  </div>
</section>

<section class="panel" id="p7">
  <div class="card">
    <h3>Implied path — global M2 YoY vs lead composite</h3>
    <div class="note" id="leadnote"></div>
    <div class="ch tall"><canvas id="cLead"></canvas></div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3>Driver scorecard</h3>
      <div class="note">Each (inverted) driver scanned for its best lead k* in 2–24m vs USD-terms M2 growth; selection requires r ≥ 0.30, weights ∝ r. The FX-constant column is the same-lead correlation vs currency-neutral growth — separating genuine monetary transmission from the dollar-denominator effect.</div>
      <div style="overflow-x:auto"><table id="scoreT"></table></div>
      <div class="note" style="margin-top:8px"><a href="#" id="csvDl">Download monthly panel (CSV)</a> — M2 components, growth rates, driver z-scores, composite.</div>
    </div>
    <div class="card">
      <h3>Composite lead index (z)</h3>
      <div class="note">Weighted sum of expanding-window z-scores at native timing. Above zero → liquidity tailwind building; below zero → headwind. Weights and the OLS mapping re-estimate on each rebuild.</div>
      <div class="ch mid"><canvas id="cComp"></canvas></div>
    </div>
  </div>
</section>

<section class="panel" id="p8">
  <div class="card">
    <h3>The transmission chain on one timeline</h3>
    <div class="note" id="cfnote"></div>
    <div class="ch tall"><canvas id="cCF"></canvas></div>
    <div class="note" style="margin-top:6px">Weekly GLI resampled to month-end (left axis, 0–100). Composite lead index and global M2 growth on the right axes. All three at monthly resolution: the point is to see conditions, base liquidity and broad money move together — and in what order.</div>
  </div>
  <div class="grid2">
    <div class="card">
      <h3>Lead–lag structure</h3>
      <div class="note">Correlation of each upstream measure at month <i>t</i> with global M2 growth at <i>t+k</i>. Peak to the right of zero means the measure leads M2. The composite is engineered to lead; the GLI proxy is shown for comparison.</div>
      <div class="ch mid"><canvas id="cLL"></canvas></div>
    </div>
    <div class="card">
      <h3>Reading the chain</h3>
      <div class="note" id="cfread"></div>
    </div>
  </div>
</section>

<section class="panel" id="p5">
  <div class="card meth">
    <h3>Construction</h3>
    <p>The proxy replicates the observable core of CrossBorder Capital's Global Liquidity Index: a weekly, normalised (0–100, mean 50) reading of funding-liquidity conditions. Two pillars are computed on a weekly Wednesday grid. The <b>central-bank pillar</b> blends the G3 USD aggregate (Fed total assets less TGA and domestic reverse repos, plus Eurosystem and BoJ total assets at spot FX, 75%) with China M2 in USD (25%). The <b>private-credit pillar</b> uses US commercial-bank credit (H.8). For each input, momentum is measured as the equal blend of the 13-week annualised and 52-week growth rates; each is standardised on a rolling 260-week window (156-week minimum, clipped at ±3σ). The composite z (0.70 / 0.30 pillar weights) maps to the index through the normal CDF: GLI = 100·Φ(z). Phases: <b>Rebound</b> &lt;50 &amp; rising, <b>Calm</b> ≥50 &amp; rising, <b>Speculation</b> ≥50 &amp; falling, <b>Turbulence</b> &lt;50 &amp; falling (13-week slope, 4-week smoothed).</p>
    <h4>Honest divergences from the real GLI</h4>
    <p>CrossBorder measure liquidity across ~90 economies from flow-of-funds data, including shadow-bank funding, collateral capacity and cross-border flows; their weights and normalisation are proprietary. This proxy covers the Big-4 official balance sheets plus one US private-credit series, so it tracks the policy-driven core of global liquidity, not the full private perimeter. China enters via M2 rather than the PBoC balance sheet deliberately: the PBoC eases through RRR cuts and lending facilities that barely move its asset totals, so M2 is the less-misleading quantity. The dollar is not a separate pillar because spot-FX conversion of the aggregates already embeds it; the inverted-USD overlay is diagnostic only. Monthly inputs (BoJ, China M2) are forward-filled and carry roughly four to six weeks of publication lag at the margin. The composite runs from December 2006 (G3 + US bank credit, once the 3-year z window fills); China M2 enters the central-bank pillar from ~2013, with weights renormalised over available components before then. G3 levels run from 2003.</p>
    <h4 style="margin:14px 0 6px">Global M2 &amp; lead index (monthly module)</h4>
    <p>The <b>global M2 aggregate</b> sums US (M2SL), euro area (ECB BSI, SA), Japan (BoJ MD02, SA; M2+CDs back-splice pre-2003) and China (East Money, FRED IFS splice pre-2008) broad money at spot USD, monthly from 1996; growth is shown both in USD terms and FX-constant (local-currency growth weighted by 12-month-lagged USD shares). The <b>lead index</b> re-tests five inverted candidate drivers \u2014 the dollar (DXY YoY), 10-year realised bond volatility, oil YoY, an ISM stand-in, and the 12-month change in the 2-year yield \u2014 each as an expanding-window z-score (36-month minimum, \u00b13\u03c3 clip), scanned for its best lead in 2\u201324 months against USD-terms M2 growth. Drivers clearing r \u2265 0.30 enter the composite with weights proportional to r; the composite is mapped to growth units by full-sample OLS at the weight-averaged effective lead and projected forward with a \u00b11\u03c3 residual band. Weights, leads and the mapping re-estimate on every rebuild \u2014 the index is adaptive but not frozen, so prints drift across publications; z-scores are expanding (no look-ahead) but selection and OLS are full-sample. ISM itself is proprietary and its free mirror blocks datacenter IPs, so the OECD amplitude-adjusted US business-confidence indicator stands in \u2014 a documented substitution that can shift that driver's score relative to earlier runs. The weekly GLI proxy and the monthly module share FX sources but are otherwise independent measurements: conditions (lead index) \u2192 base liquidity (GLI) \u2192 broad money (global M2).</p>
    <h4 style="margin:14px 0 6px">Cross-frequency alignment</h4>
    <p>The Cross-frequency tab places all three on a monthly grid: the weekly GLI is resampled to month-end (last observation, forward-filled at most one month), while the composite and M2 growth are already monthly. The lead\u2013lag panel correlates each upstream measure at month <i>t</i> against global M2 growth at <i>t+k</i> over k \u2208 [\u221212, +24]; the peak\u2019s position is where that measure leads M2. These are full-sample contemporaneous correlations for description \u2014 not the out-of-sample forecast, which lives on the Lead index tab. Resampling weekly to monthly discards intra-month timing, so the GLI\u2019s apparent lead is a lower bound on its true weekly resolution.</p>
    <h4>Sources (all keyless, fetched at build)</h4>
    <div style="overflow-x:auto"><table id="srcTable"></table></div>
    <h4>Rebuild</h4>
    <p class="mono" style="font-size:12px">python3 rebuild.py &nbsp;→&nbsp; refetches every series and regenerates this file. Chart.js is vendored inline, so the dashboard renders fully offline; only the Excel export and web fonts touch the network. Built <span id="built"></span>.</p>
  </div>
</section>

<footer>Research reconstruction for internal use. This is not CrossBorder Capital's index, and nothing here is investment advice. Figures embed source revisions and FX valuation effects; verify against primary releases before relying on any single print.</footer>
</div>

<div id="errbar" style="display:none;position:fixed;left:0;right:0;bottom:0;z-index:99;background:#D2622A;color:#FDFAF3;font-family:'IBM Plex Mono',monospace;font-size:12px;padding:8px 16px" role="alert"></div>
<script>
window.addEventListener('error',function(e){try{var el=document.getElementById('errbar');if(!el)return;el.style.display='block';el.textContent='Dashboard runtime error: '+(e.message||'unknown')+(e.filename?'  ['+String(e.filename).split('/').pop()+':'+e.lineno+']':'')+'  — please report this line.';}catch(_){}});
</script>
<script>
const D=/*__DATA__*/null;
const C={paper:'#F7F1E6',card:'#FDFAF3',ink:'#221C14',orange:'#D2622A',teal:'#0E756C',ochre:'#A67A22',plum:'#7A4A66',slate:'#4E6577'};
const PHC={Rebound:'#0E756C',Calm:'#4E6577',Speculation:'#A67A22',Turbulence:'#D2622A'};
Chart.defaults.font.family="'IBM Plex Mono',monospace";
Chart.defaults.font.size=10.5;
Chart.defaults.color='rgba(34,28,20,.62)';
Chart.defaults.borderColor='rgba(34,28,20,.08)';
Chart.defaults.animation=false; /* deterministic rendering: the animation interpolator (this._fn) fails on charts constructed in hidden/zero-sized containers (e.g. sandboxed previews); a data dashboard needs no easing */

function yearTicks(labels,step){step=step||2;return function(value){const labs=this.chart.data.labels;const lab=labs[value];if(lab==null)return '';const y=String(lab).slice(0,4);const eff=(RANGE==='1y'||RANGE==='2y')?1:step;if((+y)%eff!==0)return '';const prev=value>0?labs[value-1]:null;return (prev==null||String(prev).slice(0,4)!==y)?y:'';};}
function line(ds,extra){return Object.assign({borderWidth:1.8,pointRadius:0,pointHitRadius:6,tension:0,spanGaps:false},ds,extra||{});}
const tipTitle={title:it=>it.length?D.labels[it[0].dataIndex]:''};

// midline + phase ribbon plugin (headline chart)
const gliPlugins=[{id:'mid',afterDraw(ch){const y=ch.scales.y.getPixelForValue(50),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.45)';x.setLineDash([4,4]);x.lineWidth=1;x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}},
{id:'ribbon',afterDraw(ch){const a=ch.chartArea,x=ch.ctx,sc=ch.scales.x;x.save();for(let i=0;i<D.phase.length;i++){const p=D.phase[i];if(!p)continue;const x0=sc.getPixelForValue(Math.max(i-.5,0)),x1=sc.getPixelForValue(Math.min(i+.5,D.phase.length-1));x.fillStyle=PHC[p]+'59';x.fillRect(x0,a.bottom+4,Math.max(x1-x0,1),7);}x.restore();}}];

let RANGE='all';
const REG=[];
function basisOf(cfg){const L=cfg&&cfg.data&&cfg.data.labels;if(L===D.labels)return 'w';if(L===D.ml)return 'm';if(L===D.xl)return 'x';return null;}
const WIN={w:{'5y':261,'2y':105,'1y':53},m:{'5y':61,'2y':25,'1y':13},x:{'5y':61,'2y':25,'1y':13}};
function applyOne(r){
  if(RANGE==='all'){r.ch.data.labels=r.labels;r.ch.data.datasets.forEach((d,i)=>{d.data=r.dss[i];});}
  else{const n=WIN[r.basis][RANGE];const L=r.labels.length;let start=Math.max(0,L-n);
    if(r.basis==='x'){const fwd=L-(D.fwd0||L);start=Math.max(0,L-n-fwd);}
    r.ch.data.labels=r.labels.slice(start);r.ch.data.datasets.forEach((d,i)=>{d.data=r.dss[i].slice(start);});}
  r.ch.update('none');
}
function applyRange(){REG.forEach(applyOne);}
function mk(id,cfg){const el=document.getElementById(id);const ch=new Chart(el.getContext('2d'),cfg);
  const basis=basisOf(cfg);
  if(basis){const rec={ch,basis,labels:cfg.data.labels.slice(),dss:cfg.data.datasets.map(d=>d.data.slice())};REG.push(rec);if(RANGE!=='all')applyOne(rec);}
  return ch;}
const made={};
function build(id,fn){if(made[id])return;made[id]=1;fn();}
function resizeActive(){document.querySelectorAll('.panel.active canvas').forEach(c=>{const ch=Chart.getChart(c);if(ch)ch.resize();});}

// ---------- headline
function pHead(){
build('cGli',()=>mk('cGli',{type:'line',plugins:gliPlugins,data:{labels:D.labels,datasets:[line({label:'GLI proxy',data:D.gli,borderColor:C.orange,borderWidth:2.2})]},
options:{responsive:true,maintainAspectRatio:false,layout:{padding:{bottom:14}},interaction:{mode:'index',intersect:false},
plugins:{legend:{display:false},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' GLI '+c.parsed.y+' · '+(D.phase[c.dataIndex]||'')})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{min:0,max:100,ticks:{stepSize:25}}}}}));
build('cYoY',()=>mk('cYoY',{type:'line',plugins:[{id:'z0h',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.labels,datasets:[
line({label:'Global liquidity (G3 + China M2)',data:D.tot_yoy,borderColor:C.orange,borderWidth:2.2}),
line({label:'G3 central banks',data:D.g3_yoy,borderColor:'rgba(34,28,20,.75)',borderWidth:1.4}),
line({label:'US bank credit (H.8)',data:D.h8_yoy,borderColor:C.teal,borderWidth:1.4})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' '+c.dataset.label+': '+c.parsed.y+'%'})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'% YoY'}}}}}));
build('cClock',()=>{const n=D.gli.length,tr=[];for(let i=Math.max(14,n-104);i<n;i++){if(D.gli[i]==null||D.gli[i-13]==null)continue;tr.push({x:+(D.gli[i]-D.gli[i-13]).toFixed(1),y:D.gli[i],i});}
const cols=tr.map((p,k)=>{const a=.12+.78*(k/(tr.length-1||1));return 'rgba(34,28,20,'+a.toFixed(2)+')';});
if(tr.length){cols[cols.length-1]=C.orange;}
mk('cClock',{type:'scatter',plugins:[{id:'quad',beforeDraw(ch){const a=ch.chartArea,x=ch.ctx,x0=ch.scales.x.getPixelForValue(0),y0=ch.scales.y.getPixelForValue(50);
x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.lineWidth=1;
x.beginPath();x.moveTo(x0,a.top);x.lineTo(x0,a.bottom);x.moveTo(a.left,y0);x.lineTo(a.right,y0);x.stroke();
x.setLineDash([]);x.font="10px 'IBM Plex Mono'";
const lab=(t,px,py,c,al)=>{x.fillStyle=c;x.textAlign=al;x.fillText(t,px,py);};
lab('REBOUND',x0-8,a.bottom-8,PHC.Rebound,'right');lab('CALM',x0+8,a.top+14,PHC.Calm,'left');
lab('SPECULATION',x0-8,a.top+14,PHC.Speculation,'right');lab('TURBULENCE',x0+8,a.bottom-8,PHC.Turbulence,'left');
// mislabeled quadrants guard: rising (x>0) right side => Rebound bottom-right, Calm top-right
x.restore();}}],
data:{datasets:[{label:'trail',data:tr,pointRadius:tr.map((_,k)=>k===tr.length-1?5:2.6),pointBackgroundColor:cols,pointBorderWidth:0,showLine:true,borderColor:'rgba(34,28,20,.18)',borderWidth:1}]},
options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>{const p=c.raw;return D.labels[p.i]+' · GLI '+p.y+' · 13wΔ '+p.x;}}}},
scales:{x:{title:{display:true,text:'13-week change (pts)'},suggestedMin:-25,suggestedMax:25},y:{min:0,max:100,title:{display:true,text:'GLI level'}}}}});});
}

// ---------- central banks
function pCb(){
build('cCb',()=>mk('cCb',{type:'line',data:{labels:D.labels,datasets:[
line({label:'G3 aggregate',data:D.g3,borderColor:C.ink,borderWidth:2.4}),
line({label:'Fed net',data:D.fed_net,borderColor:C.slate}),
line({label:'Eurosystem',data:D.ecb,borderColor:C.teal}),
line({label:'BoJ',data:D.boj,borderColor:C.ochre}),
line({label:'China M2 (memo)',data:D.cn,borderColor:C.plum,borderDash:[5,3],borderWidth:1.4})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' '+c.dataset.label+': $'+c.parsed.y.toFixed(2)+'tn'})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'USD tn'}}}}}));
build('cFed',()=>mk('cFed',{type:'line',data:{labels:D.labels,datasets:[
line({label:'Fed total assets',data:D.fed_assets,borderColor:C.slate}),
line({label:'TGA',data:D.tga,borderColor:C.ochre,borderWidth:1.4}),
line({label:'Domestic RRP',data:D.rrp,borderColor:C.plum,borderWidth:1.4}),
line({label:'Net liquidity',data:D.fed_net,borderColor:C.orange,borderWidth:2.2})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' '+c.dataset.label+': $'+c.parsed.y.toFixed(2)+'tn'})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'USD tn'}}}}}));
build('cContrib',()=>mk('cContrib',{type:'bar',data:{labels:D.clabels,datasets:[
{label:'Fed net',data:D.c_fed,backgroundColor:C.slate,stack:'s'},
{label:'Eurosystem',data:D.c_ecb,backgroundColor:C.teal,stack:'s'},
{label:'BoJ',data:D.c_boj,backgroundColor:C.ochre,stack:'s'},
{label:'China M2',data:D.c_cn,backgroundColor:C.plum,stack:'s'}]},
options:{responsive:true,maintainAspectRatio:false,
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:{label:c=>' '+c.dataset.label+': '+(c.parsed.y>=0?'+':'')+c.parsed.y+'bn'}}},
scales:{x:{stacked:true,ticks:{maxRotation:0,autoSkip:true,maxTicksLimit:12}},y:{stacked:true,title:{display:true,text:'USD bn, 52w Δ'}}}}}));
}

// ---------- momentum & pillars
function pMom(){
build('cMom',()=>mk('cMom',{type:'line',plugins:[{id:'z0',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.labels,datasets:[
line({label:'13w annualised',data:D.g3_a13,borderColor:C.orange}),
line({label:'52w',data:D.g3_yoy,borderColor:C.teal})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' '+c.dataset.label+': '+c.parsed.y+'%'})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'% growth'}}}}}));
build('cZ',()=>mk('cZ',{type:'line',plugins:[{id:'z0b',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.labels,datasets:[
line({label:'Central-bank pillar',data:D.z_cb,borderColor:C.teal}),
line({label:'Private-credit pillar',data:D.z_pvt,borderColor:C.plum}),
line({label:'Composite z',data:D.z_comp,borderColor:C.orange,borderWidth:2.2})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:Object.assign({},tipTitle,{label:c=>' '+c.dataset.label+': '+c.parsed.y+'σ'})}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{suggestedMin:-3,suggestedMax:3,title:{display:true,text:'z-score'}}}}}));
}

// ---------- china & dollar
function pCn(){
build('cCn',()=>mk('cCn',{type:'line',data:{labels:D.labels,datasets:[
line({label:'China M2, USD tn',data:D.cn,borderColor:C.plum,borderWidth:2.2,yAxisID:'y'}),
line({label:'YoY %, local ccy',data:D.cn_yoy_lcl,borderColor:C.ochre,borderWidth:1.4,yAxisID:'y2'})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:tipTitle}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'USD tn'}},y2:{position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'% YoY'}}}}}));
build('cUsd',()=>mk('cUsd',{type:'line',data:{labels:D.labels,datasets:[
line({label:'USD proxy index (inverted)',data:D.usdx,borderColor:C.slate,yAxisID:'y'}),
line({label:'GLI proxy',data:D.gli,borderColor:C.orange,borderWidth:2,yAxisID:'y2'})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:tipTitle}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.labels),autoSkip:false,maxRotation:0}},y:{reverse:true,title:{display:true,text:'USD index (inverted)'}},y2:{position:'right',min:0,max:100,grid:{drawOnChartArea:false},title:{display:true,text:'GLI'}}}}}));
}

// ---------- header, KPIs, sources
(function init(){
const s=D.snap;
document.getElementById('asof').textContent='Week of '+s.date+' · built '+D.built;
document.getElementById('gliBig').textContent=s.gli==null?'—':s.gli.toFixed(1);
const pill=document.getElementById('phasePill');pill.textContent=s.phase||'—';pill.style.background=PHC[s.phase]||C.slate;
const f=(v,suf,pre)=>v==null?'—':(pre||'')+v.toLocaleString()+(suf||'');
const kpis=[
['GLI proxy',s.gli==null?'—':s.gli.toFixed(1),'composite z '+f(s.z_comp,'σ')],
['13-week change',f(s.gli_13w,' pts'),'index points'],
['Global liquidity',f(s.tot_tn,'tn','$'),f(s.tot_yoy,'% YoY')+' · G3 + China M2'],
['G3 CB liquidity',f(s.g3_tn,'tn','$'),f(s.g3_yoy,'% YoY')+' · '+f(s.g3_13w_ann,'%')+' 13w ann.'],
['Fed net liquidity',f(s.fed_net_tn,'tn','$'),'assets − TGA − RRP'],
['China M2',f(s.cn_tn,'tn','$'),f(s.cn_yoy_lcl,'% YoY')+' local ccy']];
document.getElementById('kpis').innerHTML=kpis.map(k=>'<div class="kpi"><div class="l">'+k[0]+'</div><div class="n">'+k[1]+'</div><div class="d">'+k[2]+'</div></div>').join('');
document.getElementById('phaseLegend').innerHTML=Object.keys(PHC).map(p=>'<span><span class="sw" style="background:'+PHC[p]+'"></span>'+p+'</span>').join('');
document.getElementById('srcTable').innerHTML='<tr><th>Series</th><th>Code</th><th>Source</th><th>Cadence</th><th>Last obs</th><th>Latest</th></tr>'+
D.sources.map(r=>'<tr><td>'+r.s+'</td><td class="mono">'+r.code+'</td><td>'+r.src+'</td><td class="mono">'+r.cad+'</td><td class="mono">'+r.last+'</td><td class="mono">'+r.val+'</td></tr>').join('');
document.getElementById('built').textContent=D.built;
function pM2(){
const fillc=(hex,a)=>hex+a;
build('cM2L',()=>mk('cM2L',{type:'line',data:{labels:D.ml,datasets:[
line({label:'US',data:D.m2_us,borderColor:C.slate,backgroundColor:fillc(C.slate,'66'),fill:true,borderWidth:1}),
line({label:'Euro area',data:D.m2_ea,borderColor:C.teal,backgroundColor:fillc(C.teal,'66'),fill:true,borderWidth:1}),
line({label:'Japan',data:D.m2_jp,borderColor:C.ochre,backgroundColor:fillc(C.ochre,'66'),fill:true,borderWidth:1}),
line({label:'China',data:D.m2_cn,borderColor:C.plum,backgroundColor:fillc(C.plum,'66'),fill:true,borderWidth:1})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:{title:it=>it.length?D.ml[it[0].dataIndex]:'',label:c=>' '+c.dataset.label+': $'+c.parsed.y.toFixed(1)+'tn'}}},
scales:{x:{stacked:true,grid:{display:false},ticks:{callback:yearTicks(D.ml),autoSkip:false,maxRotation:0}},y:{stacked:true,title:{display:true,text:'USD tn'}}}}}));
build('cM2Y',()=>mk('cM2Y',{type:'line',plugins:[{id:'z0m',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.ml,datasets:[
line({label:'USD terms',data:D.m2_yoy,borderColor:C.orange,borderWidth:2.2}),
line({label:'FX-constant',data:D.m2_fxc,borderColor:'rgba(34,28,20,.75)',borderWidth:1.4})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:{title:it=>it.length?D.ml[it[0].dataIndex]:'',label:c=>' '+c.dataset.label+': '+c.parsed.y+'%'}}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.ml),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'% YoY'}}}}}));
const m=D.msnap;
document.getElementById('m2note').textContent='Latest: $'+m.m2_tn+'tn ('+m.m2_date+'), China '+m.cn_share+'% of the aggregate.';
}
function pLead(){
const m=D.msnap;
document.getElementById('leadnote').innerHTML='Composite shifted forward by its effective lead of <b>'+m.keff+' months</b> and mapped to growth units by OLS (fit r '+m.fit_r+'). Dashed segment is beyond the last M2 print; shaded band is \u00b11\u03c3 of mapping residuals (\u00b1'+m.band+'pp) \u2014 direction of travel, not a point forecast. Latest implied: <b>'+(m.imp_end==null?'\u2014':m.imp_end+'%')+'</b> by '+m.imp_date+', from '+(m.m2_yoy==null?'\u2014':m.m2_yoy+'%')+' now.';
build('cLead',()=>{const f0=D.fwd0;
const impIn=D.imp_x.map((v,i)=>i<f0?v:null), impFwd=D.imp_x.map((v,i)=>i>=f0-1?v:null);
const hi=D.imp_x.map((v,i)=>v==null||i<f0-1?null:+(v+m.band).toFixed(2)), lo=D.imp_x.map((v,i)=>v==null||i<f0-1?null:+(v-m.band).toFixed(2));
mk('cLead',{type:'line',plugins:[{id:'z0l',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.xl,datasets:[
line({label:'Global M2 YoY (actual)',data:D.act_x,borderColor:C.orange,borderWidth:2.2}),
line({label:'Composite-implied (in-sample)',data:impIn,borderColor:'rgba(34,28,20,.55)',borderWidth:1.2}),
line({label:'Implied path (forward)',data:impFwd,borderColor:'rgba(34,28,20,.9)',borderWidth:1.8,borderDash:[5,4]}),
line({label:'+1\u03c3',data:hi,borderColor:'rgba(0,0,0,0)',backgroundColor:'rgba(210,98,42,.14)',fill:'+1',pointRadius:0}),
line({label:'\u22121\u03c3',data:lo,borderColor:'rgba(0,0,0,0)',pointRadius:0})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14,filter:i=>!/\u03c3/.test(i.text)}},tooltip:{callbacks:{title:it=>it.length?D.xl[it[0].dataIndex]:'',label:c=>/\u03c3/.test(c.dataset.label)?null:' '+c.dataset.label+': '+c.parsed.y+'%'}}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.xl),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'% YoY'}}}}});});
build('cComp',()=>mk('cComp',{type:'line',plugins:[{id:'z0c',afterDraw(ch){const y=ch.scales.y.getPixelForValue(0),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.4)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.ml,datasets:[line({label:'Composite lead index',data:D.comp,borderColor:C.orange,borderWidth:2})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{display:false},tooltip:{callbacks:{title:it=>it.length?D.ml[it[0].dataIndex]:'',label:c=>' z '+c.parsed.y}}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.ml,4),autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'z'}}}}}));
if(!made.scoreT){made.scoreT=1;
document.getElementById('scoreT').innerHTML='<tr><th>Driver</th><th>k*</th><th>r USD</th><th>r FX-const</th><th>Weight</th><th>Verdict</th></tr>'+
D.score.map(r=>'<tr><td>'+r.n+'</td><td class="mono">'+(r.k==null?'\u2014':r.k+'m')+'</td><td class="mono">'+(r.ru==null?'\u2014':r.ru.toFixed(2))+'</td><td class="mono">'+(r.rc==null?'\u2014':r.rc.toFixed(2))+'</td><td class="mono">'+(r.w?r.w+'%':'\u2014')+'</td><td>'+r.v+'</td></tr>').join('');
document.getElementById('csvDl').addEventListener('click',e=>{e.preventDefault();dlMonthlyCsv();});}
}
function pCF(){
const cf=D.cf,m=D.msnap;
document.getElementById('cfnote').innerHTML='Three measurements, one chain: funding <b>conditions</b> (composite lead index) \u2192 <b>base liquidity</b> (weekly GLI proxy) \u2192 <b>broad money</b> (global M2 growth). The composite\u2019s correlation with M2 is broad and persistent \u2014 it stays near its peak from 0 out to roughly 9 months ahead \u2014 whereas the GLI proxy peaks around '+cf.kbest_gli+' month(s) and decays quickly, the signature of a measure one step closer to M2 in the chain.';
build('cCF',()=>mk('cCF',{type:'line',plugins:[{id:'mid50',afterDraw(ch){const s=ch.scales.yG;if(!s)return;const y=s.getPixelForValue(50),{left,right}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.28)';x.setLineDash([4,4]);x.beginPath();x.moveTo(left,y);x.lineTo(right,y);x.stroke();x.restore();}}],
data:{labels:D.ml,datasets:[
line({label:'GLI proxy (base liquidity)',data:cf.gli_m,borderColor:C.orange,borderWidth:2.2,yAxisID:'yG'}),
line({label:'Composite lead index (conditions)',data:D.comp,borderColor:C.slate,borderWidth:1.6,yAxisID:'yZ'}),
line({label:'Global M2 YoY (broad money)',data:D.m2_yoy,borderColor:C.teal,borderWidth:1.6,yAxisID:'yM'})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:{title:it=>it.length?D.ml[it[0].dataIndex]:'',label:c=>{const u=c.dataset.yAxisID==='yG'?'':(c.dataset.yAxisID==='yM'?'%':' z');return ' '+c.dataset.label.split(' (')[0]+': '+c.parsed.y+u;}}}},
scales:{x:{grid:{display:false},ticks:{callback:yearTicks(D.ml,2),autoSkip:false,maxRotation:0}},
yG:{position:'left',min:0,max:100,ticks:{stepSize:25},title:{display:true,text:'GLI (0\u2013100)'}},
yZ:{position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'composite z'}},
yM:{position:'right',grid:{drawOnChartArea:false},title:{display:true,text:'M2 % YoY'}}}}}));
build('cLL',()=>mk('cLL',{type:'line',plugins:[{id:'zerok',afterDraw(ch){const s=ch.scales.x,i=cf.kk.indexOf(0);if(i<0)return;const px=s.getPixelForValue(i),{top,bottom}=ch.chartArea,x=ch.ctx;x.save();x.strokeStyle='rgba(34,28,20,.35)';x.setLineDash([3,3]);x.beginPath();x.moveTo(px,top);x.lineTo(px,bottom);x.stroke();x.restore();}}],
data:{labels:cf.kk,datasets:[
line({label:'Composite \u2192 M2',data:cf.r_cmp,borderColor:C.slate,borderWidth:2}),
line({label:'GLI proxy \u2192 M2',data:cf.r_gli,borderColor:C.orange,borderWidth:1.6})]},
options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
plugins:{legend:{position:'top',labels:{boxWidth:9,boxHeight:9,padding:14}},tooltip:{callbacks:{title:it=>it.length?'lead k = '+cf.kk[it[0].dataIndex]+' months':'',label:c=>' '+c.dataset.label+': r '+(c.parsed.y==null?'\u2014':c.parsed.y.toFixed(2))}}},
scales:{x:{grid:{display:false},title:{display:true,text:'lead k (months; positive = measure leads M2)'},ticks:{callback:(v,i)=>{const k=cf.kk[i];return k%6===0?k:'';},autoSkip:false,maxRotation:0}},y:{title:{display:true,text:'correlation'}}}}}));
if(!made.cfread){made.cfread=1;
document.getElementById('cfread').innerHTML=
'<p style="margin:0 0 8px">Right now the chain reads <b>consistently tight</b>. The composite sits at '+m.comp+' z ('+(m.comp_d3<0?'falling':'rising')+' over three months), the weekly GLI is in <b>'+D.snap.phase+'</b> at '+D.snap.gli+', and global M2 growth is '+m.m2_yoy+'% and mapped to decelerate toward '+ (m.imp_end==null?'\u2014':m.imp_end+'%') +' by '+m.imp_date+'.</p>'+
'<p style="margin:0 0 8px">The ordering holds historically: the composite\u2019s correlation with M2 stays elevated well to the right of zero (still ~0.5 at nine months), consistent with the '+cf.keff+'-month effective lead used for the implied path. The GLI proxy \u2014 already a normalised stock of base liquidity \u2014 sits closer to M2 in time, peaking within a few months and fading fast, as expected for a measure one step further down the chain.</p>'+
'<p style="margin:0">Watch for <b>divergence</b>: when the composite turns up while M2 is still falling, it has historically marked the low; the reverse has marked tops. All three pointing the same way, as now, is a regime signal rather than a turning point.</p>';}
}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{
document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
b.classList.add('active');document.getElementById(b.dataset.t).classList.add('active');
({p1:pHead,p2:pCb,p3:pMom,p4:pCn,p6:pM2,p7:pLead,p8:pCF}[b.dataset.t]||function(){})();
requestAnimationFrame(resizeActive);}));

/* ---- data download suite ---- */
function csvCell(v){if(v==null)return '';const s=String(v);return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}
function toCsv(cols,rows){return cols.join(',')+'\n'+rows.map(r=>r.map(csvCell).join(',')).join('\n')+'\n';}
function saveBlob(data,fname,mime){const b=new Blob([data],{type:mime});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=fname;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1500);}
const DN=Object.keys(D.dz);
function weeklyRows(){const c=['week','gli','phase','g3_tn','fed_net_tn','ecb_tn','boj_tn','cn_tn','fed_assets_tn','tga_tn','rrp_tn','us_bank_credit_tn','g3_yoy_pct','g3_ann13_pct','global_liq_yoy_pct','h8_yoy_pct','cn_m2_yoy_local_pct','usd_proxy_idx','z_centralbank','z_private','z_composite'];
const r=[];for(let i=0;i<D.labels.length;i++){r.push([D.labels[i],D.gli[i],D.phase[i],D.g3[i],D.fed_net[i],D.ecb[i],D.boj[i],D.cn[i],D.fed_assets[i],D.tga[i],D.rrp[i],D.h8[i],D.g3_yoy[i],D.g3_a13[i],D.tot_yoy[i],D.h8_yoy[i],D.cn_yoy_lcl[i],D.usdx[i],D.z_cb[i],D.z_pvt[i],D.z_comp[i]]);}
return{cols:c,rows:r};}
function monthlyRows(){const c=['month','m2_us_tn','m2_ea_tn','m2_jp_tn','m2_cn_tn','global_m2_tn','m2_yoy_usd_pct','m2_yoy_fxconst_pct','composite_z'].concat(DN.map(n=>'z: '+n));
const r=[];for(let i=0;i<D.ml.length;i++){const tot=['m2_us','m2_ea','m2_jp','m2_cn'].reduce((s,k)=>s+(D[k][i]||0),0);
r.push([D.ml[i],D.m2_us[i],D.m2_ea[i],D.m2_jp[i],D.m2_cn[i],D.m2_us[i]==null?null:+tot.toFixed(2),D.m2_yoy[i],D.m2_fxc[i],D.comp[i]].concat(DN.map(n=>D.dz[n][i])));}
return{cols:c,rows:r};}
function dlWeeklyCsv(){const w=weeklyRows();saveBlob(toCsv(w.cols,w.rows),'global_liquidity_weekly.csv','text/csv;charset=utf-8');}
function dlMonthlyCsv(){const m=monthlyRows();saveBlob(toCsv(m.cols,m.rows),'global_m2_lead_index_monthly.csv','text/csv;charset=utf-8');}
let _xlsx=null;
function loadXlsx(){return _xlsx||(_xlsx=new Promise((res,rej)=>{if(window.XLSX)return res(window.XLSX);const s=document.createElement('script');s.src='https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js';s.onload=()=>res(window.XLSX);s.onerror=()=>rej(new Error('xlsx load failed'));document.head.appendChild(s);}));}
async function dlXlsx(btn){const old=btn.textContent;btn.disabled=true;btn.textContent='Building…';
try{const XLSX=await loadXlsx();const wb=XLSX.utils.book_new();
const w=weeklyRows();XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet([w.cols,...w.rows]),'Weekly GLI');
const m=monthlyRows();XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet([m.cols,...m.rows]),'Monthly M2 & lead');
const sc=[['Driver','k* (months)','r USD-terms','r FX-constant','Weight %','Verdict']].concat(D.score.map(r=>[r.n,r.k,r.ru,r.rc,r.w,r.v]));
XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(sc),'Lead scorecard');
const s=D.snap,ms=D.msnap;
const info=[['Global Liquidity Monitor — data export'],['Built (UTC)',D.built],[],['WEEKLY GLI PROXY'],['As of',s.date],['GLI proxy',s.gli],['Phase',s.phase],['13-week change (pts)',s.gli_13w],['G3 CB liquidity ($tn)',s.g3_tn],['G3 YoY %',s.g3_yoy],['Global liquidity G3+CN M2 ($tn)',s.tot_tn],['Global liquidity YoY %',s.tot_yoy],['Fed net ($tn)',s.fed_net_tn],['China M2 ($tn)',s.cn_tn],[],['GLOBAL M2 & LEAD INDEX'],['As of',ms.m2_date],['Global M2 4-econ ($tn)',ms.m2_tn],['M2 YoY USD-terms %',ms.m2_yoy],['M2 YoY FX-constant %',ms.fxc_yoy],['China share %',ms.cn_share],['Composite z',ms.comp],['Composite 3m change',ms.comp_d3],['Effective lead (months)',ms.keff],['OLS fit r',ms.fit_r],['Implied M2 YoY %',ms.imp_end],['Implied for',ms.imp_date],['Residual band ±pp',ms.band],[],['SOURCES'],['Series','Code','Provider','Cadence']].concat(D.sources.map(x=>[x.s,x.code,x.src,x.cad]));
info.push([],['Note','Research reconstruction for internal use; not investment advice. Figures embed source revisions and FX valuation effects.']);
XLSX.utils.book_append_sheet(wb,XLSX.utils.aoa_to_sheet(info),'Info');
XLSX.writeFile(wb,'global_liquidity_monitor.xlsx');
}catch(e){alert('Excel export unavailable (needs network for the SheetJS library). Use the CSV downloads instead.');}
finally{btn.disabled=false;btn.textContent=old;}}
document.getElementById('dlWeekly').addEventListener('click',dlWeeklyCsv);
document.getElementById('dlMonthly').addEventListener('click',dlMonthlyCsv);
document.getElementById('dlXlsx').addEventListener('click',e=>dlXlsx(e.currentTarget));

document.querySelectorAll('.rgbtn').forEach(btn=>btn.addEventListener('click',()=>{
document.querySelectorAll('.rgbtn').forEach(x=>x.classList.remove('on'));
btn.classList.add('on');RANGE=btn.dataset.r;applyRange();requestAnimationFrame(resizeActive);}));
pHead();requestAnimationFrame(resizeActive);
})();
</script>
</body>
</html>"""

if __name__ == "__main__":
    main()
