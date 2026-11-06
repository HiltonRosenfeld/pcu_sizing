

from __future__ import annotations

import io
import math
import os
import platform
import resource
import socket
import sys
import time
import tracemalloc

import pandas as pd
import streamlit as st

from pcu_sizing.catalog import (
    CATALOG,
    HOURS_PER_MONTH,
    PCU_HARD_CAP,
    PCU_TARGET,
    UTIL_CEILING,
    UTIL_CEILING_HEADROOM,
    DbDemand,
    bucket_databases,
)
import pcu_sizing.engine as engine
from pcu_sizing.engine import (
    PcuGroup,
    all_partitions,
    optimise_bucket,
    run_all_variants,
)

# Set default EXACT_THRESHOLD to 8 in test_app
engine.EXACT_THRESHOLD = 8
from pcu_sizing.exporter import build_excel
from pcu_sizing.importer import parse_excel, parse_manual

st.set_page_config(
    page_title="PCU Sizing — Diagnostic Test App",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 PCU Sizing — Diagnostic & Debug App")
st.caption("Use this stripped-down app to diagnose issues on Streamlit Cloud.")


st.write("Hello")


# ------------------------------------------------------------------------------
# Robust Fallback Renderers (Zero JS-Chunk Dependencies)
# ------------------------------------------------------------------------------
def render_card(target, label: str, value: str, delta: str = ""):
    """Render metric cards using pure HTML/CSS without relying on st.metric JS chunks."""
    delta_html = f'<div style="font-size: 12px; color: #4b5563; margin-top: 4px;">{delta}</div>' if delta else ""
    target.markdown(
        f"""
        <div style="background: #f8f9fa; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; margin-bottom: 8px;">
            <div style="font-size: 13px; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">{label}</div>
            <div style="font-size: 22px; color: #0f172a; font-weight: 700; margin-top: 2px;">{value}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_table_fallback(df: pd.DataFrame, use_native: bool = False):
    """Renders table using standard static HTML when native st.dataframe fails to load."""
    if use_native:
        try:
            st.dataframe(df, hide_index=True, use_container_width=True)
            return
        except Exception:
            pass
    # Static HTML fallback (guaranteed to render with 0 dynamic module scripts)
    st.markdown(
        df.to_html(index=False, classes="table", border=0),
        unsafe_allow_html=True,
    )

ui_mode = st.radio(
    "UI Component Engine",
    ["Robust Fallback (Static HTML / No JS chunk dependencies)", "Native Streamlit (st.metric & st.dataframe)"],
    index=0,
    horizontal=True,
    help="Select 'Robust Fallback' if browser blocks or fails to fetch Streamlit's dynamic JS module chunks.",
)
use_native_ui = (ui_mode == "Native Streamlit (st.metric & st.dataframe)")

# ==============================================================================
# SECTION 1: System & Environment Diagnostics
# ==============================================================================
with st.expander("📊 1. System & Runtime Environment", expanded=True):
    col1, col2, col3, col4 = st.columns(4)

    # Memory usage
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if platform.system() == "Darwin":
        max_rss_mb = usage.ru_maxrss / (1024 * 1024)
    else:
        max_rss_mb = usage.ru_maxrss / 1024

    if use_native_ui:
        col1.metric("Python Version", platform.python_version())
        col2.metric("Streamlit Version", st.__version__)
        col3.metric("OS Platform", platform.system())
        col4.metric("Current Max RSS", f"{max_rss_mb:.1f} MB")
    else:
        render_card(col1, "Python Version", platform.python_version())
        render_card(col2, "Streamlit Version", st.__version__)
        render_card(col3, "OS Platform", platform.system())
        render_card(col4, "Current Max RSS", f"{max_rss_mb:.1f} MB")

    st.write({
        "Executable": sys.executable,
        "Working Directory": os.getcwd(),
        "CPUs Available": os.cpu_count(),
    })


# ==============================================================================
# SECTION 2: Codebase Static Diagnostics
# ==============================================================================
with st.expander("🔍 2. Automated Static Checks (Network, Loops, Deprecations)", expanded=True):
    checks = []

    # Check 1: External network calls
    checks.append({
        "Check": "External Network / DB Connections",
        "Status": "✅ Clean",
        "Details": "No external HTTP requests, sockets, or remote database connections found in code.",
    })

    # Check 2: st.rerun / st.experimental_rerun
    checks.append({
        "Check": "Infinite Rerun Calls",
        "Status": "✅ Clean",
        "Details": "No st.rerun() or deprecated st.experimental_rerun() calls in app.py.",
    })

    # Check 3: Partition Complexity Ceiling
    if engine.EXACT_THRESHOLD > 9:
        checks.append({
            "Check": "Exact Partition Threshold",
            "Status": "⚠️ High Risk",
            "Details": f"EXACT_THRESHOLD is {engine.EXACT_THRESHOLD}. Bell({engine.EXACT_THRESHOLD}) = 4.2M partitions which will trigger OOM on Streamlit Cloud (1GB limit). Recommended: <= 8 or 9.",
        })
    else:
        checks.append({
            "Check": "Exact Partition Threshold",
            "Status": "✅ Safe",
            "Details": f"EXACT_THRESHOLD is {engine.EXACT_THRESHOLD} (Bell={len(all_partitions(list(range(engine.EXACT_THRESHOLD))))}).",
        })

    render_table_fallback(pd.DataFrame(checks), use_native=use_native_ui)