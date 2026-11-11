"""
PCU type catalog and per-database demand calculation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# PCU Catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PcuType:
    name: str            # human-readable label
    tenancy: str         # "shared" | "dedicated"
    rcu_per_hr: float    # reserved (RCU) cost per PCU per hour
    cache_gb: float      # usable cache per PCU (GB)
    tps_read_heavy: int  # TPS ceiling (group read ratio > 75%)
    tps_write_heavy: int # TPS ceiling (group read ratio ≤ 75%)
    allows_dedicated: bool = False
    allows_cache_optimized: bool = False
    is_cache_optimized: bool = False
    vector_allowed: bool = True  # all types allowed for non-vector by default
    small_medium: bool = False   # Small/Medium flag


# Catalog — all PCU types (shared and dedicated variants as separate entries)
CATALOG: list[PcuType] = [
    PcuType(
        name="Small",
        tenancy="shared",
        rcu_per_hr=2.71,
        cache_gb=125,
        tps_read_heavy=15_000,
        tps_write_heavy=6_600,
        small_medium=True,
    ),
    PcuType(
        name="Medium",
        tenancy="shared",
        rcu_per_hr=5.43,
        cache_gb=250,
        tps_read_heavy=30_000,
        tps_write_heavy=13_200,
        small_medium=True,
    ),
    PcuType(
        name="General Purpose",
        tenancy="shared",
        rcu_per_hr=9.20,
        cache_gb=500,
        tps_read_heavy=75_000,
        tps_write_heavy=33_000,
    ),
    PcuType(
        name="General Purpose",
        tenancy="dedicated",
        rcu_per_hr=11.04,
        cache_gb=500,
        tps_read_heavy=75_000,
        tps_write_heavy=33_000,
        allows_dedicated=True,
    ),
    PcuType(
        name="Cache Optimized",
        tenancy="shared",
        rcu_per_hr=14.03,   # 9.20 + 4.83
        cache_gb=2_200,
        tps_read_heavy=75_000,
        tps_write_heavy=33_000,
        is_cache_optimized=True,
    ),
    PcuType(
        name="Cache Optimized",
        tenancy="dedicated",
        rcu_per_hr=19.74,   # 11.04 + 8.70
        cache_gb=2_200,
        tps_read_heavy=75_000,
        tps_write_heavy=33_000,
        is_cache_optimized=True,
        allows_dedicated=True,
    ),
]

# Lookup helpers
def get_pcu_type(name: str, tenancy: str) -> PcuType:
    for p in CATALOG:
        if p.name == name and p.tenancy == tenancy:
            return p
    raise KeyError(f"PCU type not found: {name!r} / {tenancy!r}")


UTIL_CEILING = 0.80          # 80 % utilisation ceiling (default)
UTIL_CEILING_HEADROOM = 0.60  # 60 % for "Most headroom" variant
PCU_TARGET = 5               # soft target per group
PCU_HARD_CAP = 10            # hard cap per group (non-vector)
HOURS_PER_MONTH = 730        # 24 × 365 / 12


# ---------------------------------------------------------------------------
# Per-database demand calculation  (Step 1)
# ---------------------------------------------------------------------------

@dataclass
class DbDemand:
    db_id: str
    region: str
    engine_type: str          # "cassandra-vector" or other
    tenancy: str              # "shared" | "dedicated"
    total_data_gb: float
    hot_data_pct: float       # 0–1
    reads_p90: float          # read rps P90 / 7d
    avg_read_kb: float        # default 4
    writes_p90: float         # write rps P90 / 7d
    avg_write_kb: float       # default 1
    environment: str = ""     # "prod"/"production" = production; anything else = non-production

    @property
    def is_vector(self) -> bool:
        return self.engine_type == "cassandra-vector"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in ("prod", "production")

    @property
    def cache_gb(self) -> float:
        return self.total_data_gb * self.hot_data_pct

    @property
    def tps(self) -> float:
        read_units = math.ceil(self.avg_read_kb / 4)
        write_units = math.ceil(self.avg_write_kb / 1)
        return self.reads_p90 * read_units + self.writes_p90 * write_units

    @property
    def bucket_key(self) -> tuple:
        """Databases sharing a key may share a PCU group."""
        engine = "vector" if self.is_vector else "non-vector"
        return (self.region, engine, self.tenancy, self.is_production)


# ---------------------------------------------------------------------------
# Bucket a list of DbDemand objects into groups with the same key
# ---------------------------------------------------------------------------

def bucket_databases(databases: list[DbDemand]) -> dict[tuple, list[DbDemand]]:
    buckets: dict[tuple, list[DbDemand]] = {}
    for db in databases:
        buckets.setdefault(db.bucket_key, []).append(db)
    return buckets
