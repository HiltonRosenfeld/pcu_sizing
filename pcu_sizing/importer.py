"""
Excel import — parses the on-demand metrics Excel export into DbDemand objects.

Expected columns (case-insensitive matching):
  - database name                      → db_id (required)
  - sstable KiB                        → total_data_gb
  - write rps P90 / 7d                 → writes_p90
  - read rps P90 / 7d                  → reads_p90
  - region                             → region
  - engine type                        → engine_type ("cassandra-vector" = vector)

Defaults applied:
  - hot_data_pct = 1.0 (100%)
  - avg_read_kb  = 4
  - avg_write_kb = 1
  - tenancy      = "shared"
"""
from __future__ import annotations

import io
import math
from typing import Union

import pandas as pd

from .catalog import DbDemand


# ---------------------------------------------------------------------------
# Column name normalisation helpers
# ---------------------------------------------------------------------------

def _normalise(s: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return " ".join(s.lower().split())


def _find_col(df: pd.DataFrame, *patterns: str) -> str:
    """Return the first column whose normalised name contains any of patterns."""
    normalised = {col: _normalise(col) for col in df.columns}
    for pattern in patterns:
        for col, norm in normalised.items():
            if pattern in norm:
                return col
    raise KeyError(f"Could not find column matching any of {patterns!r}. "
                   f"Available columns: {list(df.columns)}")


def _try_find_col(df: pd.DataFrame, *patterns: str) -> str | None:
    """Like _find_col but returns None instead of raising."""
    try:
        return _find_col(df, *patterns)
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

def parse_excel(file: Union[str, io.BytesIO]) -> tuple[list[DbDemand], list[str]]:
    """
    Parse the on-demand metrics Excel file.

    Returns:
        (databases, warnings)  where databases is a list of DbDemand objects
        and warnings is a list of human-readable warning strings.
    """
    return _parse_dataframe(pd.read_excel(file, engine="openpyxl"))


def parse_csv(file: Union[str, io.BytesIO]) -> tuple[list[DbDemand], list[str]]:
    """Parse the on-demand metrics CSV file (same columns as the Excel export)."""
    return _parse_dataframe(pd.read_csv(file, encoding="utf-8-sig", sep=None, engine="python"))


def _parse_dataframe(df: pd.DataFrame) -> tuple[list[DbDemand], list[str]]:
    warnings: list[str] = []

    # --- Locate columns ---
    col_sstable   = _find_col(df, "sstable kib", "sstable_kib", "sstable")
    col_writes    = _find_col(df, "write rps p90", "write rps", "writes p90", "write p90")
    col_reads     = _find_col(df, "read rps p90", "read rps", "reads p90", "read p90")
    col_region    = _find_col(df, "region")
    col_engine    = _find_col(df, "engine type", "engine_type", "engine")

    col_db = _find_col(df, "database name")

    # Optional tenancy column
    col_tenancy = _try_find_col(df, "tenancy", "dedicated")

    databases: list[DbDemand] = []

    for idx, row in df.iterrows():
        if pd.isna(row[col_db]) or not str(row[col_db]).strip():
            warnings.append(f"Row {idx+1}: missing database name — skipped.")
            continue
        db_id = str(row[col_db]).strip()

        try:
            sstable_kib = float(row[col_sstable])
        except (ValueError, TypeError):
            warnings.append(f"Row {idx+1} ({db_id}): invalid sstable KiB value — skipped.")
            continue

        total_data_gb = sstable_kib / (1024 * 1024)  # KiB → GiB

        try:
            writes_p90 = float(row[col_writes])
        except (ValueError, TypeError):
            writes_p90 = 0.0
            warnings.append(f"Row {idx+1} ({db_id}): missing write rps P90 — defaulting to 0.")

        try:
            reads_p90 = float(row[col_reads])
        except (ValueError, TypeError):
            reads_p90 = 0.0
            warnings.append(f"Row {idx+1} ({db_id}): missing read rps P90 — defaulting to 0.")

        region = str(row[col_region]).strip() if pd.notna(row[col_region]) else "unknown"
        engine_type = str(row[col_engine]).strip() if pd.notna(row[col_engine]) else ""

        if col_tenancy:
            val = str(row[col_tenancy]).strip().lower()
            tenancy = "dedicated" if val in ("dedicated", "true", "yes", "1") else "shared"
        else:
            tenancy = "shared"

        databases.append(DbDemand(
            db_id=db_id,
            region=region,
            engine_type=engine_type,
            tenancy=tenancy,
            total_data_gb=total_data_gb,
            hot_data_pct=1.0,
            reads_p90=reads_p90,
            avg_read_kb=4.0,
            writes_p90=writes_p90,
            avg_write_kb=1.0,
        ))

    if not databases:
        raise ValueError("No valid database rows found in the uploaded file.")

    return databases, warnings


# ---------------------------------------------------------------------------
# On-screen (manual) entry
# ---------------------------------------------------------------------------

MANUAL_COLUMNS = [
    "Database", "Region", "Engine", "Tenancy",
    "Data size (GB)", "Write rps P90", "Read rps P90",
]


def parse_manual(df: pd.DataFrame) -> tuple[list[DbDemand], list[str]]:
    """
    Convert rows entered on screen (columns as in MANUAL_COLUMNS) into DbDemand objects.

    Fully blank rows are ignored. Rows without a data size are skipped with a warning.
    Missing read/write rates default to 0.
    """
    warnings: list[str] = []
    databases: list[DbDemand] = []

    def _num(val) -> float | None:
        return None if pd.isna(val) else float(val)

    for idx, row in df.iterrows():
        if row.isna().all():
            continue

        name = row.get("Database")
        if pd.isna(name) or not str(name).strip():
            warnings.append(f"Row {idx+1}: missing database name — skipped.")
            continue
        db_id = str(name).strip()

        data_gb = _num(row.get("Data size (GB)"))
        if data_gb is None:
            warnings.append(f"Row {idx+1} ({db_id}): missing data size — skipped.")
            continue

        region = row.get("Region")
        region = str(region).strip() if pd.notna(region) and str(region).strip() else "unknown"
        engine = str(row.get("Engine") or "").strip().lower()
        tenancy = str(row.get("Tenancy") or "").strip().lower()

        databases.append(DbDemand(
            db_id=db_id,
            region=region,
            engine_type="cassandra-vector" if engine == "vector" else "",
            tenancy="dedicated" if tenancy == "dedicated" else "shared",
            total_data_gb=data_gb,
            hot_data_pct=1.0,
            reads_p90=_num(row.get("Read rps P90")) or 0.0,
            avg_read_kb=4.0,
            writes_p90=_num(row.get("Write rps P90")) or 0.0,
            avg_write_kb=1.0,
        ))

    if not databases:
        raise ValueError("No valid database rows entered.")

    return databases, warnings

