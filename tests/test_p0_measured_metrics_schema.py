"""Tests for P0-2: evidence_level_summary metric+value schema support.

Validates that _row_has_measured_metrics() supports:
1. Wide schema (recall=0.95, qps=1000)
2. Long schema (metric="recall@10", value="0.95")
"""

import pytest
from r2a.tools.evidence_levels import _row_has_measured_metrics


class TestMeasuredMetricsSchema:
    """Test measured metrics detection for both wide and long schemas."""

    # === Wide Schema Tests ===

    def test_wide_schema_recall_column(self):
        """Wide schema with recall column should be detected."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "recall": "0.95",
        }
        assert _row_has_measured_metrics(row) is True

    def test_wide_schema_qps_column(self):
        """Wide schema with qps column should be detected."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "qps": "1000",
        }
        assert _row_has_measured_metrics(row) is True

    def test_wide_schema_latency_column(self):
        """Wide schema with latency column should be detected."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "latency": "5.2",
        }
        assert _row_has_measured_metrics(row) is True

    # === Long Schema Tests (metric + value) ===

    def test_long_schema_recall_metric(self):
        """Long schema with metric='recall@10' and value='0.95' should be detected."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "metric": "recall@10",
            "value": "0.95",
        }
        assert _row_has_measured_metrics(row) is True

    def test_long_schema_qps_metric(self):
        """Long schema with metric='qps' and value='1000' should be detected."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "metric": "qps",
            "value": "1000",
        }
        assert _row_has_measured_metrics(row) is True

    def test_long_schema_recall_with_ef_search(self):
        """Long schema with recall and ef_search parameters."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "metric": "recall@10",
            "ef_search": "100",
            "value": "0.954",
        }
        assert _row_has_measured_metrics(row) is True

    def test_long_schema_accuracy_metric(self):
        """Long schema with accuracy metric."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "accuracy",
            "value": "0.89",
        }
        assert _row_has_measured_metrics(row) is True

    def test_long_schema_throughput_metric(self):
        """Long schema with throughput metric."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "throughput",
            "value": "500",
        }
        assert _row_has_measured_metrics(row) is True

    def test_long_schema_latency_metric(self):
        """Long schema with latency metric."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "query_time",
            "value": "5.2",
        }
        assert _row_has_measured_metrics(row) is True

    # === Actual Run Format Tests ===

    def test_actual_run_reduced_metrics_format(self):
        """Test actual format from run_20260612_003125_23ef3d02."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "metric": "recall@10",
            "ef_search": "10",
            "value": "0.522",
            "author_value": "0.5386",
            "diff": "0.0166",
            "ground_truth": "precomputed_ground_truth",
            "input_provenance": "author_parameters",
            "evidence_source": "acorn_em_run_v2.log",
        }
        assert _row_has_measured_metrics(row) is True

    def test_actual_run_qps_format(self):
        """Test QPS format from actual run."""
        row = {
            "dataset": "arxiv-for-fanns-medium",
            "method": "ACORN",
            "k": "10",
            "metric": "QPS",
            "ef_search": "10",
            "value": "1071.5",
        }
        assert _row_has_measured_metrics(row) is True

    # === Negative Tests ===

    def test_long_schema_empty_value(self):
        """Empty value should not be detected as measured metric."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "recall@10",
            "value": "",
        }
        assert _row_has_measured_metrics(row) is False

    def test_long_schema_na_value(self):
        """N/A value should not be detected as measured metric."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "recall@10",
            "value": "N/A",
        }
        assert _row_has_measured_metrics(row) is False

    def test_long_schema_non_primary_metric(self):
        """Non-primary metric should not be detected."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "build_time",
            "value": "100",
        }
        # build_time is auxiliary, not primary
        # But we still return True for now as it's a measured value
        # The function checks for primary metrics only in the value presence
        assert _row_has_measured_metrics(row) is False

    def test_row_status_fail(self):
        """Row with FAIL status should not be detected."""
        row = {
            "status": "FAIL",
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "recall@10",
            "value": "0.95",
        }
        assert _row_has_measured_metrics(row) is False

    def test_row_status_needs_input(self):
        """Row with NEEDS_INPUT status should not be detected."""
        row = {
            "status": "NEEDS_INPUT",
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "recall@10",
            "value": "0.95",
        }
        assert _row_has_measured_metrics(row) is False

    def test_no_metric_or_value_columns(self):
        """Row without metric or primary metric columns should not be detected."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "k": "10",
            "some_other_column": "some_value",
        }
        assert _row_has_measured_metrics(row) is False

    def test_metric_column_but_no_value(self):
        """Row with metric column but no value should not be detected."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "metric": "recall@10",
            # no value column
        }
        assert _row_has_measured_metrics(row) is False

    def test_value_column_but_no_metric(self):
        """Row with value column but no primary metric column should not be detected."""
        row = {
            "dataset": "test-dataset",
            "method": "test-method",
            "value": "0.95",
            # no metric column with primary metric name
        }
        assert _row_has_measured_metrics(row) is False

    # === Edge Cases ===

    def test_metric_case_insensitive(self):
        """Metric column should be case insensitive."""
        row1 = {"metric": "RECALL@10", "value": "0.95"}
        row2 = {"metric": "Recall@10", "value": "0.95"}
        row3 = {"METRIC": "recall@10", "VALUE": "0.95"}

        assert _row_has_measured_metrics(row1) is True
        assert _row_has_measured_metrics(row2) is True
        assert _row_has_measured_metrics(row3) is True

    def test_value_numeric_validation(self):
        """Value must be numeric."""
        row1 = {"metric": "recall@10", "value": "0.95"}
        row2 = {"metric": "recall@10", "value": "95%"}
        row3 = {"metric": "recall@10", "value": "not_a_number"}

        assert _row_has_measured_metrics(row1) is True
        assert _row_has_measured_metrics(row2) is False
        assert _row_has_measured_metrics(row3) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
