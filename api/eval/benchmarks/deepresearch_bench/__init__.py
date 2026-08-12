"""DeepResearch Bench dataset and Comet report export adapter.

This package deliberately contains no benchmark runner: generating fifty deep
research reports is an expensive, networked operation and must be orchestrated
explicitly.  The adapter only makes the inputs and outputs reproducible.
"""

from eval.benchmarks.deepresearch_bench.exporter import export_reports
from eval.benchmarks.deepresearch_bench.loader import load_tasks, write_manifest

__all__ = ["export_reports", "load_tasks", "write_manifest"]
