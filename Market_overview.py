"""
Market Overview tab  -  Cryptorank Streamlit dashboard
=======================================================
Uses ONLY endpoints that are available on the free (Sandbox) plan:

    GET /v3/global/market        -> Global Market Snapshot
    GET /v3/global/fear-greed    -> Fear & Greed Index
    GET /v3/global/altcoin-index -> Altcoin Season Index

Cost: 1 credit per request  ->  3 credits per full refresh.
Caching keeps usage far below the free quota (10 req/min, 10,000 credits/month).

API key: put it in .streamlit/secrets.toml  ->  CRYPTORANK_API_KEY = "your_key"
(or set the CRYPTORANK_API_KEY environment variable). Never hard-code it.
"""

import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

BASE_URL = "https://api.cryptorank.io/v3"
TIMEOUT = 20  # seconds

# Cache lifetimes (seconds)
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
    """Call a Cryptorank v3 endpoint and return {'payload': ..., 'fetched_at': ts}."""
    try:
        resp = requests.get(
            f"{BASE_URL}{path}",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error while calling {path}: {exc}") from exc

    if resp.status_code != 200:
        # Error envelope format is documented at docs.cryptorank.io/errors
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
    return {"payload": payload, "raw": body, "fetched_at": time.time()}


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
    """Return (result, error_message). One failing endpoint must not break the tab."""
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
    """Find the numeric field whose (normalized) name contains all `include` words
    and none of `exclude`. Shortest matching name wins. Returns (key, float|None)."""
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


def fmt_num(n):
    return "N/A" if n is None else f"{n:,.0f}"


def fmt_pct(n):
    return None if n is None else f"{n:+.2f}%"


# ----------------------------------------------------------------------------
# UI blocks
# ----------------------------------------------------------------------------
def _kpi(col, label, value_str, delta=None):
    col.metric(label, value_str, delta=delta)


def render_kpis(payload: dict):
    flat = flatten(payload)

    _, mcap = pick(flat, ["marketcap"], exclude=["change", "percent", "dominance", "ath"])
    _, mcap_chg = pick(flat, ["marketcap", "change"])
    _, vol = pick(flat, ["volume"], exclude=["change", "percent", "dominance"])
    _, vol_chg = pick(flat, ["volume", "change"])
    _, btc_dom = pick(flat, ["btc", "dominance"])
    if btc_dom is None:
        _, btc_dom = pick(flat, ["bitcoin", "dominance"])
    _, eth_dom = pick(flat, ["eth", "dominance"])
    if eth_dom is None:
        _, eth_dom = pick(flat, ["ethereum", "dominance"])
    _, coins = pick(flat, ["coins"], exclude=["change"])
    if coins is None:
        _, coins = pick(flat, ["currencies"], exclude=["change"])
    _, exchanges = pick(flat, ["exchanges"], exclude=["change"])

    c1, c2, c3, c4 = st.columns(4)
    _kpi(c1, "Total Market Cap", fmt_usd(mcap), fmt_pct(mcap_chg))
    _kpi(c2, "24h Trading Volume", fmt_usd(vol), fmt_pct(vol_chg))
    _kpi(c3, "BTC Dominance", "N/A" if btc_dom is None else f"{btc_dom:.2f}%")
    _kpi(c4, "ETH Dominance", "N/A" if eth_dom is None else f"{eth_dom:.2f}%")

    if coins is not None or exchanges is not None:
        d1, d2, _, _ = st.columns(4)
        if coins is not None:
            _kpi(d1, "Tracked Coins", fmt_num(coins))
        if exchanges is not None:
            _kpi(d2, "Tracked Exchanges", fmt_num(exchanges))

    return btc_dom, eth_dom


def render_dominance_chart(btc_dom, eth_dom):
    if btc_dom is None or eth_dom is None:
        st.info("Dominance data was not found in the API response.")
        return
    # Accept both 0-1 fractions and 0-100 percentages.
    scale = 100 if (btc_dom + eth_dom) <= 1.0 else 1
    btc, eth = btc_dom * scale, eth_dom * scale
    others = max(0.0, 100 - btc - eth)

    fig = go.Figure(
        go.Pie(
            labels=["Bitcoin", "Ethereum", "Others"],
            values=[btc, eth, others],
            hole=0.55,
            marker=dict(colors=["#F7931A", "#627EEA", "#9CA3AF"]),
            textinfo="label+percent",
            sort=False,
        )
    )
    fig.update_layout(
        title="Market Cap Dominance",
        height=380,
        margin=dict(t=60, b=20, l=20, r=20),
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_all_metrics(payload: dict):
    flat = flatten(payload)
    rows = [
        {"Field": k, "Value": v}
        for k, v in flat.items()
        if v is not None and not isinstance(v, (list, dict))
    ]
    if rows:
        df = pd.DataFrame(rows)
        df["Value"] = df["Value"].astype(str)
        st.dataframe(df, use_container_width=True, hide_index=True, height=380)
    else:
        st.info("No scalar fields found.")


def render_index_card(title: str, payload: dict, steps: list):
    """Gauge (0-100) for an index + a table with the remaining fields."""
    flat = flatten(payload)
    prev_words = ["previous", "prev", "yesterday", "week", "month", "year", "last"]

    key, value = pick(flat, ["value"], exclude=prev_words)
    if value is None:  # fall back to the first numeric field
        for k, v in flat.items():
            num = _to_float(v)
            if num is not None:
                key, value = k, num
                break

    label = pick_text(flat, ["classification", "label", "status", "category", "name"],
                      exclude=prev_words)

    st.subheader(title)
    if value is None:
        st.warning("Could not find a numeric value in the response.")
    else:
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

    rows = [
        {"Field": k, "Value": str(v)}
        for k, v in flat.items()
        if k != key and v is not None and not isinstance(v, (list, dict))
    ]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


FEAR_GREED_STEPS = [
    {"range": [0, 25], "color": "#EF4444"},    # Extreme fear
    {"range": [25, 45], "color": "#F97316"},   # Fear
    {"range": [45, 55], "color": "#EAB308"},   # Neutral
    {"range": [55, 75], "color": "#84CC16"},   # Greed
    {"range": [75, 100], "color": "#22C55E"},  # Extreme greed
]

ALTCOIN_STEPS = [
    {"range": [0, 25], "color": "#F7931A"},    # Bitcoin season
    {"range": [25, 75], "color": "#D1D5DB"},   # Mixed
    {"range": [75, 100], "color": "#3B82F6"},  # Altcoin season
]


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def render():
    st.header("📊 Market Overview")
    st.caption("Global crypto market snapshot and sentiment indices · Source: Cryptorank API v3")

    api_key = _get_api_key()
    if not api_key:
        st.error(
            "API key not found. Add `CRYPTORANK_API_KEY = \"...\"` to "
            "`.streamlit/secrets.toml` (or set it as an environment variable)."
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

    # --- 1) Global market KPIs ------------------------------------------------
    if err_market:
        st.error(f"Global market snapshot failed: {err_market}")
        btc_dom = eth_dom = None
    else:
        btc_dom, eth_dom = render_kpis(market["payload"])

    st.divider()

    # --- 2) Dominance + all snapshot fields ----------------------------------
    if market:
        left, right = st.columns(2)
        with left:
            render_dominance_chart(btc_dom, eth_dom)
        with right:
            st.subheader("All snapshot fields")
            render_all_metrics(market["payload"])
        st.divider()

    # --- 3) Sentiment indices --------------------------------------------------
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

    # --- 4) Raw responses (useful for debugging field names) ------------------
    with st.expander("🛠 Raw API responses"):
        for name, res in (("global/market", market), ("global/fear-greed", fng),
                          ("global/altcoin-index", alt)):
            if res:
                st.markdown(f"**{name}**")
                st.json(res["raw"], expanded=False)
