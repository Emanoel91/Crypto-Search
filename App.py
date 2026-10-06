import streamlit as st

import market_overview

st.set_page_config(page_title="Cryptorank Dashboard", page_icon="📊", layout="wide")
st.title("Cryptorank Dashboard")

tab_overview, = st.tabs(["📊 Market Overview"])

with tab_overview:
    market_overview.render()
