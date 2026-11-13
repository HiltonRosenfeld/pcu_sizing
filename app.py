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

from pcu_sizing.catalog import bucket_databases, HOURS_PER_MONTH
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
        {"Type": "Small",              "Tenancy": "Shared",    "Cache (GB)": 125,   "TPS (RH/WH)": "15k / 6.6k"},
        {"Type": "Medium",             "Tenancy": "Shared",    "Cache (GB)": 250,   "TPS (RH/WH)": "30k / 13.2k"},
        {"Type": "Large",              "Tenancy": "Shared",    "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
        {"Type": "Large",              "Tenancy": "Dedicated", "Cache (GB)": 500,   "TPS (RH/WH)": "75k / 33k"},
        {"Type": "Cache Optimized",    "Tenancy": "Shared",    "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
        {"Type": "Cache Optimized",    "Tenancy": "Dedicated", "Cache (GB)": 2_200, "TPS (RH/WH)": "75k / 33k"},
    ]
    st.dataframe(pd.DataFrame(catalog_data), hide_index=True, width="stretch")

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
        "Environment": pd.Series([None] * rows, dtype="object"),
        "Data size (GB)": pd.Series([None] * rows, dtype="float64"),
        "Write tps": pd.Series([None] * rows, dtype="float64"),
        "Read tps": pd.Series([None] * rows, dtype="float64"),
    })[MANUAL_COLUMNS]


# Settings: four bordered cards side by side, one per setting group
card_input, card_tps, card_cache, card_grouping = st.columns([1, 1, 1.6, 1])

with card_input.container(border=True):
    st.markdown("**Data input**")
    input_mode = st.radio(
        "Data input",
        ["Upload Excel/CSV file", "Enter data on screen"],
        label_visibility="collapsed",
    )

with card_tps.container(border=True):
    st.markdown("**TPS basis**")
    tps_basis_label = st.radio(
        "TPS basis",
        [TPS_BASIS_LABELS["p90"], TPS_BASIS_LABELS["max"]],
        captions=[
            "tps specified as p90.",
            "tps specified as max value."
        ],
        label_visibility="collapsed",
    )
    tps_basis = next(k for k, v in TPS_BASIS_LABELS.items() if v == tps_basis_label)

with card_cache.container(border=True):
    st.markdown("**Cache utilisation** (% of data held in cache)")
    cache_col_prod, cache_col_nonprod = st.columns(2)
    prod_cache_pct = cache_col_prod.number_input(
        "Production (%)", min_value=0.0, max_value=100.0, value=100.0, step=5.0,
        help="Cache needed = data size × this percentage, for production databases.",
    )
    nonprod_cache_pct = cache_col_nonprod.number_input(
        "Non-production (%)", min_value=0.0, max_value=100.0, value=100.0, step=5.0,
        help="Cache needed = data size × this percentage, for non-production databases.",
    )

with card_grouping.container(border=True):
    st.markdown("**Grouping**")
    use_environment = st.toggle(
        "Isolate production databases",
        value=True,
        help="When on, databases with environment 'prod' or 'production' are placed in their own "
             "PCU groups, separate from all other (non-production) databases. When off, the "
             "environment is ignored when grouping.",
    )

if input_mode == "Upload Excel/CSV file":
    uploaded_file = st.file_uploader(
        "Upload on-demand metrics Excel or CSV file",
        type=["xlsx", "xls", "csv"],
    )

    if uploaded_file is None:
        st.info("Upload an Excel or CSV file above to begin.")
        st.stop()

    try:
        file_bytes = uploaded_file.read()
        databases, warnings = load_databases(file_bytes, uploaded_file.name, tps_basis)
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
                "Environment": st.column_config.TextColumn(
                    "Environment",
                    help="'prod' or 'production' = production; anything else = non-production.",
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

buckets = bucket_databases(databases, use_environment)

@st.cache_data(show_spinner="Computing PCU mappings…")
def compute_variants(
    db_key: str, use_environment: bool, prod_pct: float, nonprod_pct: float, tps_basis: str,
    pricing_stamp: str,
):
    return run_all_variants(buckets, pricebook)


variant_results = compute_variants(
    cache_key, use_environment, prod_cache_pct, nonprod_cache_pct, tps_basis,
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

excel_bytes = build_excel(variant_results)

st.download_button(
    label="⬇️ Download Excel report",
    data=excel_bytes,
    file_name="pcu_sizing_results.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
