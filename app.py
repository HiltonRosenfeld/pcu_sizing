"""
PCU Sizing Tool — Streamlit web app.

Maps Astra DB Serverless on-demand databases onto PCU groups at minimum reserved cost.
"""
from __future__ import annotations

import hashlib
import io
import math
from dataclasses import replace

import pandas as pd
import streamlit as st

from pcu_sizing.benchmarks import (
    ThroughputConfig,
    WORKLOAD_LABELS,
    LATENCY_LABELS,
    RATIO_MODE_LABELS,
    WORKLOAD_DEFAULT,
)
from pcu_sizing.catalog import (
    bucket_databases, HOURS_PER_MONTH, ISOLATE_BY_NAME, ISOLATE_BY_ORG, ISOLATE_NONE, ISOLATION_LABELS,
)
from pcu_sizing.engine import PcuGroup, run_all_variants, VARIANTS
from pcu_sizing.importer import (
    parse_excel, parse_csv, parse_manual, MANUAL_COLUMNS, TPS_BASIS_LABELS,
)
from pcu_sizing.exporter import build_excel
from pcu_sizing.pricing import (
    PRICING_URL, PROVIDERS, REGIONS_URL, PriceBook,
    load_pricing, missing_parts, refresh_pricing, resolve_locations, unmapped_locations,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="PCU Sizing Tool",
    page_icon="💡",
    layout="wide",
)

st.title("PCU Sizing Tool")

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
- `write tps P90` and/or `write tps max` (or `write tps P100`) — write throughput
- `read tps P90` and/or `read tps max` (or `read tps P100`) — read throughput
- `region` — deployment region
- `engine type` — `cassandra-vector` for vector, anything else for non-vector

The file may contain both the P90 and max tps columns; the **TPS basis** setting selects
which pair is used. Only the pair for the selected basis is required. `tps P100` is
treated the same as `tps max` (if both are present, `tps max` is used).

**Optional columns**
- `tenancy` — `dedicated` for dedicated tenancy (default: shared)
- `organization id` — the org each database belongs to. **Required** when isolating by
  Org ID (each org then gets its own PCU groups).

**Production isolation** (Configuration section)
- **DB name** — a database is production when its name has the word `prod` or `production`
  (e.g. `orders_prod`, `production_db`; not `nonprod` or `preprod`). Production and non-production
  databases are never placed in the same PCU group.
- **Org ID** — each organization ID gets its own PCU groups. Production and non-production
  databases of the same org can share a group.
- **No isolation** — databases share PCU groups regardless of name or org.

---
**Utilisation ceilings**  
80% (default) · 60% for "Most headroom" variant

