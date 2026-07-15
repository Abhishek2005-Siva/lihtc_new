"""Sidebar -- API key only."""
from __future__ import annotations

import streamlit as st

_NVIDIA_KEY_URL = "https://build.nvidia.com/meta/llama-3_1-70b-instruct"


def render_sidebar() -> dict:
    with st.sidebar:
        st.markdown("## LIHTC Graph Agent")
        st.divider()

        api_key = st.text_input(
            "NVIDIA API Key",
            type="password",
            placeholder="nvapi-...",
        )
        st.link_button("Get a free API key ->", _NVIDIA_KEY_URL, use_container_width=True)

        if api_key:
            st.success("API key set")
        else:
            st.caption("Enter your key above to start.")

    return {
        "api_key": api_key,
        "model": "meta/llama-3.1-70b-instruct",
        "max_retries": 2,
        "show_cypher": True,
    }
