"""
Market Overview tab  -  Crypto-Search Dashboard
================================================
Uses ONLY endpoints that are available on the free (Sandbox) plan:

    GET /v3/global/market        -> Global Market Snapshot
    GET /v3/global/fear-greed    -> Fear & Greed Index
    GET /v3/global/altcoin-index -> Altcoin Season Index

Cost: 1 credit per request  ->  3 credits per full refresh.
API key: set CRYPTORANK_API_KEY in Streamlit secrets (never hard-code it).
"""

import os
import re
import time
from datetime import datetime, timezone

import plotly.graph_objects as go
import requests
import streamlit as st

BASE_URL = "https://api.cryptorank.io/v3"
TIMEOUT = 20  # seconds

TTL_MARKET = 600   # global snapshot: 10 minutes
TTL_INDEX = 1800   # sentiment indices: 30 minutes


# ----------------------------------------------------------------------------
# API layer
# ----------------------------------------------------------------------------
def _get_api_key() -> str:
    try:
        return st.secrets["CRYPTORANK_API_KEY"]
    except Exception:
        return os.environ.get("CRYPTORANK_API_KEY", "")


def _request(path: str, api_key: str) -> dict:
    try:
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error while calling {path}: {exc}") from exc

    if resp.status_code != 200:
        detail = resp.text[:300]
        if resp.status_code in (401, 403):
            hint = "Check your API key / plan permissions."
        elif resp.status_code == 429:
            hint = "Rate limit reached (free plan: 10 requests/minute). Try again shortly."
        else:
            hint = ""
        raise RuntimeError(f"HTTP {resp.status_code} on {path}. {hint} {detail}".strip())

    body = resp.json()
    payload = body.get("data", body) if isinstance(body, dict) else body
    return {"payload": payload, "fetched_at": time.time()}


@st.cache_data(ttl=TTL_MARKET, show_spinner=False)
def fetch_global_market(api_key: str) -> dict:
    return _request("/global/market", api_key)


@st.cache_data(ttl=TTL_INDEX, show_spinner=False)
def fetch_fear_greed(api_key: str) -> dict:
    return _request("/global/fear-greed", api_key)


@st.cache_data(ttl=TTL_INDEX, show_spinner=False)
def fetch_altcoin_index(api_key: str) -> dict:
    return _request("/global/altcoin-index", api_key)


def _safe(fetcher, api_key):
    try:
        return fetcher(api_key), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# ----------------------------------------------------------------------------
# Helpers: tolerant parsing (field names are discovered, not hard-coded)
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


def pick(flat: dict, include, exclude=()):
    """Numeric field whose normalized name contains all `include` words and none
    of `exclude` (shortest name wins). Returns (key, float|None)."""
    best = None
    for k, v in flat.items():
        nk = _norm(k)
        if all(i in nk for i in include) and not any(e in nk for e in exclude):
            num = _to_float(v)
            if num is None:
                continue
            if best is None or len(nk) < best[0]:
                best = (len(nk), k, num)
    return (best[1], best[2]) if best else (None, None)


def pick_first(flat: dict, attempts):
    """Try several (include, exclude) combinations in order."""
    for include, exclude in attempts:
        _, val = pick(flat, include, exclude)
        if val is not None:
            return val
    return None


def pick_text(flat: dict, words, exclude=()):
    for k, v in flat.items():
        nk = _norm(k)
        if isinstance(v, str) and _to_float(v) is None:
            if any(w in nk for w in words) and not any(e in nk for e in exclude):
                return v
    return None


def fmt_usd(n):
    if n is None:
        return "N/A"
    a = abs(n)
    if a >= 1e12:
        return f"${n / 1e12:,.2f}T"
    if a >= 1e9:
        return f"${n / 1e9:,.2f}B"
    if a >= 1e6:
        return f"${n / 1e6:,.2f}M"
    return f"${n:,.0f}"


def fmt_int(n):
    return "N/A" if n is None else f"{n:,.0f}"


def fmt_pct(n):
    return "N/A" if n is None else f"{n:+.2f}%"


# ----------------------------------------------------------------------------
# UI blocks
# ----------------------------------------------------------------------------
_CHG_EXCL = ["change", "percent", "dominance", "ath", "ratio"]