**PCU group target**: ≤ 5 PCUs (hard cap: 10)
    """)

    # st.header("PCU Catalog")
    # catalog_data = [
    #     {"Type": "Small",              "Tenancy": "Shared",    "Cache (GB)": 125,   "TPS (RH/WH)": "15k / 6.6k"},
    #     {"Type": "Medium",             "Tenancy": "Shared",    "Cache (GB)": 250,   "TPS (RH/WH)": "30k / 13.2k"},
    #     {"Type": "Large",              "Tenancy": "Shared",    "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
    #     {"Type": "Large",              "Tenancy": "Dedicated", "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
    #     {"Type": "Cache Optimized",    "Tenancy": "Shared",    "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
    #     {"Type": "Cache Optimized",    "Tenancy": "Dedicated", "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
    # ]
    # st.dataframe(pd.DataFrame(catalog_data), hide_index=True, width="stretch")

    st.header("Pricing")
    with st.expander("Refresh pricing"):
        st.markdown(
            f"Two pages feed the pricing: the [Astra DB pricing page]({PRICING_URL}) (prices) and "
            f"the [Astra DB regions page]({REGIONS_URL}) (each region's geography and pricing "
            "tier). Both block automated downloads, so open each page in your browser, let it "
            "finish loading, then either save it as HTML (**Save Page As**) and upload it, or "
            "copy the rendered HTML (DevTools → Elements → right-click `<html>` → Copy → Copy "
            "outerHTML) and paste it. Either page is optional: a page you leave empty keeps its "
            "cached data. The result is cached in `data/pricing.json`."
        )
        page_inputs = {}
        for key, label in (("pricing", "Pricing page"), ("regions", "Regions page")):
            upload = st.file_uploader(f"{label} HTML", type=["html", "htm"], key=f"{key}_html")
            paste = st.text_area(f"…or paste the {label.lower()} HTML", height=80, key=f"{key}_paste")
            page_inputs[key] = (
                upload.getvalue().decode("utf-8", errors="replace") if upload is not None else paste
            )
        if st.button("Refresh pricing"):
            if not any(v.strip() for v in page_inputs.values()):
                st.warning("Upload or paste at least one of the pages first.")
            else:
                try:
                    refresh_pricing(page_inputs["pricing"], page_inputs["regions"])
                    st.success("Pricing refreshed.")
                except Exception as exc:
                    st.error(f"Could not refresh pricing (existing data kept): {exc}")

    pricing = load_pricing()
    absent = missing_parts(pricing)
    if absent:
        st.error("Missing cached data: " + ", ".join(absent) + ". Refresh pricing above.")
    else:
        st.caption(
            f"Pricing page last updated: {pricing.get('page_last_updated') or 'unknown'}  \n"
            f"Pricing cached: {pricing.get('pricing_refreshed_at', 'unknown')}  \n"
            f"Regions cached: {pricing.get('regions_refreshed_at', 'unknown')}  \n"
            "All costs are in Resource Units (RU)."
        )

if missing_parts(pricing):
    st.warning(
        "Pricing data is incomplete (`data/pricing.json` is missing or lacks the "
        + " / ".join(missing_parts(pricing))
        + "). Use **Refresh pricing** in the sidebar to load it from the saved pages."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Data input (Excel upload or on-screen entry)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Parsing file…")
def load_databases(file_bytes: bytes, file_name: str, tps_basis: str):
    if file_name.lower().endswith(".csv"):
        return parse_csv(io.BytesIO(file_bytes), tps_basis)
    return parse_excel(io.BytesIO(file_bytes), tps_basis)


def _empty_entry_table(rows: int = 3) -> pd.DataFrame:
    return pd.DataFrame({
        "Database": pd.Series([None] * rows, dtype="object"),
        "Provider": pd.Series([None] * rows, dtype="object"),
        "Region": pd.Series([None] * rows, dtype="object"),
        "Engine": pd.Series([None] * rows, dtype="object"),
        "Tenancy": pd.Series([None] * rows, dtype="object"),
        "Org ID": pd.Series([None] * rows, dtype="object"),
        "Data size (GB)": pd.Series([None] * rows, dtype="float64"),
        "Write tps": pd.Series([None] * rows, dtype="float64"),
        "Read tps": pd.Series([None] * rows, dtype="float64"),
    })[MANUAL_COLUMNS]


# Custom styling for configuration cards: equal height and compact controls
st.markdown(
    """
    <style>
    /* Equal height cards and compact layout: stretch every wrapper layer between
       the column and the bordered container (descendant selectors, not strict children) */
    [data-testid="stHorizontalBlock"] {
        align-items: stretch !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        display: flex !important;
        flex-direction: column !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"],
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stVerticalBlockBorderWrapper"] {
        display: flex !important;
        flex-direction: column !important;
        flex: 1 1 auto !important;
        height: 100% !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stVerticalBlockBorderWrapper"] {
        padding: 0.75rem 0.85rem;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] [data-testid="stVerticalBlockBorderWrapper"] > div {
        flex: 1 1 auto !important;
    }
    /* Compact field labels and spacing */
    [data-testid="stVerticalBlockBorderWrapper"] label p {
        font-size: 0.82rem !important;
        font-weight: 500;
        margin-bottom: 2px !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stNumberInput"] input {
        font-size: 0.85rem !important;
        padding-top: 2px !important;
        padding-bottom: 2px !important;
    }
    [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stSelectbox"] div[data-baseweb="select"] {
        font-size: 0.85rem !important;
    }


    </style>
    """,
    unsafe_allow_html=True,
)

st.html("""
<style>
/* 1. Force the dropzone section into a single horizontal row */
[data-testid="stFileUploader"] section {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: flex-start !important;
    gap: 12px !important;
    padding: 10px 16px !important;
}

/* 2. Put the Browse button first on the left */
[data-testid="stFileUploader"] section button {
    order: 1 !important;
    margin: 0 !important;
    flex-shrink: 0 !important;
}

