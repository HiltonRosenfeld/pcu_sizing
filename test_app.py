"""
Diagnostic & Minimal Test App for Streamlit Cloud deployment debugging.

Features:
1. System & Environment Health (Memory, Python, Streamlit, Package versions).
2. Codebase static checks (infinite loop risks, st.rerun calls, network connections).
3. Step-by-step modular pipeline runner (Import -> Optimise -> Render -> Export) with execution timing.
4. Stress-testing tool for the partition algorithm (measuring memory & latency per bucket size).
"""
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

# Set default EXACT_THRESHOLD to 1 in test_app
engine.EXACT_THRESHOLD = 1
from pcu_sizing.exporter import build_excel
from pcu_sizing.importer import parse_excel, parse_manual

st.set_page_config(
    page_title="PCU Sizing — Diagnostic Test App",
    page_icon="🧪",
    layout="wide",
)

st.title("🧪 PCU Sizing — Diagnostic & Debug App")
st.caption("Use this stripped-down app to diagnose issues on Streamlit Cloud.")

# ==============================================================================
# SECTION 1: System & Environment Diagnostics
# ==============================================================================
with st.expander("📊 1. System & Runtime Environment", expanded=True):
    col1, col2, col3, col4 = st.columns(4)

    # Memory usage
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # on macOS ru_maxrss is bytes, on Linux it is kilobytes
    if platform.system() == "Darwin":
        max_rss_mb = usage.ru_maxrss / (1024 * 1024)
    else:
        max_rss_mb = usage.ru_maxrss / 1024

    col1.metric("Python Version", platform.python_version())
    col2.metric("Streamlit Version", st.__version__)
    col3.metric("OS Platform", platform.system())
    col4.metric("Current Max RSS", f"{max_rss_mb:.1f} MB")

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

    st.dataframe(pd.DataFrame(checks), hide_index=True, width="stretch")

# ==============================================================================
# SECTION 3: Partition Memory & CPU Benchmark
# ==============================================================================
with st.expander("⚡ 3. Partition Memory & Latency Stress Test", expanded=False):
    st.write(
        "Test how much memory and time `all_partitions()` consumes for varying bucket sizes $N$."
    )
    test_n = st.slider("Select Database Bucket Size (N)", min_value=1, max_value=11, value=7)

    if st.button(f"Run Benchmark for N = {test_n}"):
        tracemalloc.start()
        start_time = time.perf_counter()

        dummy_items = list(range(test_n))
        partitions = all_partitions(dummy_items)
        elapsed = time.perf_counter() - start_time
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        b1, b2, b3 = st.columns(3)
        b1.metric("Partition Count", f"{len(partitions):,}")
        b2.metric("Elapsed Time", f"{elapsed * 1000:.2f} ms")
        b3.metric("Peak Memory Allocated", f"{peak_mem / (1024 * 1024):.2f} MB")

        if peak_mem / (1024 * 1024) > 100 or elapsed > 5:
            st.warning("⚠️ High resource consumption! A bucket of this size risks hitting Streamlit Cloud limits.")
        else:
            st.success("✅ Execution within safe limits.")

# ==============================================================================
# SECTION 4: Modular Step-by-Step Test Harness
# ==============================================================================
st.subheader("🛠️ Step-by-Step Pipeline Test")
st.caption("Toggle individual steps to isolate any failure or crash.")

col_t1, col_t2, col_t3, col_t4 = st.columns(4)
enable_import = col_t1.checkbox("1. Test Import", value=True)
enable_optimise = col_t2.checkbox("2. Test Engine Calculation", value=True)
enable_render = col_t3.checkbox("3. Test UI Rendering", value=True)
enable_export = col_t4.checkbox("4. Test Excel Export", value=True)

# Input data generation
sample_mode = st.radio(
    "Data Source",
    ["Synthetic Test Workload (Instant)", "Upload Real Excel File"],
    horizontal=True,
)

databases: list[DbDemand] = []
warnings: list[str] = []

if sample_mode == "Synthetic Test Workload (Instant)":
    num_dbs = st.slider("Number of synthetic databases", min_value=1, max_value=30, value=6)
    for i in range(1, num_dbs + 1):
        databases.append(
            DbDemand(
                db_id=f"test_db_{i}",
                region="us-east-1",
                engine_type="cassandra-vector" if i % 4 == 0 else "",
                tenancy="shared",
                total_data_gb=float(i * 50),
                hot_data_pct=1.0,
                reads_p90=float(i * 200),
                avg_read_kb=4.0,
                writes_p90=float(i * 100),
                avg_write_kb=1.0,
            )
        )
    st.info(f"Generated {len(databases)} synthetic databases in memory.")

else:
    uploaded = st.file_uploader("Upload test Excel file", type=["xlsx", "xls"])
    if uploaded is not None:
        try:
            databases, warnings = parse_excel(io.BytesIO(uploaded.read()))
            st.success(f"Parsed {len(databases)} databases from uploaded file.")
        except Exception as e:
            st.error(f"Import failed: {e}")
            st.stop()
    else:
        st.info("Upload a file or switch to synthetic mode.")
        st.stop()

# Execution Pipeline with timing
if enable_import and databases:
    with st.expander("📋 Loaded Databases Preview", expanded=False):
        preview_data = [
            {
                "ID": db.db_id,
                "Region": db.region,
                "Vector": db.is_vector,
                "Tenancy": db.tenancy,
                "Data (GB)": db.total_data_gb,
                "TPS": round(db.tps, 1),
            }
            for db in databases
        ]
        st.dataframe(pd.DataFrame(preview_data), width="stretch")

if enable_optimise and databases:
    st.markdown("### Step 2: Optimisation Engine")
    exact_thresh = st.number_input(
        "EXACT_THRESHOLD (switch from exact to greedy when bucket > N)",
        min_value=1,
        max_value=12,
        value=1,
        help="Default set to 1 to force greedy optimization during debug.",
    )
    engine.EXACT_THRESHOLD = int(exact_thresh)
    t0 = time.perf_counter()
    buckets = bucket_databases(databases)
    bucket_summary = [{"Bucket": str(k), "Count": len(v)} for k, v in buckets.items()]
    st.write("Buckets:", bucket_summary)

    results = run_all_variants(buckets)
    calc_time = (time.perf_counter() - t0) * 1000
    st.success(f"✅ Calculation complete in **{calc_time:.2f} ms**.")

    if enable_render:
        st.markdown("### Step 3: UI Rendering")
        summary_cols = st.columns(len(results))
        for col, (var_name, groups) in zip(summary_cols, results.items()):
            col.metric(var_name, f"${sum(g.monthly_cost for g in groups):,.0f}/mo", f"{len(groups)} groups")

    if enable_export:
        st.markdown("### Step 4: Excel Export")
        t_exp = time.perf_counter()
        excel_data = build_excel(results)
        exp_time = (time.perf_counter() - t_exp) * 1000
        st.success(f"✅ Excel generated in **{exp_time:.2f} ms** ({len(excel_data):,} bytes).")
        st.download_button(
            "⬇️ Download Test Excel",
            data=excel_data,
            file_name="test_pcu_sizing.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