def render_kpis(payload: dict):
    flat = flatten(payload)

    total_mcap = pick_first(flat, [
        (["total", "marketcap"], _CHG_EXCL + ["btc", "eth", "bitcoin", "ethereum"]),
        (["marketcap"], _CHG_EXCL + ["btc", "eth", "bitcoin", "ethereum"]),
    ])
    total_vol = pick_first(flat, [
        (["total", "volume"], ["change", "percent", "ratio"]),
        (["volume"], ["change", "percent", "ratio"]),
    ])
    mcap_change = pick_first(flat, [
        (["marketcap", "change", "24"], ["btc", "eth"]),
        (["marketcap", "change"], ["btc", "eth"]),
    ])
    btc_mcap = pick_first(flat, [
        (["btc", "marketcap"], ["change", "percent", "dominance"]),
        (["bitcoin", "marketcap"], ["change", "percent", "dominance"]),
    ])
    eth_mcap = pick_first(flat, [
        (["eth", "marketcap"], ["change", "percent", "dominance"]),
        (["ethereum", "marketcap"], ["change", "percent", "dominance"]),
    ])
    active_cur = pick_first(flat, [
        (["active", "currenc"], ["change"]),
        (["active", "coin"], ["change"]),
        (["currenc"], ["change"]),
        (["coins"], ["change"]),
    ])
    active_exc = pick_first(flat, [
        (["active", "exchange"], ["percent", "pct", "delta"]),
        (["exchange"], ["percent", "pct", "delta"]),
    ])

    mcap_to_vol = (total_mcap / total_vol) if (total_mcap and total_vol) else None
    btc_dom = (btc_mcap / total_mcap * 100) if (btc_mcap is not None and total_mcap) else None
    eth_dom = (eth_mcap / total_mcap * 100) if (eth_mcap is not None and total_mcap) else None

    # Row 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total MarketCap", fmt_usd(total_mcap))
    c2.metric("Total Volume (24h)", fmt_usd(total_vol))
    c3.metric("MarketCap/Volume", "N/A" if mcap_to_vol is None else f"{mcap_to_vol:,.2f}")
    c4.metric("%MarketCap Change (24h)", fmt_pct(mcap_change))

    # Row 2
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("BTC MarketCap", fmt_usd(btc_mcap))
    d1.caption("BTC Dominance: " + ("N/A" if btc_dom is None else f"{btc_dom:.2f}%"))
    d2.metric("ETH MarketCap", fmt_usd(eth_mcap))
    d2.caption("ETH Dominance: " + ("N/A" if eth_dom is None else f"{eth_dom:.2f}%"))
    d3.metric("Active Currencies", fmt_int(active_cur))
    d4.metric("Active Exchanges", fmt_int(active_exc))

    missing = [name for name, val in [
        ("Total MarketCap", total_mcap), ("Total Volume", total_vol),
        ("MarketCap Change", mcap_change), ("BTC MarketCap", btc_mcap),
        ("ETH MarketCap", eth_mcap), ("Active Currencies", active_cur),
        ("Active Exchanges", active_exc),
    ] if val is None]
    if missing:
        st.warning("Fields not found in the API response: " + ", ".join(missing))


def render_index_card(title: str, payload: dict, steps: list):
    """Gauge (0-100) showing the index value and its classification."""
    flat = flatten(payload)
    prev_words = ["previous", "prev", "yesterday", "week", "month", "year", "last"]

    _, value = pick(flat, ["value"], exclude=prev_words)
    if value is None:
        for v in flat.values():
            num = _to_float(v)
            if num is not None:
                value = num
                break

    label = pick_text(flat, ["classification", "label", "status", "category", "name"],
                      exclude=prev_words)

    st.subheader(title)
    if value is None:
        st.warning("Could not find a numeric value in the response.")
        return

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=value,
            title={"text": label or ""},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#111827", "thickness": 0.25},
                "steps": steps,
            },
        )
    )
    fig.update_layout(height=300, margin=dict(t=60, b=10, l=30, r=30),
                      paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


FEAR_GREED_STEPS = [
    {"range": [0, 25], "color": "#EF4444"},
    {"range": [25, 45], "color": "#F97316"},
    {"range": [45, 55], "color": "#EAB308"},
    {"range": [55, 75], "color": "#84CC16"},
    {"range": [75, 100], "color": "#22C55E"},
]

ALTCOIN_STEPS = [
    {"range": [0, 25], "color": "#F7931A"},
    {"range": [25, 75], "color": "#D1D5DB"},
    {"range": [75, 100], "color": "#3B82F6"},
]


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def render():
    st.header("📊 Market Overview")

    api_key = _get_api_key()
    if not api_key:
        st.error(
            "API key not found. Add `CRYPTORANK_API_KEY = \"...\"` to the app's "
            "Secrets (or set it as an environment variable)."
        )
        return

    top_l, top_r = st.columns([6, 1])
    with top_r:
        if st.button("🔄 Refresh", help="Clear the cache and reload (costs 3 credits)"):
            st.cache_data.clear()

    with st.spinner("Loading market data..."):
        market, err_market = _safe(fetch_global_market, api_key)
        fng, err_fng = _safe(fetch_fear_greed, api_key)
        alt, err_alt = _safe(fetch_altcoin_index, api_key)

    if market:
        with top_l:
            ts = datetime.fromtimestamp(market["fetched_at"], tz=timezone.utc)
            st.caption(f"Snapshot fetched at {ts:%Y-%m-%d %H:%M} UTC · cached up to "
                       f"{TTL_MARKET // 60} min")

    # KPI rows
    if err_market:
        st.error(f"Global market snapshot failed: {err_market}")
    else:
        render_kpis(market["payload"])

    st.divider()

    # Sentiment gauges
    g1, g2 = st.columns(2)
    with g1:
        if err_fng:
            st.error(f"Fear & Greed failed: {err_fng}")
        elif fng:
            render_index_card("Fear & Greed Index", fng["payload"], FEAR_GREED_STEPS)
    with g2:
        if err_alt:
            st.error(f"Altcoin Season Index failed: {err_alt}")
        elif alt:
            render_index_card("Altcoin Season Index", alt["payload"], ALTCOIN_STEPS)