/* 3. Put the instructions container second (to the right of the button) */
[data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzoneInstructions"] * {
    order: 2 !important;
    margin: 0 !important;
    display: flex !important;
    align-items: center !important;
    min-width: 0 !important;
    flex: 1 1 auto !important;
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    word-break: normal !important;
    overflow-wrap: break-word !important;
}

</style>
""")

# Build concise summary for expander title, including the loaded source and database count.
# Computed from session state (before the widgets render) so the header is current on every run.
tps_summary = st.session_state.get("tps_basis_label", TPS_BASIS_LABELS["p90"])
_tps_basis_hdr = next((k for k, v in TPS_BASIS_LABELS.items() if v == tps_summary), "p90")
if st.session_state.get("input_mode", "Upload file") == "Upload file":
    _file_hdr = st.session_state.get("uploaded_metrics_file")
    if _file_hdr is None:
        mode_summary = "No file uploaded"
    else:
        try:
            _dbs_hdr, _ = load_databases(_file_hdr.getvalue(), _file_hdr.name, _tps_basis_hdr)
            mode_summary = f"{_file_hdr.name} ({len(_dbs_hdr)} databases)"
        except Exception:
            mode_summary = f"{_file_hdr.name} (could not be parsed)"
else:
    _manual_hdr = st.session_state.get("manual_df")
    if _manual_hdr is None:
        mode_summary = "On-screen entry (no data calculated)"
    else:
        try:
            _dbs_hdr, _ = parse_manual(_manual_hdr)
            mode_summary = f"On-screen entry ({len(_dbs_hdr)} databases)"
        except Exception:
            mode_summary = "On-screen entry (invalid data)"
workload_summary = WORKLOAD_LABELS.get(st.session_state.get("workload_key", WORKLOAD_DEFAULT), "Catalog")
if st.session_state.get("workload_key", WORKLOAD_DEFAULT) != WORKLOAD_DEFAULT:
    lat_s = LATENCY_LABELS.get(st.session_state.get("latency_val", ""), "")
    ratio_s = RATIO_MODE_LABELS.get(st.session_state.get("ratio_val", ""), "")
    workload_summary = f"{workload_summary} ({lat_s}, {ratio_s})"
prod_c_summary = st.session_state.get("prod_cache_pct", 100.0)
nonprod_c_summary = st.session_state.get("nonprod_cache_pct", 100.0)
iso_summary = f"Isolate by {st.session_state.get('isolation_label') or ISOLATION_LABELS[ISOLATE_BY_NAME]}"
config_summary = f"{mode_summary} · {tps_summary} TPS · {workload_summary} · Cache {prod_c_summary:.0f}%/{nonprod_c_summary:.0f}% · {iso_summary}"

# Settings: Four compact configuration sections in a collapsible expander
with st.expander(f"⚙️ Configuration — {config_summary}", expanded=True):
    card_source, card_tps, card_workload, card_capacity = st.columns([1.1, 0.9, 1.8, 1.4], border=True)

    with card_source.container():
        st.markdown("**Data source**")
        input_mode = st.segmented_control(
            "Data source",
            ["Upload file", "Manual entry"],
            default="Upload file",
            key="input_mode",
            label_visibility="collapsed",
        ) or "Upload file"

        uploaded_file = None
        if input_mode == "Upload file":
            uploaded_file = st.file_uploader(
                "Upload on-demand metrics Excel or CSV file",
                type=["xlsx", "xls", "csv"],
                key="uploaded_metrics_file",
                label_visibility="collapsed",
                help="Upload an on-demand metrics Excel or CSV file.",
            )

    with card_tps.container():
        st.markdown("**TPS basis**")
        tps_basis_label = st.segmented_control(
            "TPS basis",
            [TPS_BASIS_LABELS["p90"], TPS_BASIS_LABELS["max"]],
            default=TPS_BASIS_LABELS["p90"],
            key="tps_basis_label",
            label_visibility="collapsed",
            help="P90: TPS values represent the 90th percentile.\nMaximum: TPS values represent the peak / P100 value.",
        ) or TPS_BASIS_LABELS["p90"]
        tps_basis = next(k for k, v in TPS_BASIS_LABELS.items() if v == tps_basis_label)
        st.caption(f"Using **{tps_basis_label}** throughput values.")

    with card_workload.container():
        st.markdown("**Workload model**")
        workload_key = st.selectbox(
            "Workload type",
            options=list(WORKLOAD_LABELS.keys()),
            format_func=lambda k: WORKLOAD_LABELS[k],
            index=0,
            key="workload_key",
            help="Select standard catalog limits or measured benchmark limits for KV or IoT workloads.",
        )
        if workload_key != WORKLOAD_DEFAULT:
            col_lat, col_ratio = st.columns(2)
            latency_val = col_lat.selectbox(
                "Latency SLA",
                options=list(LATENCY_LABELS.keys()),
                format_func=lambda k: LATENCY_LABELS[k],
                index=0,
                key="latency_val",
                help="Benchmarked latency percentile target.",
            )
            ratio_val = col_ratio.selectbox(
                "Read/Write ratio",
                options=list(RATIO_MODE_LABELS.keys()),
                format_func=lambda k: RATIO_MODE_LABELS[k],
                index=0,
                key="ratio_val",
                help="Dynamic ratio brackets (<40% -> 20/80, 40-70% -> 60/40, >70% -> 80/20) or fixed ratio.",
            )
            throughput_config = ThroughputConfig(
                workload=workload_key,
                latency=latency_val,
                ratio_mode=ratio_val,
            )
        else:
            st.caption("Standard catalog limits (15k/30k/75k TPS).")
            throughput_config = ThroughputConfig(workload=WORKLOAD_DEFAULT)

    with card_capacity.container():
        st.markdown("**Capacity**")
        cache_col_prod, cache_col_nonprod = st.columns(2)
        prod_cache_pct = cache_col_prod.number_input(
            "Prod cache (%)",
            min_value=0.0,
            max_value=100.0,
            value=100.0,
            step=5.0,
            key="prod_cache_pct",
            help="Cache needed = data size × this percentage, for production databases.",
        )
        nonprod_cache_pct = cache_col_nonprod.number_input(
            "Non-prod cache (%)",
            min_value=0.0,
            max_value=100.0,
            value=100.0,
            step=5.0,
            key="nonprod_cache_pct",
            help="Cache needed = data size × this percentage, for non-production databases.",
        )
        isolation_label = st.segmented_control(
            "Isolate production databases by",
            [ISOLATION_LABELS[ISOLATE_BY_NAME], ISOLATION_LABELS[ISOLATE_BY_ORG], ISOLATION_LABELS[ISOLATE_NONE]],
            default=ISOLATION_LABELS[ISOLATE_BY_NAME],
            key="isolation_label",
            help="DB name: databases whose name has the word 'prod' or 'production' get their own PCU "
                 "groups, separate from all others.\nOrg ID: each organization ID gets its own PCU "
                 "groups. Every database must have an organization id.\nNo isolation: databases share "
                 "PCU groups regardless of name or org.",
        ) or ISOLATION_LABELS[ISOLATE_BY_NAME]
        isolation_by = next(k for k, v in ISOLATION_LABELS.items() if v == isolation_label)

if input_mode == "Upload file":
    if uploaded_file is None:
        st.info("Upload an Excel or CSV file above to begin.")
        st.stop()

    try:
        file_bytes = uploaded_file.getvalue()
        databases, warnings = load_databases(file_bytes, uploaded_file.name, tps_basis)
        cache_key = hashlib.md5(file_bytes).hexdigest()
        source_label = uploaded_file.name
    except Exception as exc:
        st.error(f"Failed to parse file: {exc}")
        st.stop()

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
                "Provider": st.column_config.SelectboxColumn("Provider", options=list(PROVIDERS)),
                "Region": st.column_config.TextColumn(
                    "Region", help="Cloud provider region, e.g. us-east-1, europe-west1, westeurope.",
                ),
                "Engine": st.column_config.SelectboxColumn(
                    "Engine", options=["Non-vector", "Vector"],
                ),
                "Tenancy": st.column_config.SelectboxColumn(
                    "Tenancy", options=["Shared", "Dedicated"],
                ),
                "Org ID": st.column_config.TextColumn(
                    "Org ID",
                    help="Organization ID. Required when isolating production databases by Org ID.",
                    required=isolation_by == ISOLATE_BY_ORG,
                ),
                "Data size (GB)": st.column_config.NumberColumn(
                    "Data size (GB)", min_value=0.0, format="%.4f",
                ),
                "Write tps": st.column_config.NumberColumn(
                    "Write tps", min_value=0.0,
                    help="Enter in the selected TPS basis (P90 or maximum).",
                ),
                "Read tps": st.column_config.NumberColumn(
                    "Read tps", min_value=0.0,
                    help="Enter in the selected TPS basis (P90 or maximum).",
                ),
            },
            key="manual_editor",
        )
        submitted = st.form_submit_button("Calculate", type="primary")

    if submitted:
        st.session_state["manual_df"] = edited.reset_index(drop=True)
        st.rerun()  # refresh the Configuration header with the new database count

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

# Map each database's provider/region to its geographic pricing region and tier (regions page)
unmapped = unmapped_locations(pricing, databases)
if unmapped:
    st.warning(
        "These provider / region combinations are not on the Astra DB regions page, so the "
        "databases using them cannot be priced (their groups are flagged \"no pricing "
        "available\"): "
        + ", ".join(f"**{prov or '(no provider)'} / {reg}**" for prov, reg in unmapped)
        + ". Check the provider and region values, or refresh the regions page."
    )
databases = resolve_locations(pricing, databases)
pricebook = PriceBook(pricing)

# Apply the per-environment cache utilisation (copies, so cached parse results are not mutated)
databases = [
    replace(db, hot_data_pct=(prod_cache_pct if db.is_production else nonprod_cache_pct) / 100)
    for db in databases
]


# ---------------------------------------------------------------------------
# Show raw input data
# ---------------------------------------------------------------------------

with st.expander("📋 Input data preview"):
    input_rows = []
    for db in databases:
        input_rows.append({
            "Database": db.db_id,
            "Provider": db.provider,
            "Region": db.region,
            "Pricing region": db.geo_region,
            "Tier": db.tier,
            "Engine": "Vector" if db.is_vector else "Non-vector",
            "Tenancy": db.tenancy.capitalize(),
            "Org ID": db.org_id,
            "Environment": "Production" if db.is_production else "Non-production",
            "Data (GB)": round(db.total_data_gb, 4),
            "Cache needed (GB)": round(db.cache_gb, 4),
            "Read tps": db.reads_tps,
            "Write tps": db.writes_tps,
            "TPS demand": round(db.tps, 1),
        })
    st.dataframe(pd.DataFrame(input_rows), hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
# Run optimiser
# ---------------------------------------------------------------------------

if isolation_by == ISOLATE_BY_ORG:
    missing_org = [db.db_id for db in databases if not db.org_id]
    if missing_org:
        st.error(
            "Isolating by Org ID requires an organization id for every database. Missing for: "
            + ", ".join(missing_org)
        )
        st.stop()

buckets = bucket_databases(databases, isolation_by)

@st.cache_data(show_spinner="Computing PCU mappings…")
def compute_variants(
    db_key: str, isolation_by: str, prod_pct: float, nonprod_pct: float, tps_basis: str,
    workload: str, latency: str, ratio_mode: str,
    pricing_stamp: str,
):
    t_config = ThroughputConfig(workload=workload, latency=latency, ratio_mode=ratio_mode)
    return run_all_variants(buckets, pricebook, throughput_config=t_config)


variant_results = compute_variants(
    cache_key, isolation_by, prod_cache_pct, nonprod_cache_pct, tps_basis,
    throughput_config.workload, throughput_config.latency, throughput_config.ratio_mode,
    pricing.get("refreshed_at", ""),
)


# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

st.subheader("Mapping variants")
#st.caption(body="All values in IBM Resource Units (RU)")

# summary_cols = st.columns(len(VARIANTS))
# for col, (variant_name, _) in zip(summary_cols, VARIANTS):
#     groups = variant_results[variant_name]
#     total_monthly = sum(g.monthly_cost for g in groups)
#     num_groups = len(groups)
#     num_flags = sum(1 for g in groups if g.flag)
#     col.metric(
#         label=variant_name,
#         value=f"{total_monthly:,.0f}",
#         delta=f"{num_groups} group{'s' if num_groups != 1 else ''}"
#               + (f" · ⚠️ {num_flags} flag{'s' if num_flags != 1 else ''}" if num_flags else ""),
#         delta_color="off",
#     )

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
        total_storage = sum(g.storage_monthly_cost or 0.0 for g in groups)
        # c1, c2, c3, c4, c5 = st.columns(5)
        # c1.metric("Groups", len(groups))
        # c2.metric("Hourly cost", f"{total_hourly:,.2f}")
        # c3.metric("Monthly cost", f"{total_monthly:,.0f}")
        # c4.metric("Annual cost", f"{total_annual:,.0f}")
        # c5.metric("Storage / month", f"{total_storage:,.0f}",
        #           help="Data size × storage rate. Shown separately; not part of the PCU cost "
        #                "and not used when choosing the mapping.")
        # if any(g.flag == "no pricing available" for g in groups):
        #     st.warning(
        #         "Some groups have no published price for their provider / region / tier (or their "
        #         "region is not on the regions page). They are "
        #         "flagged \"no pricing available\" and counted as 0 RU in the totals."
        #     )

        # st.divider()

        # Cost summary by component
        _, hourly_col, monthly_col, annual_col = st.columns([1, 2, 2, 2])
        hourly_col.markdown("**RU/hr**", text_alignment="center")
        monthly_col.markdown("**RU/mo**", text_alignment="center")
        annual_col.markdown("**RU/yr**", text_alignment="center")

        pcu_label, pcu_hourly, pcu_monthly, pcu_annual = st.columns([1, 2, 2, 2], vertical_alignment="center")
        pcu_label.markdown("**PCU Groups**")
        pcu_hourly.markdown(f"## {total_hourly:,.2f}", text_alignment="right")
        pcu_monthly.markdown(f"## {total_monthly:,.2f}", text_alignment="right")
        pcu_annual.markdown(f"## {total_annual:,.2f}", text_alignment="right")

        storage_label, storage_hourly, storage_monthly, storage_annual = st.columns([1, 2, 2, 2], vertical_alignment="center")
        storage_label.markdown("**Storage**")
        storage_hourly.markdown(f"### {total_storage / HOURS_PER_MONTH:,.2f}", text_alignment="right")
        storage_monthly.markdown(f"### {total_storage:,.2f}", text_alignment="right")
        storage_annual.markdown(f"### {total_storage * 12:,.2f}", text_alignment="right")

        st.divider()

        # Per-group details
        rows = []
        for i, g in enumerate(groups, 1):
            rows.append({
                "Group": i,
                "PCU Type": f"{g.pcu_type.name} ({g.pcu_type.tenancy.capitalize()})",
                "Provider": g.provider,
                "Region": g.databases[0].region,
                "Pricing region": g.geo_region,
                "Tier": g.tier,
                "Environment": g.environment_label,
                "PCUs": g.pcu_count,
                "Databases": ", ".join(g.db_ids),
                "Cache (GB) (used / available)": f"{g.total_cache_gb:.1f} / {g.pcu_count * g.pcu_type.cache_gb:.0f}",
                "Cache util": g.cache_utilisation,
                "TPS (used / available)": f"{g.total_tps:,.0f} / {g.pcu_count * g.tps_ceiling_per_pcu:,}",
                "TPS util": g.tps_utilisation,
                "RU/hr": g.hourly_cost,
                "RU/mo": g.monthly_cost,
                "Flag": g.flag,
            })

        df = pd.DataFrame(rows)

        def _flag_style(val):
            if val in ("needs account team", "no pricing available"):
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
                "RU/hr": "{:.4f}",
                "RU/mo": "{:,.2f}",
            }, na_rep="n/a")
            .map(_flag_style, subset=["Flag"])
            .map(_util_style, subset=["Cache util", "TPS util"])
        )

        st.dataframe(styled, hide_index=True, width="stretch")

        # Per-group expandable detail
        with st.expander("Group details"):
            for i, g in enumerate(groups, 1):
                st.markdown(f"**Group {i}: {g.pcu_type.name} ({g.pcu_type.tenancy.capitalize()}) × {g.pcu_count} PCU{'s' if g.pcu_count != 1 else ''}**")
                if g.flag:
                    flag_color = "red" if g.flag in ("needs account team", "no pricing available") else "orange"
                    st.markdown(f":{flag_color}[⚠ {g.flag}]")

                detail_rows = []
                for db in g.databases:
                    detail_rows.append({
                        "Database": db.db_id,
                        "Provider": db.provider,
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
                    f"Cost: **{g.hourly_cost:.4f} RU/hr** · **{g.monthly_cost:,.2f} RU/mo**"
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

excel_bytes = build_excel(variant_results, throughput_config=throughput_config)

st.download_button(
    label="⬇️ Download Excel report",
    data=excel_bytes,
    file_name="pcu_sizing_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
