# Project Overview

## What This Project Is

An internal IBM tool for capacity planning on **Astra DB Serverless**. Its purpose is to **map a set of existing databases onto PCUs** (Provisioned Capacity Units) in the **most cost-effective collection of PCU groups**, when migrating known workloads from **Astra DB on-demand** pricing to **Astra DB PCU Groups**.

Each PCU has a pre-provisioned, fixed amount of capacity (cache and TPS) that depends on its type. The tool decides:

- which databases share a PCU group (multiple small, low-use databases can share a single small PCU),
- which PCU type each group uses,
- how many PCUs each group needs (a single massive or very active database may need multiple General Purpose or Cache Optimized PCUs),

while respecting the limits of each PCU type, and minimising total reserved cost.

> **PCU** = Provisioned Capacity Unit — pre-provisioned capacity for Astra DB Serverless databases to support complex, latency-sensitive workloads and accommodate periodic traffic spikes. See [DataStax docs](https://docs.datastax.com/en/astra-db-serverless/administration/provisioned-capacity-units.html).

> **History:** the project originally produced a "PCU Group configuration recommendation" per workload. It was redefined as a database→PCU mapping / cost-optimisation problem (Step 4 is now an optimisation, not first-fit packing).

## Main Goals and Objectives

- Encode the existing manual sizing formula into a reliable, automated tool
- Allow architects to import known on-demand metrics and receive a **database → PCU group mapping** with total cost
- Make the tool easy to use — initially for personal use, with a clear path to expand to the wider team
- Deliver as a **web app** with the ability to import on-demand workload metrics

## Key Stakeholders and Users

| Stakeholder | Role |
|-------------|------|
| Hilton (owner) | Primary user and developer during initial phase |
| IBM team (future) | Wider team access once the tool is proven effective |

The tool is designed with **ease of use** as a first-class concern to support future team adoption.

## Key Concepts

### PCU Catalog (Reserved / RCU pricing only)

Pricing is per PCU per hour, **reserved (RCU) only** — flexible (HCU) is out of scope. HCU rates are kept for reference.

| PCU type | Tenancy | RCU $/hr | HCU $/hr | Specs | Cache per PCU used for sizing | TPS read-heavy / write-heavy |
|----------|---------|----------|----------|-------|-------------------------------|------------------------------|
| Small | Shared only | 2.71 | 4.52 | 4 vCPU, 16 GB RAM | 125 GB | 15,000 / 6,600 |
| Medium | Shared only | 5.43 | 9.05 | 8 vCPU, 32 GB RAM | 250 GB | 30,000 / 13,200 |
| General Purpose (Large) | Shared | 9.20 | 13.79 | 16 vCPU, 64 GB RAM | 500 GB | 75,000 / 33,000 |
| General Purpose (Large) | Dedicated | 11.04 | 16.55 | 16 vCPU, 64 GB RAM | 500 GB | 75,000 / 33,000 |
| Cache Optimized (Large) | Shared | 9.20 + 4.83 = **14.03** | — | 16 vCPU, 128 GB RAM | **2,200 GB usable** (spec 2.5 TB; 2.2 TB from experience) | 75,000 / 33,000 |
| Cache Optimized (Large) | Dedicated | 11.04 + 8.70 = **19.74** | — | 16 vCPU, 128 GB RAM | **2,200 GB usable** | 75,000 / 33,000 |

- Cache Optimized = Large PCU price + cache-optimisation add-on (4.83 shared, 8.70 dedicated).
- Small and Medium have **no Cache Optimized option and no Dedicated tenancy**.
- "Read-heavy" applies when the **group's** read ratio > 75%; otherwise the write-heavy (lower) TPS ceiling applies.
- Cost observations: 2× Small (5.42) ≈ 1× Medium (5.43) for the same capacity; Medium mainly helps stay under the PCU-count cap. Large is cheaper per TPS and per GB of cache than Small/Medium. Cache Optimized is roughly a third of General Purpose cost per GB of cache.

### Group Configuration Parameters

| Parameter | Description |
|-----------|-------------|
| **PCU Type** | One of the types above. **A group cannot mix PCU types.** |
| **Reserved capacity** | Number of PCUs billed at the RCU rate (long-term committed, 1-year; can only increase during a subscription period) |
| **Minimum / Maximum capacity** | Autoscaling bounds (see Open Questions for how they are set in the output) |
| **Tenant type** | `Shared` (default, multi-tenant) or `Dedicated` (single-tenant, for compliance). Dedicated forces Dedicated Large types |

### Billing Units

| Unit | Description |
|------|-------------|
| **RCU** (Reserved Capacity Unit) | Long-term committed rate; requires 1-year commitment; lowest cost for continuous workloads. **The only unit this tool costs.** |
| **HCU** (Hourly Capacity Unit) | Flexible rate; no commitment. Out of scope. |

### Inputs (from on-demand workload)

| Input | Example | Notes |
|-------|---------|-------|
| Total Data (GB) — unreplicated | 500 | After compression; if RF=3, divide DC size by 3 |
| % of data needing fast read access | 100% | What % of data must be hot in the fast-read layer |
| Writes/sec (P90) (globally) | 100 | Writes replicate to all regions |
| Avg Write Size (KB) | 1 | |
| Reads/sec (P90) (average across all regions) | 50 | Reads occur in a single region |
| Avg Read Size (KB) | 4 | |

- Database type: Serverless (vector) vs Serverless (non-vector) — affects PCU limits and type
- Number of regions deployed (multi-region requires one PCU group per region)
- Number of tables/collections (a single PCU group should not exceed ~5,000 tables across all databases)
- Tenancy requirement per database: `Shared` (default) or `Dedicated` (flag)

### Input Data Mapping (Excel export of on-demand metrics)

The on-demand metrics arrive as an Excel sheet. Calculator inputs map to its columns as follows:

| Calculator input | Source | Excel column / default |
|------------------|--------|------------------------|
| Total Data (GB) | Excel | `sstable KiB` ÷ (1024 × 1024) |
| % of data needing fast read access | Default | 100% |
| Writes/sec | Excel | `write rps P90 / 7d` |
| Avg Write Size (KB) | Default | 1 |
| Reads/sec | Excel | `read rps P90 / 7d` |
| Avg Read Size (KB) | Default | 4 |
| Region (for grouping) | Excel | `region` column |
| Database type (for grouping) | Excel | `engine type` column; value `cassandra-vector` = Serverless (vector), anything else = Serverless (non-vector) |
| Tenancy | Default | Shared (per-database Dedicated flag) |

Notes:
- Reads/writes per second are the **P90 over 7 days** (not a true mean). **P90 is used directly for the TPS calculation** — there is no peak multiplier and P100 values are not used (changed from the earlier `avg × multiplier` / P100 approach).
- **Each Excel row is one database.**
- `sstable KiB` is already **unreplicated and compressed** — no division by replication factor needed.
- `read_ratio` (75% threshold) uses the same P90 reads and writes.

## Mapping Model

> Replaces the earlier per-database "reserved_rcus" rounding + first-fit packing. Per-database demand is now kept **fractional** and summed within a group; PCU counts are rounded up per **group**, not per database.

### Step 1 — Per-database demand

```
cache_gb   = total_data_gb × hot_data_pct
tps        = reads_p90  × ROUNDUP(read_size_kb / 4, 0)
           + writes_p90 × ROUNDUP(write_size_kb / 1, 0)
```

Reads are normalised to 4 KB units, writes to 1 KB units. `reads_p90` / `writes_p90` are `read rps P90 / 7d` and `write rps P90 / 7d`.

> Cache-Optimized usable cache is **2,200 GB** (experience), not the 2,500 GB spec. General Purpose 500 GB matches spec.

### Step 2 — Group capacity test

For a candidate group of databases `D` with PCU type `t` and PCU count `n`:

```
group_read_ratio = Σ reads_p90 / Σ (reads_p90 + writes_p90)
tps_per_pcu      = read_heavy_tps[t] if group_read_ratio > 0.75 else write_heavy_tps[t]

fits  ⇔  Σ cache_gb  ≤ 0.8 × n × cache_per_pcu[t]
     AND Σ tps       ≤ 0.8 × n × tps_per_pcu
```

- **80% utilisation ceiling** applies to both cache and TPS.
- The TPS ceiling is decided by the **group's** combined read ratio, not each database's.
- Minimum `n` for a group/type = `MAX(ROUNDUP(Σcache / (0.8×cache_per_pcu), 0), ROUNDUP(Σtps / (0.8×tps_per_pcu), 0), 1)`.

### Step 3 — Bucketing (hard constraints)

Databases may only share a group if they match on **all** of:

1. **Deployment type**: Serverless (vector) vs non-vector (`engine type`) — never mixed.
2. **Region** (`region` column) — PCU groups are region-specific.
3. **Tenancy**: Shared (default) or Dedicated.

Within a group, **one PCU type only**.

### Step 4 — Cost-minimising grouping

Within each bucket, choose the partition of databases into groups, and for each group the PCU type `t` and count `n`, that **minimises total hourly RCU cost** subject to:

| Rule | Non-vector | Vector |
|------|-----------|--------|
| Allowed PCU types | Small, Medium, General Purpose, Cache Optimized (Small/Medium Shared only) | **Cache Optimized only** |
| PCUs per group | Target **≤ 5** (headroom against the 10-PCU hard cap) | **Exactly 1** (min = max = reserved = 1) |
| Dedicated tenancy | General Purpose / Cache Optimized Dedicated only | Cache Optimized Dedicated only |
| Table limit | ~5,000 per group — **not enforced** (counts unavailable) | same |

Overflow handling:
- A single database that needs **6–10 PCUs** gets its own group, flagged **"no headroom"**.
- A database (or group) that would need **> 10 PCUs** is **flagged for the DataStax/IBM account team**; it cannot be placed.
- A vector database needing > 1 Cache Optimized PCU (at 80%) is flagged for the account team.

Type selection (`evaluate_group`): a PCU type whose required count exceeds the group limit (10 non-vector, 1 vector) is **excluded from the cost comparison** — clamping the count and costing it would make an unusable Small/Medium look cheapest. It is shown (flagged "needs account team", type needing the fewest PCUs) only when no eligible type fits. In the exact optimiser, a mapping containing any such unplaceable group always loses to a fully placeable mapping, regardless of cost.

Rationale for the 5-PCU target: balances headroom within the 10-PCU group cap against over-supplying capacity — e.g. a Small PCU may already be more than the databases on it need, so extra headroom is cost with no value.

### Output (per bucket)

For each group: PCU type, tenancy, PCU count (reserved), list of databases, summed cache/TPS vs. capacity (utilisation %), hourly cost; plus flags and overall total cost.

**Alternative mappings (named variants):** the tool shows the following named variants side by side. Each shows total cost, difference vs. Lowest cost, and group/PCU/utilisation summary. All variants obey the same hard constraints; they differ only in the objective or an extra restriction. Duplicate results (same mapping) are collapsed into one.

| Variant | Objective / restriction |
|---------|------------------------|
| **Lowest cost** (default) | Minimise total hourly RCU cost |
| **Fewest groups** | Minimise number of PCU groups (cost as tie-breaker) — simpler to operate |
| **Most headroom** | Same as Lowest cost but with a **60% utilisation ceiling** (cache and TPS) instead of 80% |
| **Large PCUs only** | Exclude Small and Medium (General Purpose / Cache Optimized only) — fewer, bigger PCUs |

The variant set is confirmed (Lowest cost, Fewest groups, Most headroom, Large PCUs only) and can be extended later.

**Cost presentation:** hourly cost × **730 hours/month** (24 × 365 / 12) for monthly cost; annual = monthly × 12.

## Planning Rules and Constraints

These rules come from [DataStax PCU planning guidance](https://docs.datastax.com/en/astra-db-serverless/administration/plan-pcu.html) and must be encoded in the engine.

### Structural Constraints

| Constraint | Rule |
|------------|------|
| PCU groups are region-specific | One PCU group per region |
| Deployment type lock | A PCU group contains Serverless (vector) OR Serverless (non-vector) databases, not both |
| Table limit | ~5,000 tables max across all databases in a single PCU group (not enforced yet) |
| Non-vector max capacity | 10 PCUs per group hard cap (project target ≤ 5) |
| Vector capacity | Exactly 1 PCU per group; autoscaling disabled |
| One PCU type per group | Types cannot be mixed |
| Small / Medium | No Cache Optimized, no Dedicated tenancy |

### Sizing Starting Points

- **Committed capacity (production)**: Start with reserved=1, min=1, max=2. Monitor and increase min/max before increasing reserved.
- **RCU vs HCU tipping point**: If a PCU group will be active >~450 hours/month for multiple months, committed capacity (RCU) is more cost-effective than flexible (HCU). This tool assumes reserved.

### PCU Type Selection (DataStax guidance)

| Scenario | Recommended PCU Type |
|----------|----------------------|
| Serverless (vector) databases | **Cache Optimized** (strongly recommended; enforced as the only option here) |
| Large working dataset (>500 GB) | **Cache Optimized** |
| Standard workload | **General Purpose** |
| Small/dev workload | **Small** or **Medium** (no dedicated tenancy) |

### Capacity Allocation Strategy

- **Minimum** = continuously-provisioned baseline; set above average traffic for a buffer before autoscaling
- **Maximum** = emergency ceiling; only billed at HCU rate while in use
- **Reserved** = long-term RCU commitment; increase only when min capacity has been stable and proven

### Cyclical/Seasonal Spikes

- Temporarily raise **minimum** to pre-provision extra capacity ahead of spikes (billed at HCU until reverted)
- Temporarily raise **maximum** to widen autoscaling range
- After the spike, revert min/max; if the higher capacity proves permanent, raise **reserved** to save on HCU costs

## Technical Approach

- **Stack**: **Python** web app built with **Streamlit**. Not Node/React.
- **Input**: Excel upload of on-demand metrics, **or** optional on-screen entry for small data sets (radio at top of the app switches mode; no sample-data option).
  - On-screen entry is an editable table (`st.data_editor`, dynamic rows) inside a form with a **Calculate** button. Columns: Database, Region, Engine (Vector/Non-vector), Tenancy (Shared/Dedicated), Data size (GB), Write rps P90, Read rps P90.
  - Parsed by `parse_manual()` in `pcu_sizing/importer.py`, with the same defaults as the Excel import (100% hot data, 4 KB reads, 1 KB writes). Data size is entered directly in **GB** (Excel uses `sstable KiB`). Blank rows are ignored; rows missing data size are skipped with a warning.
- **Output**: results shown **on screen** (Streamlit tables, one tab per variant) and also available as a **downloadable Excel file** (one sheet per variant plus a summary sheet).
- **Streamlit version pin**: `requirements.txt` pins `streamlit==1.35` (exact). Consequences for `app.py`:
  - Use `use_container_width=True` on `st.dataframe` / `st.data_editor`. `width="stretch"` is only supported in much newer Streamlit and crashes 1.35 with `TypeError: 'str' object cannot be interpreted as an integer`.
  - Streamlit 1.35 requires `numpy<2`, but unpinned `pyarrow` resolves to a release needing NumPy 2 (breaks `st.dataframe`), so `requirements.txt` also pins `pyarrow<21`.
  - If the Streamlit pin is changed again, re-check `app.py` for API compatibility. Headless check: run it with `streamlit.testing.v1.AppTest` (needs the project on `PYTHONPATH`), in both input modes, with `manual_df` preset in `session_state` to exercise the results rendering.
  - `test_app.py` is a Streamlit diagnostic page for Streamlit Cloud deployment debugging, not a pytest suite.
- **Optimiser**: **exact search per bucket** (one row per database keeps buckets small enough), with a **heuristic fallback** when a bucket has too many databases for exact search (threshold TBD during implementation).
- **Phase 1**: Web app with metric import and database→PCU mapping output
- **Phase 2**: Expand access to the wider IBM team with polished UX

## Current State

- ✅ Sizing formula established (per-database demand)
- ✅ Mapping model defined (catalog, rules, 80% ceiling, 5-PCU group target)
- ✅ Tech decisions made (Python + Streamlit, Excel in/out, exact optimiser with heuristic fallback)
- 🔲 Web app scaffolding
- 🔲 Metric import functionality
- 🔲 Optimisation / mapping engine
- 🔲 Output / reporting

## Open Questions

<!-- Add decisions or unknowns here as they emerge -->
- Table/collection counts are unavailable for now; the 5,000-table limit is not enforced.
- How should min/max capacity be set in the output for each group (e.g. min = reserved, max = reserved + headroom)?
- Bucket size above which the exact optimiser falls back to the heuristic.
