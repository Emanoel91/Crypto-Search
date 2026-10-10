"""
Market Explorer tab  -  Crypto-Search Dashboard
================================================
Source: GET /v3/currencies/list  (free Sandbox plan, 1 credit per request).

Instead of just re-printing a price table, this tab turns one market snapshot into
derived indicators you will not find on a standard market page:

  1. Market Pulse      - breadth, median vs cap-weighted move, auto-written verdict
  2. Cap-Tier Rotation - who is winning: top 10, mid caps or small caps? + Attention Index
  3. Turnover Radar    - volume / market-cap map, unusually active & unusually quiet coins
  4. Concentration     - how top-heavy is the market (cumulative market-cap curve)
  5. Explorer table    - searchable / filterable / downloadable

API key: CRYPTORANK_API_KEY in Streamlit secrets (never hard-code it).
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

BASE_URL = "https://api.cryptorank.io/v3"
TIMEOUT = 25
PAGE_SIZE = 300      # max page size of the list endpoint
TTL = 600            # cache lifetime in seconds (10 min)
MAX_CALLS = 5        # hard safety cap on requests per refresh

GREEN, RED, GREY, ORANGE = "#22C55E", "#EF4444", "#9CA3AF", "#F7931A"


# ----------------------------------------------------------------------------
# API layer
# ----------------------------------------------------------------------------
def _get_api_key() -> str:
    try:
        return st.secrets["CRYPTORANK_API_KEY"]
    except Exception:
        return os.environ.get("CRYPTORANK_API_KEY", "")


def _http_get(path: str, api_key: str, params=None):
    try:
        return requests.get(
            f"{BASE_URL}{path}",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error while calling {path}: {exc}") from exc


def _extract_rows(body) -> list:
    data = body.get("data", body) if isinstance(body, dict) else body
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _row_id(row: dict, idx: int) -> str:
    for k in ("id", "key", "slug", "symbol"):
        if row.get(k) is not None:
            return f"{k}:{row[k]}"
    return f"row:{idx}:{json.dumps(row, sort_keys=True, default=str)[:120]}"


@st.cache_data(ttl=TTL, show_spinner=False)
def fetch_currencies(api_key: str, n_coins: int) -> dict:
    """Load up to n_coins rows from /currencies/list (offset pagination)."""
    rows, seen = [], set()
    paginated, calls = True, 0

    while len(rows) < n_coins and calls < MAX_CALLS:
        limit = min(PAGE_SIZE, n_coins - len(rows))
        params = {"limit": limit, "skip": len(rows)} if paginated else None
        resp = _http_get("/currencies/list", api_key, params)
        calls += 1

        # If the pagination parameters are rejected, fall back to the default page.
        if resp.status_code in (400, 422) and paginated:
            paginated = False
            resp = _http_get("/currencies/list", api_key)
            calls += 1

        if resp.status_code != 200:
            hint = {401: "Check your API key.", 403: "Your plan may not allow this endpoint.",
                    429: "Rate limit reached (10 requests/min on the free plan)."}.get(resp.status_code, "")
            raise RuntimeError(f"HTTP {resp.status_code} on /currencies/list. {hint} {resp.text[:250]}".strip())

        page = _extract_rows(resp.json())
        new = 0
        for r in page:
            rid = _row_id(r, len(seen))
            if rid not in seen:
                seen.add(rid)
                rows.append(r)
                new += 1
        if new == 0 or not paginated or len(page) < limit:
            break

    return {"rows": rows, "fetched_at": time.time(), "calls": calls, "paginated": paginated}


# ----------------------------------------------------------------------------
# Tolerant field discovery (response field names are matched by keywords)
# ----------------------------------------------------------------------------
def flatten(obj, prefix: str = "") -> dict:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    else:
        out[prefix or "value"] = obj
    return out


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _pick(flat: dict, include, exclude=()):
    best = None
    for k, v in flat.items():
        nk = _norm(k)
        if all(i in nk for i in include) and not any(e in nk for e in exclude):
            num = _to_float(v)
            if num is None:
                continue
            if best is None or len(nk) < best[0]:
                best = (len(nk), num)
    return best[1] if best else None


def _pick_first(flat: dict, attempts):
    for include, exclude in attempts:
        val = _pick(flat, include, exclude)
        if val is not None:
            return val
    return None


def _pick_text(flat: dict, names):
    best = None
    for k, v in flat.items():
        if isinstance(v, str) and _norm(k.split(".")[-1]) in names:
            if best is None or len(k) < best[0]:
                best = (len(k), v)
    return best[1] if best else None


_CHG_EXCL = ["volume", "marketcap", "mcap", "supply", "price"]
_PRICE_EXCL = ["change", "percent", "ath", "atl", "high", "low", "chg"]


def to_frame(rows: list) -> pd.DataFrame:
    recs = []
    for r in rows:
        f = flatten(r)
        recs.append({
            "name": _pick_text(f, ["name"]),
            "symbol": _pick_text(f, ["symbol", "ticker"]),
            "rank_api": _pick_first(f, [(["rank"], ["change"])]),
            "price": _pick_first(f, [(["price"], _PRICE_EXCL)]),
            "mcap": _pick_first(f, [(["marketcap"], ["change", "percent", "fully", "fdv", "diluted", "rank", "dominance"])]),
            "volume": _pick_first(f, [(["volume", "24"], ["change", "percent", "dominance"]),
                                      (["volume"], ["change", "percent", "dominance"])]),
            "chg24": _pick_first(f, [(["change", "24h"], _CHG_EXCL), (["percent", "24h"], _CHG_EXCL)]),
            "chg7d": _pick_first(f, [(["change", "7d"], _CHG_EXCL), (["percent", "7d"], _CHG_EXCL)]),
            "chg30d": _pick_first(f, [(["change", "30d"], _CHG_EXCL), (["percent", "30d"], _CHG_EXCL)]),
        })
    df = pd.DataFrame(recs)
    for col in ["rank_api", "price", "mcap", "volume", "chg24", "chg7d", "chg30d"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["symbol"] = df["symbol"].fillna("?")
    df["name"] = df["name"].fillna(df["symbol"])
    df = df.sort_values("mcap", ascending=False, na_position="last").reset_index(drop=True)
    df["pos"] = np.arange(1, len(df) + 1)                      # rank by market cap inside the set
    df["turnover"] = np.where((df["mcap"] > 0) & (df["volume"] >= 0), df["volume"] / df["mcap"] * 100, np.nan)
    return df


# ----------------------------------------------------------------------------
# Analytics (pure functions - no Streamlit calls)
# ----------------------------------------------------------------------------
def pulse_stats(df: pd.DataFrame):
    a = df.dropna(subset=["mcap", "chg24"])
    a = a[a["mcap"] > 0]
    if len(a) < 5:
        return None
    chg = a["chg24"].clip(-50, 50)
    return {
        "n": len(a),
        "breadth": float((a["chg24"] > 0).mean() * 100),
        "median": float(a["chg24"].median()),
        "cap_weighted": float((chg * a["mcap"]).sum() / a["mcap"].sum()),
        "ew_mean": float(chg.mean()),
    }


def verdict(s: dict):
    b, med, cw = s["breadth"], s["median"], s["cap_weighted"]
    if b >= 60 and med > 0:
        return "success", "Broad rally", (
            f"{b:.0f}% of coins are up and the median coin gained {med:+.2f}% - participation is wide, not just majors.")
    if b <= 40 and med < 0:
        return "error", "Broad sell-off", (
            f"Only {b:.0f}% of coins are up and the median coin moved {med:+.2f}% - weakness is spread across the market.")
    if cw > 0 and b < 45:
        return "warning", "Narrow, giant-led move", (
            f"Cap-weighted change is {cw:+.2f}% but only {b:.0f}% of coins are rising - large caps are carrying the market.")
    if cw < 0 and b > 55:
        return "info", "Majors lag, the rest is rising", (
            f"Cap-weighted change is {cw:+.2f}% while {b:.0f}% of coins are up - money may be rotating away from large caps.")
    return "info", "Mixed / range-bound", (
        f"{b:.0f}% of coins are up, median {med:+.2f}%, cap-weighted {cw:+.2f}% - no clear direction.")


TIER_EDGES = [0, 10, 50, 100, 250, 500, 10**9]
TIER_LABELS = ["Top 10", "11-50", "51-100", "101-250", "251-500", "500+"]


def tier_table(df: pd.DataFrame) -> pd.DataFrame:
    a = df.dropna(subset=["mcap"]).copy()
    a = a[a["mcap"] > 0].sort_values("mcap", ascending=False).reset_index(drop=True)
    a["tier"] = pd.cut(np.arange(1, len(a) + 1), bins=TIER_EDGES, labels=TIER_LABELS, right=True)
    a["volume"] = a["volume"].fillna(0)
    tot_mcap, tot_vol = a["mcap"].sum(), a["volume"].sum()
    rows = []
    for tier, g in a.groupby("tier", observed=True):
        mcap_share = g["mcap"].sum() / tot_mcap * 100
        vol_share = (g["volume"].sum() / tot_vol * 100) if tot_vol > 0 else np.nan
        chg = g["chg24"].dropna()
        rows.append({
            "tier": str(tier),
            "coins": len(g),
            "breadth": float((chg > 0).mean() * 100) if len(chg) else np.nan,
            "median_chg": float(chg.median()) if len(chg) else np.nan,
            "mcap_share": mcap_share,
            "vol_share": vol_share,
            "attention": (vol_share / mcap_share) if mcap_share > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def turnover_frames(df: pd.DataFrame):
    r = df[(df["mcap"] > 0) & (df["volume"] > 0)].copy()
    if len(r) < 10:
        return r, None, None
    x = np.log10(r["turnover"])
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    r["z"] = (x - med) / (1.4826 * mad + 1e-9)

    def signal(row):
        c = row["chg24"]
        if pd.isna(c):
            return "n/a"
        return "Active & rising" if c > 2 else ("Active & falling" if c < -2 else "Active, flat")

    active = r[r["z"] > 2].sort_values("z", ascending=False).head(12).copy()
    active["signal"] = active.apply(signal, axis=1)
    quiet = r[r["pos"] <= 250].sort_values("z").head(12).copy()
    quiet = quiet[quiet["z"] < -1]
    return r, active, quiet


def concentration(df: pd.DataFrame):
    c = df["mcap"].dropna()
    c = c[c > 0].sort_values(ascending=False).reset_index(drop=True)
    if len(c) < 10:
        return None
    share = c.cumsum() / c.sum() * 100
    return {
        "share": share,
        "top1": float(share.iloc[0]),
        "top10": float(share.iloc[min(9, len(share) - 1)]),
        "n90": int((share < 90).sum() + 1),
        "n": len(c),
    }


# ----------------------------------------------------------------------------
# UI sections
# ----------------------------------------------------------------------------
def _layout(fig, height=380):
    fig.update_layout(height=height, margin=dict(t=50, b=30, l=10, r=10),
                      paper_bgcolor="rgba(0,0,0,0)")
    return fig


def section_pulse(df):
    st.subheader("1 · Market Pulse")
    st.caption("Is the move broad or driven by a few giants? Compares the median coin with the "
               "market-cap-weighted move.")
    s = pulse_stats(df)
    if s is None:
        st.info("24h change data was not found, so the pulse cannot be computed.")
        return
    level, title, text = verdict(s)
    getattr(st, level)(f"**{title}** - {text}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Breadth (coins up)", f"{s['breadth']:.0f}%")
    c2.metric("Median coin (24h)", f"{s['median']:+.2f}%")
    c3.metric("Cap-weighted (24h)", f"{s['cap_weighted']:+.2f}%")
    c4.metric("Leadership gap", f"{s['cap_weighted'] - s['median']:+.2f} pp",
              help="Cap-weighted change minus median change. Positive = giants outperform the typical coin.")


def section_tiers(df):
    st.subheader("2 · Cap-Tier Rotation & Attention Index")
    st.caption("Median 24h change per size bucket, and where trading attention goes relative to size "
               "(Attention Index = share of volume ÷ share of market cap; above 1 = over-traded).")
    t = tier_table(df)
    if t.empty:
        st.info("Not enough data for tier analysis.")
        return
    left, right = st.columns(2)
    with left:
        colors = [GREEN if (v or 0) >= 0 else RED for v in t["median_chg"]]
        fig = go.Figure(go.Bar(x=t["tier"], y=t["median_chg"], marker_color=colors,
                               text=[f"{v:+.2f}%" if pd.notna(v) else "" for v in t["median_chg"]],
                               textposition="outside"))
        fig.update_layout(title="Median 24h change by size tier", yaxis_title="%")
        st.plotly_chart(_layout(fig), use_container_width=True)
    with right:
        colors = [ORANGE if (v or 0) > 1 else GREY for v in t["attention"]]
        fig = go.Figure(go.Bar(x=t["tier"], y=t["attention"], marker_color=colors,
                               text=[f"{v:.2f}" if pd.notna(v) else "" for v in t["attention"]],
                               textposition="outside"))
        fig.add_hline(y=1, line_dash="dash", line_color="#6B7280")
        fig.update_layout(title="Attention Index by size tier", yaxis_title="x")
        st.plotly_chart(_layout(fig), use_container_width=True)

    show = pd.DataFrame({
        "Tier": t["tier"], "Coins": t["coins"],
        "Coins up %": t["breadth"], "Median 24h %": t["median_chg"],
        "Share of mcap %": t["mcap_share"], "Share of volume %": t["vol_share"],
    })
    st.dataframe(show, hide_index=True, use_container_width=True, column_config={
        "Coins up %": st.column_config.NumberColumn(format="%.0f"),
        "Median 24h %": st.column_config.NumberColumn(format="%.2f"),
        "Share of mcap %": st.column_config.NumberColumn(format="%.1f"),
        "Share of volume %": st.column_config.NumberColumn(format="%.1f"),
    })


def section_radar(df):
    st.subheader("3 · Turnover Radar")
    st.caption("Turnover = 24h volume ÷ market cap. Bubbles far above the median line are trading unusually "
               "hard for their size; colour shows the 24h move.")
    r, active, quiet = turnover_frames(df)
    if active is None:
        st.info("Not enough volume / market-cap data for the radar.")
        return

    size = 6 + 34 * np.sqrt(r["volume"] / r["volume"].max())
    has_chg = r["chg24"].notna().any()
    marker = dict(size=size, opacity=0.75, line=dict(width=0.5, color="#374151"))
    if has_chg:
        marker.update(color=r["chg24"].clip(-15, 15).fillna(0), colorscale="RdYlGn", cmin=-15, cmax=15,
                      colorbar=dict(title="24h %"))
    else:
        marker.update(color=GREY)
    fig = go.Figure(go.Scatter(
        x=r["mcap"], y=r["turnover"], mode="markers", marker=marker,
        text=r["symbol"],
        customdata=r[["name", "chg24", "volume"]].to_numpy(),
        hovertemplate="<b>%{text}</b> (%{customdata[0]})<br>MCap: %{x:,.0f}<br>"
                      "Turnover: %{y:.2f}%<br>24h: %{customdata[1]:.2f}%<extra></extra>",
    ))
    fig.add_hline(y=float(r["turnover"].median()), line_dash="dash", line_color="#6B7280",
                  annotation_text="median turnover", annotation_position="bottom right")
    fig.update_layout(xaxis_title="Market cap (log)", yaxis_title="Turnover % (log)",
                      xaxis_type="log", yaxis_type="log", title="Market cap vs turnover")
    st.plotly_chart(_layout(fig, 480), use_container_width=True)

    cfg = {"MCap (M)": st.column_config.NumberColumn(format="%.1f"),
           "Turnover %": st.column_config.NumberColumn(format="%.2f"),
           "24h %": st.column_config.NumberColumn(format="%.2f"),
           "Activity z": st.column_config.NumberColumn(format="%.1f")}

    def _tbl(d, with_signal):
        out = pd.DataFrame({"#": d["pos"], "Symbol": d["symbol"], "MCap (M)": d["mcap"] / 1e6,
                            "Turnover %": d["turnover"], "24h %": d["chg24"], "Activity z": d["z"]})
        if with_signal:
            out["Signal"] = d["signal"]
        return out

    a, b = st.columns(2)
    with a:
        st.markdown("**Unusually active** (turnover far above the market norm)")
        if len(active):
            st.dataframe(_tbl(active, True), hide_index=True, use_container_width=True, column_config=cfg)
        else:
            st.caption("No outliers right now.")
    with b:
        st.markdown("**Unusually quiet** (top-250 coins with thin trading for their size)")
        if len(quiet):
            st.dataframe(_tbl(quiet, False), hide_index=True, use_container_width=True, column_config=cfg)
        else:
            st.caption("No outliers right now.")


def section_concentration(df):
    st.subheader("4 · Market Concentration")
    st.caption("How top-heavy is the market? Cumulative share of total market cap by rank "
               "(within the analysed set).")
    c = concentration(df)
    if c is None:
        st.info("Not enough market-cap data.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("#1 coin share", f"{c['top1']:.1f}%")
    m2.metric("Top 10 share", f"{c['top10']:.1f}%")
    m3.metric("Coins needed for 90%", f"{c['n90']}", help=f"Out of {c['n']} analysed coins")

    x = np.arange(1, c["n"] + 1)
    fig = go.Figure(go.Scatter(x=x, y=c["share"], mode="lines", line=dict(color=ORANGE, width=3),
                               fill="tozeroy", fillcolor="rgba(247,147,26,0.15)"))
    fig.add_hline(y=90, line_dash="dash", line_color="#6B7280", annotation_text="90%")
    fig.update_layout(xaxis_title="Rank by market cap", yaxis_title="Cumulative share of market cap (%)",
                      xaxis_type="log", yaxis_range=[0, 100], title="Cumulative market-cap share")
    st.plotly_chart(_layout(fig), use_container_width=True)


def section_table(df):
    st.subheader("5 · Explorer Table")
    f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
    query = f1.text_input("Search name or symbol", key="mx_search")
    min_mcap = f2.selectbox("Min market cap", ["Any", "$1M", "$10M", "$100M", "$1B"], key="mx_minmcap")
    sort_by = f3.selectbox("Sort by", ["Market cap", "24h change", "Volume", "Turnover"], key="mx_sort")
    order = f4.radio("Order", ["Highest first", "Lowest first"], horizontal=True, key="mx_order")

    v = df.copy()
    if query:
        q = query.lower().strip()
        v = v[v["name"].str.lower().str.contains(q, na=False) | v["symbol"].str.lower().str.contains(q, na=False)]
    floor = {"Any": 0, "$1M": 1e6, "$10M": 1e7, "$100M": 1e8, "$1B": 1e9}[min_mcap]
    if floor:
        v = v[v["mcap"] >= floor]
    col = {"Market cap": "mcap", "24h change": "chg24", "Volume": "volume", "Turnover": "turnover"}[sort_by]
    v = v.sort_values(col, ascending=(order == "Lowest first"), na_position="last")

    show = pd.DataFrame({
        "#": v["pos"], "Name": v["name"], "Symbol": v["symbol"], "Price": v["price"],
        "24h %": v["chg24"], "7d %": v["chg7d"], "30d %": v["chg30d"],
        "MCap (M)": v["mcap"] / 1e6, "Volume 24h (M)": v["volume"] / 1e6, "Turnover %": v["turnover"],
    }).dropna(axis=1, how="all")

    fmt = {"Price": "%.4f", "24h %": "%.2f", "7d %": "%.2f", "30d %": "%.2f",
           "MCap (M)": "%.1f", "Volume 24h (M)": "%.1f", "Turnover %": "%.2f"}
    cfg = {k: st.column_config.NumberColumn(format=f) for k, f in fmt.items() if k in show.columns}
    st.caption(f"{len(show):,} coins shown")
    st.dataframe(show, hide_index=True, use_container_width=True, height=520, column_config=cfg)
    st.download_button("⬇️ Download CSV", show.to_csv(index=False).encode("utf-8"),
                       file_name="market_explorer.csv", mime="text/csv", key="mx_csv")


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def render():
    st.header("🔍 Market Explorer")

    api_key = _get_api_key()
    if not api_key:
        st.error("API key not found. Add `CRYPTORANK_API_KEY` to the app's Secrets.")
        return

    c1, c2, c3 = st.columns([2, 4, 1])
    n_coins = c1.selectbox("Coins to analyse", [100, 300, 600], index=1, key="mx_n",
                           help="Each 300 coins costs 1 credit (cached for 10 minutes).")
    if c3.button("🔄 Refresh", key="mx_refresh"):
        st.cache_data.clear()

    try:
        with st.spinner("Loading market data..."):
            res = fetch_currencies(api_key, int(n_coins))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load the currency list: {exc}")
        return

    rows = res["rows"]
    if not rows:
        st.warning("The API returned no coins.")
        return

    ts = datetime.fromtimestamp(res["fetched_at"], tz=timezone.utc)
    c2.caption(f"{len(rows)} coins · fetched {ts:%Y-%m-%d %H:%M} UTC · cached up to {TTL // 60} min")
    if not res["paginated"] and n_coins > len(rows):
        st.caption("ℹ️ The API only returned its default page size, so fewer coins than requested are analysed.")

    df = to_frame(rows)

    missing = [n for n, c in [("market cap", "mcap"), ("volume", "volume"), ("24h change", "chg24"),
                              ("price", "price")] if df[c].isna().all()]
    if df["mcap"].isna().all():
        st.error("Market-cap field was not found in the response, so the analytics cannot run. "
                 "Detected fields in the first row: " + ", ".join(sorted(flatten(rows[0]).keys())))
        return
    if missing:
        st.warning("Fields not found in the response: " + ", ".join(missing) +
                   ". Related sections will be limited. Detected fields: " +
                   ", ".join(sorted(flatten(rows[0]).keys())))

    section_pulse(df)
    st.divider()
    section_tiers(df)
    st.divider()
    section_radar(df)
    st.divider()
    section_concentration(df)
    st.divider()
    section_table(df)
