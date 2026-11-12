"""
PCU Sizing Tool — Streamlit web app.

Maps Astra DB Serverless on-demand databases onto PCU groups at minimum reserved cost.
"""
from __future__ import annotations

import hashlib
import io
import math

import pandas as pd
import streamlit as st

from pcu_sizing.catalog import bucket_databases, HOURS_PER_MONTH
from pcu_sizing.engine import PcuGroup, run_all_variants, VARIANTS
from pcu_sizing.importer import parse_excel, parse_csv, parse_manual, MANUAL_COLUMNS
from pcu_sizing.exporter import build_excel

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="PCU Sizing Tool",
    page_icon="💡",
    layout="wide",
)

st.title("PCU Sizing Tool")
st.caption("Maps Astra DB Serverless databases onto PCU groups at minimum reserved cost.")

# ---------------------------------------------------------------------------
# Sidebar — about / help
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("About")
    st.markdown("""
**PCU Sizing Tool** maps existing Astra DB on-demand databases onto
[PCU Groups](https://docs.datastax.com/en/astra-db-serverless/administration/provisioned-capacity-units.html)
at the lowest reserved (RCU) cost.

**Upload** an Excel or CSV export of your on-demand metrics, or **enter data on screen**
for small data sets.
The tool will show four mapping variants side by side.

---
**Required columns** (Excel or CSV)
- `Database name`
- `sstable KiB` — total data size (unreplicated, compressed)
- `write rps P90 / 7d` — write throughput at P90
- `read rps P90 / 7d` — read throughput at P90
- `region` — deployment region
- `engine type` — `cassandra-vector` for vector, anything else for non-vector

**Optional columns**
- `tenancy` — `dedicated` for dedicated tenancy (default: shared)
- `environment` — `prod` or `production` for production databases; any other value
  (or blank) is non-production. Production and non-production databases are never
  placed in the same PCU group.

---
**Utilisation ceilings**  
80% (default) · 60% for "Most headroom" variant

**PCU group target**: ≤ 5 PCUs (hard cap: 10)
    """)

    st.header("PCU Catalog")
    catalog_data = [
        {"Type": "Small",              "Tenancy": "Shared",    "RCU $/hr": 2.71,  "Cache (GB)": 125,   "TPS (RH/WH)": "15k / 6.6k"},
        {"Type": "Medium",             "Tenancy": "Shared",    "RCU $/hr": 5.43,  "Cache (GB)": 250,   "TPS (RH/WH)": "30k / 13.2k"},
        {"Type": "General Purpose",    "Tenancy": "Shared",    "RCU $/hr": 9.20,  "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
        {"Type": "General Purpose",    "Tenancy": "Dedicated", "RCU $/hr": 11.04, "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
        {"Type": "Cache Optimized",    "Tenancy": "Shared",    "RCU $/hr": 14.03, "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
        {"Type": "Cache Optimized",    "Tenancy": "Dedicated", "RCU $/hr": 19.74, "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
    ]
    st.dataframe(pd.DataFrame(catalog_data), hide_index=True, width="stretch")



# ---------------------------------------------------------------------------
# Data input (Excel upload or on-screen entry)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Parsing file…")
def load_databases(file_bytes: bytes, file_name: str):
    if file_name.lower().endswith(".csv"):
        return parse_csv(io.BytesIO(file_bytes))
    return parse_excel(io.BytesIO(file_bytes))


def _empty_entry_table(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "Database": pd.Series([None] * rows, dtype="object"),
        "Region": pd.Series([None] * rows, dtype="object"),
        "Engine": pd.Series([None] * rows, dtype="object"),
        "Tenancy": pd.Series([None] * rows, dtype="object"),
        "Environment": pd.Series([None] * rows, dtype="object"),
        "Data size (GB)": pd.Series([None] * rows, dtype="float64"),
        "Write rps P90": pd.Series([None] * rows, dtype="float64"),
        "Read rps P90": pd.Series([None] * rows, dtype="float64"),
    })[MANUAL_COLUMNS]


use_environment = st.toggle(
    "Group production and non-production databases separately",
    value=True,
    help="When on, databases with environment 'prod' or 'production' are placed in their own "
         "PCU groups, separate from all other (non-production) databases. When off, the "
         "environment is ignored when grouping.",
)

input_mode = st.radio(
    "Data input",
    ["Upload Excel/CSV file", "Enter data on screen"],
    horizontal=True,
)

if input_mode == "Upload Excel/CSV file":
    uploaded_file = st.file_uploader(
        "Upload on-demand metrics Excel or CSV file",
        type=["xlsx", "xls", "csv"],
        help="Excel or CSV export of on-demand Astra DB metrics.",
    )

    if uploaded_file is None:
        st.info("Upload an Excel or CSV file above to begin.")
        st.stop()

    try:
        file_bytes = uploaded_file.read()
        databases, warnings = load_databases(file_bytes, uploaded_file.name)
    except Exception as exc:
        st.error(f"Failed to parse file: {exc}")
        st.stop()

    cache_key = hashlib.md5(file_bytes).hexdigest()
    source_label = uploaded_file.name

else:
    st.caption(
        "Add one row per database (use the empty row at the bottom to add more; "
        "select a row and press Delete to remove it). Data size is the total "
        "unreplicated, compressed size in GB. Click **Calculate** when done."
    )
    with st.form("manual_entry"):
        edited = st.data_editor(
            _empty_entry_table(),
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "Database": st.column_config.TextColumn("Database name", required=True),
                "Region": st.column_config.TextColumn("Region"),
                "Engine": st.column_config.SelectboxColumn(
                    "Engine", options=["Non-vector", "Vector"],
                ),
                "Tenancy": st.column_config.SelectboxColumn(
                    "Tenancy", options=["Shared", "Dedicated"],
                ),
                "Environment": st.column_config.TextColumn(
                    "Environment",
                    help="'prod' or 'production' = production; anything else = non-production.",
                ),
                "Data size (GB)": st.column_config.NumberColumn(
                    "Data size (GB)", min_value=0.0, format="%.4f",
                ),
                "Write rps P90": st.column_config.NumberColumn(
                    "Write rps P90", min_value=0.0,
                ),
                "Read rps P90": st.column_config.NumberColumn(
                    "Read rps P90", min_value=0.0,
                ),
            },
            key="manual_editor",
        )
        submitted = st.form_submit_button("Calculate", type="primary")

    if submitted:
        st.session_state["manual_df"] = edited.reset_index(drop=True)

    if "manual_df" not in st.session_state:
        st.info("Enter your databases above and click Calculate to begin.")
        st.stop()

    manual_df = st.session_state["manual_df"]
    try:
        databases, warnings = parse_manual(manual_df)
    except Exception as exc:
        st.error(f"Could not use entered data: {exc}")
        st.stop()

    cache_key = hashlib.md5(manual_df.to_csv().encode()).hexdigest()
    source_label = "on-screen entry"

if warnings:
    with st.expander(f"⚠️ {len(warnings)} import warning(s)"):
        for w in warnings:
            st.warning(w)

st.success(f"Loaded **{len(databases)}** database(s) from **{source_label}**.")


# ---------------------------------------------------------------------------
# Show raw input data
# ---------------------------------------------------------------------------

with st.expander("📋 Input data preview"):
    input_rows = []
    for db in databases:
        input_rows.append({
            "Database": db.db_id,
            "Region": db.region,
            "Engine": "Vector" if db.is_vector else "Non-vector",
            "Tenancy": db.tenancy.capitalize(),
            "Environment": "Production" if db.is_production else "Non-production",
            "Data (GB)": round(db.total_data_gb, 4),
            "Cache needed (GB)": round(db.cache_gb, 4),
            "Reads P90": db.reads_p90,
            "Writes P90": db.writes_p90,
            "TPS demand": round(db.tps, 1),
        })
    st.dataframe(pd.DataFrame(input_rows), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Run optimiser
# ---------------------------------------------------------------------------

buckets = bucket_databases(databases, use_environment)

@st.cache_data(show_spinner="Computing PCU mappings…")
def compute_variants(db_key: str, use_environment: bool):
    return run_all_variants(buckets)


variant_results = compute_variants(cache_key, use_environment)


# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

st.subheader("Mapping variants")

summary_cols = st.columns(len(VARIANTS))
for col, (variant_name, _) in zip(summary_cols, VARIANTS):
    groups = variant_results[variant_name]
    total_monthly = sum(g.monthly_cost for g in groups)
    num_groups = len(groups)
    num_flags = sum(1 for g in groups if g.flag)
    col.metric(
        label=variant_name,
        value=f"${total_monthly:,.0f}/mo",
        delta=f"{num_groups} group{'s' if num_groups != 1 else ''}"
              + (f" · ⚠️ {num_flags} flag{'s' if num_flags != 1 else ''}" if num_flags else ""),
        delta_color="off",
    )


# ---------------------------------------------------------------------------
# Per-variant tabs
# ---------------------------------------------------------------------------

tab_objects = st.tabs([name for name, _ in VARIANTS])

for tab, (variant_name, _) in zip(tab_objects, VARIANTS):
    with tab:
        groups = variant_results[variant_name]

        if not groups:
            st.warning("No valid mapping found for this variant.")
            continue

        total_hourly = sum(g.hourly_cost for g in groups)
        total_monthly = total_hourly * HOURS_PER_MONTH
        total_annual = total_monthly * 12

        # Cost summary
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Groups", len(groups))
        c2.metric("Hourly cost", f"${total_hourly:,.4f}")
        c3.metric("Monthly cost", f"${total_monthly:,.2f}")
        c4.metric("Annual cost", f"${total_annual:,.2f}")

        st.divider()

        # Per-group details
        rows = []
        for i, g in enumerate(groups, 1):
            rows.append({
                "Group": i,
                "PCU Type": f"{g.pcu_type.name} ({g.pcu_type.tenancy.capitalize()})",
                "Region": g.databases[0].region,
                "Environment": g.environment_label,
                "PCUs": g.pcu_count,
                "Databases": ", ".join(g.db_ids),
                "Cache (GB) (used / available)": f"{g.total_cache_gb:.1f} / {g.pcu_count * g.pcu_type.cache_gb:.0f}",
                "Cache util": g.cache_utilisation,
                "TPS (used / available)": f"{g.total_tps:,.0f} / {g.pcu_count * g.tps_ceiling_per_pcu:,}",
                "TPS util": g.tps_utilisation,
                "$/hr": g.hourly_cost,
                "$/mo": g.monthly_cost,
                "Flag": g.flag,
            })

        df = pd.DataFrame(rows)

        def _flag_style(val):
            if val == "needs account team":
                return "background-color: #ffdddd; color: #cc0000; font-weight: bold"
            if val == "no headroom":
                return "background-color: #fff3cd; color: #856404; font-weight: bold"
            return ""

        def _util_style(val):
            if val >= 0.80:
                return "background-color: #ffdddd"
            if val >= 0.60:
                return "background-color: #fff3cd"
            return ""

        styled = (
            df.style
            .format({
                "Cache util": "{:.1%}",
                "TPS util": "{:.1%}",
                "$/hr": "${:.4f}",
                "$/mo": "${:,.2f}",
            })
            .map(_flag_style, subset=["Flag"])
            .map(_util_style, subset=["Cache util", "TPS util"])
        )

        st.dataframe(styled, hide_index=True, width="stretch")

        # Per-group expandable detail
        with st.expander("Group details"):
            for i, g in enumerate(groups, 1):
                st.markdown(f"**Group {i}: {g.pcu_type.name} ({g.pcu_type.tenancy.capitalize()}) × {g.pcu_count} PCU{'s' if g.pcu_count != 1 else ''}**")
                if g.flag:
                    flag_color = "red" if g.flag == "needs account team" else "orange"
                    st.markdown(f":{flag_color}[⚠ {g.flag}]")

                detail_rows = []
                for db in g.databases:
                    detail_rows.append({
                        "Database": db.db_id,
                        "Region": db.region,
                        "Engine": "Vector" if db.is_vector else "Non-vector",
                        "Environment": "Production" if db.is_production else "Non-production",
                        "Cache (GB)": round(db.cache_gb, 4),
                        "TPS demand": round(db.tps, 1),
                    })
                st.dataframe(pd.DataFrame(detail_rows), hide_index=True, width="stretch")

                cap_cache = g.pcu_count * g.pcu_type.cache_gb
                cap_tps = g.pcu_count * g.tps_ceiling_per_pcu
                st.markdown(
                    f"Cache: **{g.total_cache_gb:.1f} GB** / {cap_cache:.0f} GB "
                    f"({g.cache_utilisation:.1%})  |  "
                    f"TPS: **{g.total_tps:,.0f}** / {cap_tps:,} "
                    f"({g.tps_utilisation:.1%})  |  "
                    f"Cost: **${g.hourly_cost:.4f}/hr** · **${g.monthly_cost:,.2f}/mo**"
                )
                st.divider()


# ---------------------------------------------------------------------------
# Excluded databases (need account team)
# ---------------------------------------------------------------------------

excluded_groups = [g for g in variant_results["Lowest cost"] if g.flag == "needs account team"]

if excluded_groups:
    st.subheader("⛔ Databases excluded from sizing — account team required")
    st.markdown(
        "The following databases exceed the PCU group hard cap (10 PCUs for non-vector, "
        "1 PCU for vector) and **cannot be placed** with current PCU sizing. "
        "These need to be reviewed with the DataStax/IBM account team."
    )
    excluded_rows = []
    for g in excluded_groups:
        for db in g.databases:
            excluded_rows.append({
                "Database": db.db_id,
                "Region": db.region,
                "Engine": "Vector" if db.is_vector else "Non-vector",
                "Tenancy": db.tenancy.capitalize(),
                "Environment": "Production" if db.is_production else "Non-production",
                "Cache needed (GB)": round(db.cache_gb, 2),
                "TPS demand": round(db.tps, 1),
                "PCU Type attempted": g.pcu_type.name,
            })
    st.dataframe(pd.DataFrame(excluded_rows), hide_index=True, width="stretch")
else:
    st.success("✅ All databases fit within PCU group limits.")

# ---------------------------------------------------------------------------
# Download button
# ---------------------------------------------------------------------------

st.subheader("Download results")

excel_bytes = build_excel(variant_results)

st.download_button(
    label="⬇️ Download Excel report",
    data=excel_bytes,
    file_name="pcu_sizing_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
