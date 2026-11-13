"""
Optimisation engine — maps databases onto PCU groups.

Step 4: Cost-minimising grouping within each bucket.

Strategy:
  - Exact search (try all partitions) when bucket size ≤ EXACT_THRESHOLD.
  - Greedy heuristic fallback for larger buckets.

For each candidate group the minimum feasible PCU count and type are evaluated,
and the combination minimising total hourly RCU cost is selected.  Rates come from a
PriceBook (provider / geographic region / tier, all fixed per bucket); a PCU type with no published rate is
unavailable, and a group with no priced type is flagged "no pricing available" and costs 0.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Optional

from .pricing import PriceBook
from .catalog import (
    CATALOG,
    DbDemand,
    PcuType,
    UTIL_CEILING,
    UTIL_CEILING_HEADROOM,
    PCU_TARGET,
    PCU_HARD_CAP,
    HOURS_PER_MONTH,
    get_pcu_type,
)

FLAG_NO_PRICING = "no pricing available"

EXACT_THRESHOLD = 6  # exact partition search up to this many databases per bucket

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PcuGroup:
    pcu_type: PcuType
    pcu_count: int                       # reserved PCUs
    databases: list[DbDemand]
    flag: str = ""                       # "no headroom" | "needs account team" | "no pricing available" | ""
    rate_per_pcu: Optional[float] = None  # reserved RU per PCU per hour; None = no price published
    storage_rate: Optional[float] = None  # RU per GB stored per month; None = no price published

    @property
    def total_cache_gb(self) -> float:
        return sum(db.cache_gb for db in self.databases)

    @property
    def total_tps(self) -> float:
        return sum(db.tps for db in self.databases)

    @property
    def group_read_ratio(self) -> float:
        total_reads = sum(db.reads_tps for db in self.databases)
        total_rw = sum(db.reads_tps + db.writes_tps for db in self.databases)
        if total_rw == 0:
            return 1.0
        return total_reads / total_rw

    @property
    def tps_ceiling_per_pcu(self) -> int:
        if self.group_read_ratio > 0.75:
            return self.pcu_type.tps_read_heavy
        return self.pcu_type.tps_write_heavy

    @property
    def cache_utilisation(self) -> float:
        cap = self.pcu_count * self.pcu_type.cache_gb
        return self.total_cache_gb / cap if cap > 0 else 0.0

    @property
    def tps_utilisation(self) -> float:
        cap = self.pcu_count * self.tps_ceiling_per_pcu
        return self.total_tps / cap if cap > 0 else 0.0

    @property
    def hourly_cost(self) -> float:
        """Reserved PCU cost (RU/hr); 0 when no price is published."""
        if self.rate_per_pcu is None:
            return 0.0
        return self.pcu_count * self.rate_per_pcu

    @property
    def comparison_cost(self) -> float:
        """Cost used to rank candidates: hourly cost, or PCU count when no price is published."""
        return float(self.pcu_count) if self.rate_per_pcu is None else self.hourly_cost

    @property
    def provider(self) -> str:
        return self.databases[0].provider

    @property
    def geo_region(self) -> str:
        return self.databases[0].geo_region

    @property
    def tier(self) -> str:
        return self.databases[0].tier

    @property
    def total_data_gb(self) -> float:
        return sum(db.total_data_gb for db in self.databases)

    @property
    def storage_monthly_cost(self) -> Optional[float]:
        """Storage cost (RU/month) = data GB × storage rate; None when no rate is published."""
        if self.storage_rate is None:
            return None
        return self.total_data_gb * self.storage_rate

    @property
    def monthly_cost(self) -> float:
        return self.hourly_cost * HOURS_PER_MONTH

    @property
    def annual_cost(self) -> float:
        return self.monthly_cost * 12

    @property
    def db_ids(self) -> list[str]:
        return [db.db_id for db in self.databases]

    @property
    def environment_label(self) -> str:
        """Production / Non-production, or Mixed when environment grouping is off."""
        labels = {"Production" if db.is_production else "Non-production" for db in self.databases}
        return labels.pop() if len(labels) == 1 else "Mixed"


@dataclass
class Mapping:
    """A complete mapping of a bucket's databases onto PCU groups."""
    groups: list[PcuGroup] = field(default_factory=list)

    @property
    def total_hourly_cost(self) -> float:
        return sum(g.hourly_cost for g in self.groups)

    @property
    def total_monthly_cost(self) -> float:
        return self.total_hourly_cost * HOURS_PER_MONTH

    @property
    def total_annual_cost(self) -> float:
        return self.total_monthly_cost * 12

    @property
    def num_groups(self) -> int:
        return len(self.groups)


# ---------------------------------------------------------------------------
# Eligible PCU types for a given database / group
# ---------------------------------------------------------------------------

def eligible_types(tenancy: str, is_vector: bool, large_only: bool = False) -> list[PcuType]:
    """Return eligible PcuType objects for a group with given tenancy and engine."""
    result = []
    for pcu in CATALOG:
        # Vector databases → Cache Optimized only
        if is_vector and not pcu.is_cache_optimized:
            continue
        # Dedicated tenancy → only dedicated types (Large or Cache Optimized)
        if tenancy == "dedicated" and pcu.tenancy != "dedicated":
            continue
        if tenancy == "shared" and pcu.tenancy == "dedicated":
            continue
        # Large only variant — exclude Small and Medium
        if large_only and pcu.small_medium:
            continue
        result.append(pcu)
    return result


