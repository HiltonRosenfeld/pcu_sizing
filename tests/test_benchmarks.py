"""
Unit tests for benchmark throughput limits and lookup functions.
"""
import unittest

from pcu_sizing.benchmarks import (
    BENCHMARK_DATA,
    LATENCY_P99_10MS,
    LATENCY_P99_25MS,
    RATIO_20_80,
    RATIO_60_40,
    RATIO_80_20,
    RATIO_DYNAMIC,
    WORKLOAD_DEFAULT,
    WORKLOAD_IOT,
    WORKLOAD_KV,
    ThroughputConfig,
    get_benchmark_point,
    get_tps_ceiling,
    normalize_pcu_size,
    normalize_provider,
    resolve_ratio_bucket,
)


class TestBenchmarks(unittest.TestCase):
    def test_resolve_ratio_bucket(self):
        self.assertEqual(resolve_ratio_bucket(0.0), RATIO_20_80)
        self.assertEqual(resolve_ratio_bucket(0.20), RATIO_20_80)
        self.assertEqual(resolve_ratio_bucket(0.39), RATIO_20_80)
        self.assertEqual(resolve_ratio_bucket(0.40), RATIO_60_40)
        self.assertEqual(resolve_ratio_bucket(0.60), RATIO_60_40)
        self.assertEqual(resolve_ratio_bucket(0.70), RATIO_60_40)
        self.assertEqual(resolve_ratio_bucket(0.71), RATIO_80_20)
        self.assertEqual(resolve_ratio_bucket(0.80), RATIO_80_20)
        self.assertEqual(resolve_ratio_bucket(1.0), RATIO_80_20)

    def test_normalizers(self):
        self.assertEqual(normalize_provider("AWS"), "AWS")
        self.assertEqual(normalize_provider("aws"), "AWS")
        self.assertEqual(normalize_provider("Google Cloud"), "GCP")
        self.assertEqual(normalize_provider("GCP"), "GCP")
        self.assertEqual(normalize_provider("Azure"), "Azure")
        self.assertEqual(normalize_provider("Microsoft Azure"), "Azure")

        self.assertEqual(normalize_pcu_size("Small"), "Small")
        self.assertEqual(normalize_pcu_size("Medium"), "Medium")
        self.assertEqual(normalize_pcu_size("Large"), "Standard")
        self.assertEqual(normalize_pcu_size("Cache Optimized"), "Standard")

    def test_kv_benchmark_values(self):
        # Test AWS Small 20/80
        pt = get_benchmark_point(WORKLOAD_KV, RATIO_20_80, "AWS", "Small")
        self.assertIsNotNone(pt)
        self.assertEqual(pt.reads_p99_10ms, 900)
        self.assertEqual(pt.reads_p99_25ms, 1500)
        self.assertEqual(pt.writes_p99_10ms, 5200)
        self.assertEqual(pt.writes_p99_25ms, 7600)
        self.assertEqual(pt.total_p99_10ms, 6100)
        self.assertEqual(pt.total_p99_25ms, 9100)

        # Test GCP Standard 60/40
        pt_gcp = get_benchmark_point(WORKLOAD_KV, RATIO_60_40, "GCP", "Large")
        self.assertIsNotNone(pt_gcp)
        self.assertEqual(pt_gcp.reads_p99_10ms, 10500)
        self.assertEqual(pt_gcp.reads_p99_25ms, 14700)
        self.assertEqual(pt_gcp.writes_p99_10ms, 11800)
        self.assertEqual(pt_gcp.writes_p99_25ms, 11800)
        self.assertEqual(pt_gcp.total_tps(LATENCY_P99_25MS), 26500)
        self.assertEqual(pt_gcp.total_tps(LATENCY_P99_10MS), 22300)

    def test_iot_benchmark_values(self):
        # Test Azure Medium 20/80
        pt = get_benchmark_point(WORKLOAD_IOT, RATIO_20_80, "Azure", "Medium")
        self.assertIsNotNone(pt)
        self.assertEqual(pt.reads_p99_10ms, 1000)
        self.assertEqual(pt.reads_p99_25ms, 1000)
        self.assertEqual(pt.writes_p99_10ms, 7600)
        self.assertEqual(pt.writes_p99_25ms, 7600)
        self.assertEqual(pt.total_p99_25ms, 8600)

        # Test AWS Standard 80/20
        pt_aws = get_benchmark_point(WORKLOAD_IOT, RATIO_80_20, "AWS", "Cache Optimized")
        self.assertIsNotNone(pt_aws)
        self.assertEqual(pt_aws.reads_p99_10ms, 7600)
        self.assertEqual(pt_aws.reads_p99_25ms, 10040)
        self.assertEqual(pt_aws.writes_p99_10ms, 5990)
        self.assertEqual(pt_aws.writes_p99_25ms, 5990)
        self.assertEqual(pt_aws.total_p99_25ms, 16030)

    def test_get_tps_ceiling_default_fallback(self):
        config_default = ThroughputConfig(workload=WORKLOAD_DEFAULT)
        self.assertEqual(get_tps_ceiling(None, "AWS", "Small", 0.8, 15000, 6600), 15000)
        self.assertEqual(get_tps_ceiling(None, "AWS", "Small", 0.5, 15000, 6600), 6600)
        self.assertEqual(get_tps_ceiling(config_default, "AWS", "Small", 0.8, 15000, 6600), 15000)
        self.assertEqual(get_tps_ceiling(config_default, "AWS", "Small", 0.5, 15000, 6600), 6600)

    def test_get_tps_ceiling_dynamic_and_fixed(self):
        config_dynamic = ThroughputConfig(workload=WORKLOAD_KV, latency=LATENCY_P99_25MS, ratio_mode=RATIO_DYNAMIC)
        # 20% reads (<40%) -> 20/80 -> 1500 + 7600 = 9100
        self.assertEqual(get_tps_ceiling(config_dynamic, "AWS", "Small", 0.20, 15000, 6600), 9100)
        # 50% reads (40-70%) -> 60/40 -> 2100 + 3800 = 5900
        self.assertEqual(get_tps_ceiling(config_dynamic, "AWS", "Small", 0.50, 15000, 6600), 5900)
        # 80% reads (>70%) -> 80/20 -> 5200 + 2070 = 7270
        self.assertEqual(get_tps_ceiling(config_dynamic, "AWS", "Small", 0.80, 15000, 6600), 7270)

        # Fixed ratio mode: 80/20 even if measured ratio is 20%
        config_fixed = ThroughputConfig(workload=WORKLOAD_KV, latency=LATENCY_P99_25MS, ratio_mode=RATIO_80_20)
        self.assertEqual(get_tps_ceiling(config_fixed, "AWS", "Small", 0.20, 15000, 6600), 7270)

    def test_engine_integration(self):
        from pcu_sizing.catalog import DbDemand, get_pcu_type
        from pcu_sizing.engine import PcuGroup, min_pcus
        from pcu_sizing.benchmarks import ThroughputConfig, WORKLOAD_KV, LATENCY_P99_25MS, RATIO_DYNAMIC

        # AWS database with 2000 reads and 8000 writes (20% reads, 80% writes)
        db = DbDemand(
            db_id="db1",
            region="us-east-1",
            engine_type="cassandra",
            tenancy="shared",
            total_data_gb=50.0,
            hot_data_pct=1.0,
            reads_tps=2000.0,
            avg_read_kb=4.0,
            writes_tps=8000.0,
            avg_write_kb=1.0,
            provider="AWS",
        )
        small_pcu = get_pcu_type("Small", "shared")

        # Under default catalog: write-heavy ceiling is 6,600.
        # Demand = 10,000 TPS. At 80% util ceiling: 0.8 * 6,600 = 5,280 TPS/PCU.
        # Required PCUs = ceil(10,000 / 5,280) = 2 PCUs.
        n_default = min_pcus([db], small_pcu, 0.80, None)
        self.assertEqual(n_default, 2)

        # Under KV benchmark at p99 < 25ms: AWS Small 20/80 total TPS = 9,100.
        # At 80% util ceiling: 0.8 * 9,100 = 7,280 TPS/PCU.
        # Required PCUs = ceil(10,000 / 7,280) = 2 PCUs.
        config_kv = ThroughputConfig(workload=WORKLOAD_KV, latency=LATENCY_P99_25MS, ratio_mode=RATIO_DYNAMIC)
        n_kv = min_pcus([db], small_pcu, 0.80, config_kv)
        self.assertEqual(n_kv, 2)

        # Under KV benchmark at p99 < 10ms: AWS Small 20/80 total TPS = 6,100.
        # At 80% util ceiling: 0.8 * 6,100 = 4,880 TPS/PCU.
        # Required PCUs = ceil(10,000 / 4,880) = 3 PCUs.
        config_kv_10ms = ThroughputConfig(workload=WORKLOAD_KV, latency=LATENCY_P99_10MS, ratio_mode=RATIO_DYNAMIC)
        n_kv_10ms = min_pcus([db], small_pcu, 0.80, config_kv_10ms)
        self.assertEqual(n_kv_10ms, 3)

        # Check PcuGroup tps_ceiling_per_pcu property
        group = PcuGroup(pcu_type=small_pcu, pcu_count=n_kv, databases=[db], throughput_config=config_kv)
        self.assertEqual(group.tps_ceiling_per_pcu, 9100)


if __name__ == "__main__":
    unittest.main()
