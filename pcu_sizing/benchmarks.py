"""
Astra DB PCU Performance Benchmark definitions and throughput lookup.

Provides benchmarked TPS capacities for Key-Value and IoT workloads across
AWS, Azure, and GCP, for Small, Medium, and Standard (Large) PCU sizes under
both p99 < 25 ms and p99 < 10 ms latency targets.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Workload identifiers
WORKLOAD_DEFAULT = "default"
WORKLOAD_KV = "kv"
WORKLOAD_IOT = "iot"

WORKLOAD_LABELS: dict[str, str] = {
    WORKLOAD_DEFAULT: "Absolute",
    WORKLOAD_KV: "Key-Value (KV)",
    WORKLOAD_IOT: "IoT",
}

# Latency SLA targets
LATENCY_P99_25MS = "p99_25ms"
LATENCY_P99_10MS = "p99_10ms"

LATENCY_LABELS: dict[str, str] = {
    LATENCY_P99_25MS: "p99 < 25 ms",
    LATENCY_P99_10MS: "p99 < 10 ms",
}

# Read/Write ratio modes
RATIO_DYNAMIC = "dynamic"
RATIO_20_80 = "20_80"
RATIO_60_40 = "60_40"
RATIO_80_20 = "80_20"

RATIO_MODE_LABELS: dict[str, str] = {
    RATIO_DYNAMIC: "Dynamic",
    RATIO_20_80: "20% Reads / 80% Writes",
    RATIO_60_40: "60% Reads / 40% Writes",
    RATIO_80_20: "80% Reads / 20% Writes",
}


@dataclass(frozen=True)
class ThroughputConfig:
    """Configuration for throughput limit calculations."""
    workload: str = WORKLOAD_DEFAULT
    latency: str = LATENCY_P99_25MS
    ratio_mode: str = RATIO_DYNAMIC

    @property
    def is_benchmark(self) -> bool:
        return self.workload in (WORKLOAD_KV, WORKLOAD_IOT)


@dataclass(frozen=True)
class BenchmarkPoint:
    """Benchmark measurements for a specific configuration."""
    reads_p99_10ms: int
    reads_p99_25ms: int
    writes_p99_10ms: int
    writes_p99_25ms: int

    @property
    def total_p99_10ms(self) -> int:
        return self.reads_p99_10ms + self.writes_p99_10ms

    @property
    def total_p99_25ms(self) -> int:
        return self.reads_p99_25ms + self.writes_p99_25ms

    def total_tps(self, latency: str) -> int:
        if latency == LATENCY_P99_10MS:
            return self.total_p99_10ms
        return self.total_p99_25ms


# Benchmark dataset: [workload][ratio_bucket][cloud][pcu_size]
# PCU sizes: "Small", "Medium", "Standard" (used for Large General Purpose and Cache Optimized)
# Cloud providers: "AWS", "Azure", "GCP"
# "≥" values from benchmark tests are represented as the tested ceiling integer value.
BENCHMARK_DATA: dict[str, dict[str, dict[str, dict[str, BenchmarkPoint]]]] = {
    WORKLOAD_KV: {
        RATIO_20_80: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=900, reads_p99_25ms=1_500, writes_p99_10ms=5_200, writes_p99_25ms=7_600),
                "Medium": BenchmarkPoint(reads_p99_10ms=2_700, reads_p99_25ms=3_300, writes_p99_10ms=13_200, writes_p99_25ms=18_000),
                "Standard": BenchmarkPoint(reads_p99_10ms=3_500, reads_p99_25ms=5_900, writes_p99_10ms=23_600, writes_p99_25ms=23_600),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_100, reads_p99_25ms=1_500, writes_p99_10ms=8_400, writes_p99_25ms=8_400),
                "Medium": BenchmarkPoint(reads_p99_10ms=1_700, reads_p99_25ms=2_900, writes_p99_10ms=16_400, writes_p99_25ms=18_800),
                "Standard": BenchmarkPoint(reads_p99_10ms=5_900, reads_p99_25ms=5_900, writes_p99_10ms=23_600, writes_p99_25ms=23_600),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_100, reads_p99_25ms=1_500, writes_p99_10ms=6_000, writes_p99_25ms=9_200),
                "Medium": BenchmarkPoint(reads_p99_10ms=3_500, reads_p99_25ms=5_500, writes_p99_10ms=20_400, writes_p99_25ms=23_650),
                "Standard": BenchmarkPoint(reads_p99_10ms=5_500, reads_p99_25ms=5_900, writes_p99_10ms=23_600, writes_p99_25ms=23_600),
            },
        },
        RATIO_60_40: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_100, reads_p99_25ms=2_100, writes_p99_10ms=3_000, writes_p99_25ms=3_800),
                "Medium": BenchmarkPoint(reads_p99_10ms=14_100, reads_p99_25ms=16_600, writes_p99_10ms=7_800, writes_p99_25ms=11_400),
                "Standard": BenchmarkPoint(reads_p99_10ms=17_700, reads_p99_25ms=17_700, writes_p99_10ms=11_800, writes_p99_25ms=11_800),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_500, reads_p99_25ms=4_500, writes_p99_10ms=4_600, writes_p99_25ms=6_120),
                "Medium": BenchmarkPoint(reads_p99_10ms=3_300, reads_p99_25ms=6_900, writes_p99_10ms=11_830, writes_p99_25ms=11_830),
                "Standard": BenchmarkPoint(reads_p99_10ms=14_100, reads_p99_25ms=17_700, writes_p99_10ms=11_820, writes_p99_25ms=11_820),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_100, reads_p99_25ms=3_900, writes_p99_10ms=2_600, writes_p99_25ms=4_675),
                "Medium": BenchmarkPoint(reads_p99_10ms=7_500, reads_p99_25ms=9_900, writes_p99_10ms=9_370, writes_p99_25ms=10_950),
                "Standard": BenchmarkPoint(reads_p99_10ms=10_500, reads_p99_25ms=14_700, writes_p99_10ms=11_800, writes_p99_25ms=11_800),
            },
        },
        RATIO_80_20: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_800, reads_p99_25ms=5_200, writes_p99_10ms=1_500, writes_p99_25ms=2_070),
                "Medium": BenchmarkPoint(reads_p99_10ms=9_200, reads_p99_25ms=11_600, writes_p99_10ms=4_300, writes_p99_25ms=5_125),
                "Standard": BenchmarkPoint(reads_p99_10ms=14_800, reads_p99_25ms=23_600, writes_p99_10ms=5_600, writes_p99_25ms=5_600),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_000, reads_p99_25ms=5_200, writes_p99_10ms=2_500, writes_p99_25ms=2_670),
                "Medium": BenchmarkPoint(reads_p99_10ms=2_800, reads_p99_25ms=9_200, writes_p99_10ms=5_310, writes_p99_25ms=5_310),
                "Standard": BenchmarkPoint(reads_p99_10ms=21_200, reads_p99_25ms=23_600, writes_p99_10ms=5_900, writes_p99_25ms=5_900),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=3_600, reads_p99_25ms=5_200, writes_p99_10ms=1_500, writes_p99_25ms=2_060),
                "Medium": BenchmarkPoint(reads_p99_10ms=10_000, reads_p99_25ms=13_200, writes_p99_10ms=4_410, writes_p99_25ms=4_410),
                "Standard": BenchmarkPoint(reads_p99_10ms=14_000, reads_p99_25ms=17_200, writes_p99_10ms=5_910, writes_p99_25ms=5_910),
            },
        },
    },
    WORKLOAD_IOT: {
        RATIO_20_80: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=305, reads_p99_25ms=1_110, writes_p99_10ms=4_400, writes_p99_25ms=4_400),
                "Medium": BenchmarkPoint(reads_p99_10ms=830, reads_p99_25ms=1_880, writes_p99_10ms=7_600, writes_p99_25ms=11_600),
                "Standard": BenchmarkPoint(reads_p99_10ms=1_700, reads_p99_25ms=3_510, writes_p99_10ms=20_400, writes_p99_25ms=23_655),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=300, reads_p99_25ms=500, writes_p99_10ms=5_190, writes_p99_25ms=5_190),
                "Medium": BenchmarkPoint(reads_p99_10ms=1_000, reads_p99_25ms=1_000, writes_p99_10ms=7_600, writes_p99_25ms=7_600),
                "Standard": BenchmarkPoint(reads_p99_10ms=1_710, reads_p99_25ms=2_655, writes_p99_10ms=15_700, writes_p99_25ms=16_400),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=100, reads_p99_25ms=700, writes_p99_10ms=4_385, writes_p99_25ms=4_385),
                "Medium": BenchmarkPoint(reads_p99_10ms=1_090, reads_p99_25ms=2_280, writes_p99_10ms=11_600, writes_p99_25ms=16_400),
                "Standard": BenchmarkPoint(reads_p99_10ms=1_280, reads_p99_25ms=2_430, writes_p99_10ms=23_550, writes_p99_25ms=23_550),
            },
        },
        RATIO_60_40: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_480, reads_p99_25ms=2_115, writes_p99_10ms=1_420, writes_p99_25ms=2_600),
                "Medium": BenchmarkPoint(reads_p99_10ms=4_500, reads_p99_25ms=7_520, writes_p99_10ms=5_000, writes_p99_25ms=10_030),
                "Standard": BenchmarkPoint(reads_p99_10ms=7_440, reads_p99_25ms=9_975, writes_p99_10ms=11_830, writes_p99_25ms=11_830),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=885, reads_p99_25ms=1_490, writes_p99_10ms=2_990, writes_p99_25ms=4_850),
                "Medium": BenchmarkPoint(reads_p99_10ms=3_300, reads_p99_25ms=5_700, writes_p99_10ms=5_400, writes_p99_25ms=10_600),
                "Standard": BenchmarkPoint(reads_p99_10ms=7_380, reads_p99_25ms=7_380, writes_p99_10ms=11_800, writes_p99_25ms=11_800),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_490, reads_p99_25ms=1_490, writes_p99_10ms=2_200, writes_p99_25ms=2_200),
                "Medium": BenchmarkPoint(reads_p99_10ms=3_890, reads_p99_25ms=6_290, writes_p99_10ms=6_990, writes_p99_25ms=8_480),
                "Standard": BenchmarkPoint(reads_p99_10ms=5_100, reads_p99_25ms=6_970, writes_p99_10ms=11_100, writes_p99_25ms=11_400),
            },
        },
        RATIO_80_20: {
            "AWS": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_800, reads_p99_25ms=3_610, writes_p99_10ms=1_100, writes_p99_25ms=1_480),
                "Medium": BenchmarkPoint(reads_p99_10ms=6_000, reads_p99_25ms=10_000, writes_p99_10ms=3_300, writes_p99_25ms=4_310),
                "Standard": BenchmarkPoint(reads_p99_10ms=7_600, reads_p99_25ms=10_040, writes_p99_10ms=5_990, writes_p99_25ms=5_990),
            },
            "Azure": {
                "Small": BenchmarkPoint(reads_p99_10ms=2_820, reads_p99_25ms=2_260, writes_p99_10ms=1_700, writes_p99_25ms=4_400),
                "Medium": BenchmarkPoint(reads_p99_10ms=6_800, reads_p99_25ms=10_000, writes_p99_10ms=4_600, writes_p99_25ms=4_600),
                "Standard": BenchmarkPoint(reads_p99_10ms=11_800, reads_p99_25ms=14_100, writes_p99_10ms=4_600, writes_p99_25ms=5_100),
            },
            "GCP": {
                "Small": BenchmarkPoint(reads_p99_10ms=1_990, reads_p99_25ms=3_600, writes_p99_10ms=1_160, writes_p99_25ms=1_720),
                "Medium": BenchmarkPoint(reads_p99_10ms=5_190, reads_p99_25ms=9_200, writes_p99_10ms=3_490, writes_p99_25ms=3_490),
                "Standard": BenchmarkPoint(reads_p99_10ms=10_000, reads_p99_25ms=11_600, writes_p99_10ms=3_470, writes_p99_25ms=3_470),
            },
        },
    },
}

# Fix small typo in the AWS Small 80/20 write field definition if needed:
# writes_p99_25ms is 1_480


def resolve_ratio_bucket(group_read_ratio: float) -> str:
    """Map a measured group read ratio to the nearest benchmark ratio bucket.

    - < 40% read ratio  -> 20/80
    - 40% to 70% reads  -> 60/40
    - > 70% reads       -> 80/20
    """
    if group_read_ratio < 0.40:
        return RATIO_20_80
    elif group_read_ratio <= 0.70:
        return RATIO_60_40
    return RATIO_80_20


def normalize_provider(provider: str) -> str:
    """Normalize provider name to AWS, Azure, or GCP."""
    p = provider.strip().upper()
    if "GCP" in p or "GOOGLE" in p:
        return "GCP"
    if "AZURE" in p or "MICROSOFT" in p:
        return "Azure"
    return "AWS"


def normalize_pcu_size(pcu_name: str) -> str:
    """Map PCU type name to benchmark size category: Small, Medium, Standard."""
    name = pcu_name.strip()
    if name == "Small":
        return "Small"
    if name == "Medium":
        return "Medium"
    # Large and Cache Optimized are "Standard" PCU size in benchmarks
    return "Standard"


def get_benchmark_point(
    workload: str,
    ratio_bucket: str,
    provider: str,
    pcu_name: str,
) -> Optional[BenchmarkPoint]:
    """Retrieve raw benchmark measurement point."""
    w_data = BENCHMARK_DATA.get(workload)
    if not w_data:
        return None
    r_data = w_data.get(ratio_bucket)
    if not r_data:
        return None
    c_data = r_data.get(normalize_provider(provider))
    if not c_data:
        return None
    return c_data.get(normalize_pcu_size(pcu_name))


def get_tps_ceiling(
    config: Optional[ThroughputConfig],
    provider: str,
    pcu_name: str,
    group_read_ratio: float,
    tps_read_heavy_catalog: int,
    tps_write_heavy_catalog: int,
) -> int:
    """Determine the TPS ceiling per PCU for a group.

    If config is None or set to WORKLOAD_DEFAULT, uses catalog default (threshold at 75% reads).
    Otherwise, uses the configured benchmark data and options.
    """
    if not config or not config.is_benchmark:
        if group_read_ratio > 0.75:
            return tps_read_heavy_catalog
        return tps_write_heavy_catalog

    # Determine ratio bucket
    if config.ratio_mode == RATIO_DYNAMIC:
        ratio_bucket = resolve_ratio_bucket(group_read_ratio)
    else:
        ratio_bucket = config.ratio_mode

    point = get_benchmark_point(config.workload, ratio_bucket, provider, pcu_name)
    if point:
        return point.total_tps(config.latency)

    # Fallback to catalog if benchmark data missing
    if group_read_ratio > 0.75:
        return tps_read_heavy_catalog
    return tps_write_heavy_catalog
