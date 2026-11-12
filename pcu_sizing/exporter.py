"""
Excel export — writes the mapping results to a downloadable Excel file.

One sheet per variant + a Summary sheet.
"""
from __future__ import annotations

import io

import pandas as pd

from .catalog import HOURS_PER_MONTH
from .engine import PcuGroup, Mapping, VARIANTS


def _groups_to_df(groups: list[PcuGroup]) -> pd.DataFrame:
    rows = []
    for i, g in enumerate(groups, 1):
        rows.append({
            "Group #": i,
            "PCU Type": g.pcu_type.name,
            "Tenancy": g.pcu_type.tenancy.capitalize(),
            "Environment": g.environment_label,
            "PCU Count (reserved)": g.pcu_count,
            "Databases": ", ".join(g.db_ids),
            "Total Cache (GB)": round(g.total_cache_gb, 2),
            "Cache Capacity (GB)": round(g.pcu_count * g.pcu_type.cache_gb, 2),
            "Cache Util %": f"{g.cache_utilisation:.1%}",
            "Total TPS": round(g.total_tps, 1),
            "TPS Capacity": g.pcu_count * g.tps_ceiling_per_pcu,
            "TPS Util %": f"{g.tps_utilisation:.1%}",
            "Hourly Cost ($)": round(g.hourly_cost, 4),
            "Monthly Cost ($)": round(g.monthly_cost, 2),
            "Annual Cost ($)": round(g.annual_cost, 2),
            "Flag": g.flag,
        })
    return pd.DataFrame(rows)


def _summary_row(variant_name: str, groups: list[PcuGroup], baseline_hourly: float) -> dict:
    total_hourly = sum(g.hourly_cost for g in groups)
    diff = total_hourly - baseline_hourly
    return {
        "Variant": variant_name,
        "# Groups": len(groups),
        "Total PCUs": sum(g.pcu_count for g in groups),
        "Hourly Cost ($)": round(total_hourly, 4),
        "Monthly Cost ($)": round(total_hourly * HOURS_PER_MONTH, 2),
        "Annual Cost ($)": round(total_hourly * HOURS_PER_MONTH * 12, 2),
        "vs Lowest Cost ($/hr)": f"{diff:+.4f}" if variant_name != "Lowest cost" else "—",
        "Flags": sum(1 for g in groups if g.flag),
    }


def build_excel(variant_results: dict[str, list[PcuGroup]]) -> bytes:
    """
    Build an Excel workbook in memory and return the bytes.

    variant_results: dict of variant_name → list[PcuGroup]
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book

        # Formats
        header_fmt = workbook.add_format({"bold": True, "bg_color": "#003F87", "font_color": "#FFFFFF"})
        money_fmt = workbook.add_format({"num_format": "$#,##0.00"})
        pct_fmt = workbook.add_format({"num_format": "0.0%"})
        flag_fmt = workbook.add_format({"font_color": "#CC0000", "bold": True})

        # Summary sheet
        baseline_hourly = sum(g.hourly_cost for g in variant_results.get("Lowest cost", []))
        summary_rows = [
            _summary_row(name, groups, baseline_hourly)
            for name, groups in variant_results.items()
        ]
        summary_df = pd.DataFrame(summary_rows)
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        _style_sheet(writer, "Summary", summary_df, header_fmt)

        # Per-variant sheets
        for variant_name, groups in variant_results.items():
            sheet_name = variant_name[:31]  # Excel 31-char limit
            df = _groups_to_df(groups)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            _style_sheet(writer, sheet_name, df, header_fmt)

    return output.getvalue()


def _style_sheet(writer, sheet_name: str, df: pd.DataFrame, header_fmt):
    """Auto-fit columns and apply header format."""
    worksheet = writer.sheets[sheet_name]
    for col_idx, col_name in enumerate(df.columns):
        max_len = max(len(str(col_name)), df[col_name].astype(str).map(len).max() if len(df) > 0 else 0)
        worksheet.set_column(col_idx, col_idx, min(max_len + 2, 50))
    # Re-write header row with formatting
    for col_idx, col_name in enumerate(df.columns):
        worksheet.write(0, col_idx, col_name, header_fmt)
