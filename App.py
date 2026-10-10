import streamlit as st

import market_explorer
import market_overview

st.set_page_config(page_title="Crypto-Search Dashboard", page_icon="📊", layout="wide")
st.title("Crypto-Search Dashboard")

# --- Builder Info ------------------------------------------------------------------------
st.markdown(
    """
    <div style="margin-top: 25px; font-size: 16px;">
        <div style="display: flex; align-items: center; gap: 10px;">
            <img src="https://pbs.twimg.com/profile_images/2060406047391559681/sA9zPNKM_400x400.jpg" alt="Eman Raz" style="width:25px; height:25px; border-radius: 50%;">
            <span>Built by: <a href="https://x.com/0xeman_raz" target="_blank">Eman Raz</a></span>
        </div>
    </div>
    <!-- Support / Tips Box -->
    <div style="
        background-color: #F5F5F5;
        border-left: 5px solid #888;
        padding: 12px;
        border-radius: 10px;
        margin-top: 10px;
        font-size: 15px;
        color: #333;
    ">
        🎁 <b>Support / Tips:</b><br>
        <code>0x621bd661e3d57da1c8237209824827f1027abf62</code>
    </div>
    """,
    unsafe_allow_html=True,
)

tab_overview, tab_explorer = st.tabs(["📊 Market Overview", "🔍 Market Explorer"])

with tab_overview:
    market_overview.render()

with tab_explorer:
    market_explorer.render()