# ---------------------------------------------------------------------------
# Minimum feasible PCU count for a group of databases under a given PCU type
# ---------------------------------------------------------------------------

def min_pcus(databases: list[DbDemand], pcu_type: PcuType, util_ceiling: float) -> int:
    """
    Return minimum PCU count for a group, or None if a single database exceeds
    the hard cap (caller handles that as a flag).
    """
    total_cache = sum(db.cache_gb for db in databases)
    total_tps = sum(db.tps for db in databases)

    total_reads = sum(db.reads_tps for db in databases)
    total_rw = sum(db.reads_tps + db.writes_tps for db in databases)
    read_ratio = total_reads / total_rw if total_rw > 0 else 1.0
    tps_per_pcu = pcu_type.tps_read_heavy if read_ratio > 0.75 else pcu_type.tps_write_heavy

    cache_pcus = math.ceil(total_cache / (util_ceiling * pcu_type.cache_gb))
    tps_pcus = math.ceil(total_tps / (util_ceiling * tps_per_pcu))
    return max(cache_pcus, tps_pcus, 1)


# ---------------------------------------------------------------------------
# Evaluate a candidate group — returns PcuGroup or None if infeasible
# ---------------------------------------------------------------------------

def evaluate_group(
    databases: list[DbDemand],
    util_ceiling: float,
    pricebook: PriceBook,
    large_only: bool = False,
) -> Optional[PcuGroup]:
    """
    Try all eligible, priced PCU types and return the cheapest *feasible* PcuGroup.
    Types with no published rate for the group's provider/region/tier are skipped; if none
    is priced, all eligible types are sized (fewest PCUs wins) and the result is flagged
    "no pricing available".
    A type is feasible only if the PCUs it needs fit within the group limit
    (1 for vector, PCU_HARD_CAP otherwise).  Infeasible types are used
    (flagged "needs account team") only when no eligible type is feasible; in
    that case the type needing the fewest PCUs is shown.
    Returns None only if no eligible type exists at all.
    """
    assert databases, "evaluate_group called with empty database list"

    # All databases in a candidate group must share the same bucket key
    tenancy = databases[0].tenancy
    is_vector = databases[0].is_vector

    types = eligible_types(tenancy, is_vector, large_only)
    if not types:
        return None

    limit = 1 if is_vector else PCU_HARD_CAP

    provider, geo, tier = databases[0].provider, databases[0].geo_region, databases[0].tier
    rated = [(t, pricebook.pcu_rate(t, provider, geo, tier)) for t in types]
    priced = [(t, r) for t, r in rated if r is not None]
    unpriced = not priced
    storage_rate = pricebook.storage_rate(provider, geo, tier)

    best_feasible: Optional[PcuGroup] = None
    best_infeasible: Optional[PcuGroup] = None
    best_infeasible_n = 0

    for pcu_type, rate in (rated if unpriced else priced):
        n = min_pcus(databases, pcu_type, util_ceiling)

        if n > limit:
            # Cannot be placed on this type; clamped result is only a fallback
            group = PcuGroup(
                pcu_type=pcu_type, pcu_count=limit, databases=databases,
                flag="needs account team", rate_per_pcu=rate, storage_rate=storage_rate,
            )
            if (
                best_infeasible is None
                or n < best_infeasible_n
                or (n == best_infeasible_n and group.comparison_cost < best_infeasible.comparison_cost)
            ):
                best_infeasible = group
                best_infeasible_n = n
            continue

        flag = "no headroom" if (not is_vector and n > PCU_TARGET) else ""
        group = PcuGroup(
            pcu_type=pcu_type, pcu_count=n, databases=databases, flag=flag,
            rate_per_pcu=rate, storage_rate=storage_rate,
        )
        if best_feasible is None or group.comparison_cost < best_feasible.comparison_cost:
            best_feasible = group

    best = best_feasible or best_infeasible
    if unpriced and best is not None and best.flag != "needs account team":
        best.flag = FLAG_NO_PRICING
    return best


# ---------------------------------------------------------------------------
# Partition helpers
# ---------------------------------------------------------------------------

def all_partitions(items: list) -> list[list[list]]:
    """Generate all set partitions of items (Bell number — only feasible for small lists)."""
    if not items:
        return [[]]
    first = items[0]
    rest = items[1:]
    result = []
    for partition in all_partitions(rest):
        # Add first to each existing subset
        for i in range(len(partition)):
            new_partition = [s[:] for s in partition]
            new_partition[i] = [first] + new_partition[i]
            result.append(new_partition)
        # first forms a new singleton subset
        result.append([[first]] + partition)
    return result


def _cost_of_mapping(groups: list[PcuGroup]) -> float:
    return sum(g.comparison_cost for g in groups)


