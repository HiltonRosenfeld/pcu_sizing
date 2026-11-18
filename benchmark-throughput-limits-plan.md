# Plan: Benchmark-Based Throughput Limits for PCU Sizing

## Top-Level Overview
Add support for recent Astra DB PCU performance benchmarks to the PCU Sizing Tool alongside the existing catalog limits. Users will be able to select the workload type (Catalog Default, Key-Value, or IoT), the latency SLA target (p99 < 25 ms or p99 < 10 ms), and choose between dynamic read/write ratio resolution (<40%, 40–70%, >70%) or predetermined fixed ratios (20/80, 60/40, 80/20). Throughput limits will accurately reflect benchmarked capacity across cloud providers (AWS, GCP, Azure) and PCU sizes (Small, Medium, Large/Standard).

---

## Sub-Tasks

### Sub-Task 1: Benchmark Data Model and Lookup Module
- **Intent**: Extract and structure the benchmark data from `data/Astra DB PCU performance benchmarks.md` into a structured, typed Python module (e.g., `pcu_sizing/benchmarks.py`) with lookup functions.
- **Expected Outcomes**:
  - Structured dataset containing throughput limits (Read ops/s, Write ops/s, and Total ops/s) for both KV and IoT workloads, all 3 clouds (AWS, GCP, Azure), all 3 PCU sizes (Small, Medium, Standard/Large), and both latency tiers (`p99 < 10 ms`, `p99 < 25 ms`).
  - Helper functions to compute/lookup the effective TPS ceiling per PCU given workload type, provider, PCU size, latency target, and calculated or fixed read ratio.
  - Clean mapping of dynamic group read ratio brackets (<40% -> 20/80, 40–70% -> 60/40, >70% -> 80/20).
- **Todo List**:
  - [ ] Create `pcu_sizing/benchmarks.py` with structured constants for Key-Value and IoT benchmarks.
  - [ ] Implement `ThroughputConfig` data class representing the selected workload type, ratio mode, and latency SLA.
  - [ ] Implement `get_benchmark_tps_ceiling(config, provider, pcu_size, group_read_ratio)` with fallback to default catalog limits.
  - [ ] Add unit tests verifying all benchmark values and bracket resolutions.
- **Relevant Context**:
  - `data/Astra DB PCU performance benchmarks.md`
  - `pcu_sizing/catalog.py`
- **Status**: `[x] done`

---

### Sub-Task 2: Sizing Engine Integration
- **Intent**: Update `pcu_sizing/engine.py` to use `ThroughputConfig` in `min_pcus`, `PcuGroup`, and candidate evaluation.
- **Expected Outcomes**:
  - `PcuGroup.tps_ceiling_per_pcu` and `min_pcus()` dynamically resolve TPS capacity based on the active `ThroughputConfig`, group provider, and group read ratio.
  - Sizing optimization and variants (`run_all_variants`, `optimise_bucket`, etc.) properly accept and pass the throughput configuration.
  - Utilization calculations and headroom checks reflect the benchmarked TPS ceilings.
- **Todo List**:
  - [x] Update `PcuGroup` to take `throughput_config: Optional[ThroughputConfig]` and derive `tps_ceiling_per_pcu`.
  - [x] Update `min_pcus()` and `evaluate_group()` to accept `throughput_config` and use it during capacity calculation.
  - [x] Pass `throughput_config` through `optimise_exact()`, `optimise_greedy()`, `optimise_bucket()`, and `run_all_variants()`.
  - [x] Ensure backward compatibility when `throughput_config` is None (defaults to standard catalog).
- **Relevant Context**:
  - `pcu_sizing/engine.py:min_pcus` (lines 187-203)
  - `pcu_sizing/engine.py:PcuGroup` (lines 68-73)
  - `pcu_sizing/engine.py:run_all_variants` (lines 444-458)
- **Status**: `[x] done`

---

### Sub-Task 3: Web UI Controls & Configuration Card
- **Intent**: Add UI controls in `app.py` for selecting workload type, latency SLA, and read/write ratio mode.
- **Expected Outcomes**:
  - A clean, intuitive configuration UI (e.g., in a dedicated "Throughput Model" card or expanding the TPS settings card) allowing selection of:
    - Workload Type: "Default / Catalog" (default), "Key-Value (KV)", "IoT"
    - Latency Target (when benchmark workload selected): "p99 < 25 ms" (default), "p99 < 10 ms"
    - Read/Write Ratio: "Dynamic (from input TPS)" (default), "20% Reads / 80% Writes", "60% Reads / 40% Writes", "80% Reads / 20% Writes"
  - `compute_variants` cache key updated to include the new throughput settings so recalculation is instant and reliable.
  - Sidebar catalog/details updated or annotated to indicate the active benchmark limits.
- **Todo List**:
  - [x] Layout UI inputs for Workload Type, Latency SLA, and Ratio Mode in `app.py`.
  - [x] Construct `ThroughputConfig` instance from user selections.
  - [x] Include throughput configuration in `st.cache_data` caching key and pass to engine.
  - [x] Update group summary cards and detail tables to reflect active TPS ceilings.
- **Relevant Context**:
  - `app.py` (lines 166-210 for cards layout, lines 350-365 for cache key)
- **Status**: `[x] done`

---

### Sub-Task 4: Exporter & Verification
- **Intent**: Ensure exported Excel reports and UI summaries reflect the throughput model settings and test the entire workflow.
- **Expected Outcomes**:
  - Excel exporter (`pcu_sizing/exporter.py`) includes throughput model metadata in the summary sheet.
  - End-to-end tests verify both Excel/CSV import and manual entry modes produce accurate sizing results under all workload configurations.
- **Todo List**:
  - [x] Update `pcu_sizing/exporter.py` to include throughput configuration in the export summary sheet.
  - [x] Write automated tests in `tests/test_benchmarks.py` covering all combinations of workload, SLA, ratio mode, and cloud providers.
  - [x] Validate end-to-end functionality using Streamlit headless testing (`streamlit.testing.v1.AppTest`).
- **Relevant Context**:
  - `pcu_sizing/exporter.py`
  - `tests/`
- **Status**: `[x] done`