# ---------------------------------------------------------------------------
# Exact optimiser (small buckets)
# ---------------------------------------------------------------------------

def optimise_exact(
    databases: list[DbDemand],
    util_ceiling: float,
    pricebook: PriceBook,
    large_only: bool = False,
    objective: str = "cost",  # "cost" | "fewest_groups"
) -> list[PcuGroup]:
    """
    Enumerate all partitions of databases and return the mapping with the
    lowest total hourly cost (or fewest groups, with cost as tie-breaker).
    """
    best_groups: Optional[list[PcuGroup]] = None
    best_key: Optional[tuple] = None

    for partition in all_partitions(databases):
        groups = []
        feasible = True
        for subset in partition:
            g = evaluate_group(subset, util_ceiling, pricebook, large_only)
            if g is None:
                feasible = False
                break
            groups.append(g)

        if not feasible:
            continue

        # Mappings containing unplaceable groups always lose to placeable ones
        unplaceable = sum(1 for g in groups if g.flag == "needs account team")
        cost = _cost_of_mapping(groups)
        if objective == "fewest_groups":
            key = (unplaceable, len(groups), cost)
        else:
            key = (unplaceable, cost)

        if best_key is None or key < best_key:
            best_groups = groups
            best_key = key

    return best_groups or []


# ---------------------------------------------------------------------------
# Greedy heuristic (large buckets)
# ---------------------------------------------------------------------------

def optimise_greedy(
    databases: list[DbDemand],
    util_ceiling: float,
    pricebook: PriceBook,
    large_only: bool = False,
    objective: str = "cost",
) -> list[PcuGroup]:
    """
    Greedy first-fit descending heuristic.  Sorts databases by descending demand
    (cache_gb + normalised tps) and greedily adds each to the cheapest existing
    group that still fits, or opens a new group.
    """
    # Sort descending by cache demand as a proxy for overall demand
    sorted_dbs = sorted(databases, key=lambda db: db.cache_gb + db.tps / 10_000, reverse=True)

    groups: list[list[DbDemand]] = []

    for db in sorted_dbs:
        placed = False
        best_slot: Optional[int] = None
        best_slot_cost = float("inf")

        for idx, group_dbs in enumerate(groups):
            candidate = group_dbs + [db]
            # Respect PCU target ceiling for grouping
            g = evaluate_group(candidate, util_ceiling, pricebook, large_only)
            if g is None:
                continue
            # For fewest_groups, any slot that doesn't need account team is fine
            if objective == "fewest_groups":
                if g.flag != "needs account team" and (
                    best_slot is None or g.comparison_cost < best_slot_cost
                ):
                    best_slot = idx
                    best_slot_cost = g.comparison_cost
            else:
                # Cost optimisation: pick slot that yields cheapest incremental cost
                current_g = evaluate_group(group_dbs, util_ceiling, pricebook, large_only)
                current_cost = current_g.comparison_cost if current_g else 0.0
                incremental = g.comparison_cost - current_cost
                if g.flag != "needs account team" and incremental < best_slot_cost:
                    best_slot = idx
                    best_slot_cost = incremental

        if best_slot is not None:
            groups[best_slot].append(db)
        else:
            groups.append([db])

    result = []
    for group_dbs in groups:
        g = evaluate_group(group_dbs, util_ceiling, pricebook, large_only)
        if g:
            result.append(g)
    return result


# ---------------------------------------------------------------------------
# Top-level: optimise a single bucket
# ---------------------------------------------------------------------------

def optimise_bucket(
    databases: list[DbDemand],
    pricebook: PriceBook,
    util_ceiling: float = UTIL_CEILING,
    large_only: bool = False,
    objective: str = "cost",
) -> list[PcuGroup]:
    if not databases:
        return []

    if len(databases) <= EXACT_THRESHOLD:
        return optimise_exact(databases, util_ceiling, pricebook, large_only, objective)
    else:
        return optimise_greedy(databases, util_ceiling, pricebook, large_only, objective)


# ---------------------------------------------------------------------------
# Run all four variants
# ---------------------------------------------------------------------------

VARIANTS = [
    ("Lowest cost",     dict(util_ceiling=UTIL_CEILING, large_only=False, objective="cost")),
    ("Fewest groups",   dict(util_ceiling=UTIL_CEILING, large_only=False, objective="fewest_groups")),
    ("Most headroom",   dict(util_ceiling=UTIL_CEILING_HEADROOM, large_only=False, objective="cost")),
    ("Large PCUs only", dict(util_ceiling=UTIL_CEILING, large_only=True, objective="cost")),
]


def run_all_variants(
    buckets: dict[tuple, list[DbDemand]],
    pricebook: PriceBook,
) -> dict[str, list[PcuGroup]]:
    """
    Run all four variants across all buckets.
    Returns a dict: variant_name → flat list of PcuGroup across all buckets.
    """
    results: dict[str, list[PcuGroup]] = {}
    for variant_name, kwargs in VARIANTS:
        all_groups = []
        for bucket_dbs in buckets.values():
            all_groups.extend(optimise_bucket(bucket_dbs, pricebook, **kwargs))
        results[variant_name] = all_groups
    return results
