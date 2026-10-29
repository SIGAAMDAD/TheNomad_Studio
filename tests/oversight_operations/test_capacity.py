import ast
from pathlib import Path
import unittest

from oversight_operations import simulate_capacity


class CapacityAndBoundaryTests(unittest.TestCase):
    def test_500_workers_800_synthetic_events_backpressure(self):
        result = simulate_capacity(500, 800, 600)
        self.assertEqual(result.logical_workers, 500)
        self.assertEqual(result.synthetic_events, 800)
        self.assertEqual(result.accepted, 600)
        self.assertEqual(result.backpressured, 200)
        self.assertEqual(result.maximum_queue_depth, 600)
        self.assertEqual(result.bottleneck, "QUEUE_ADMISSION_BACKPRESSURE")
        self.assertFalse(result.actual_host_sessions_observed)
        self.assertGreater(result.elapsed_ms, 0)
        self.assertGreaterEqual(result.ingestion_p99_ms, result.ingestion_p50_ms)

    def test_capacity_without_overflow_and_invalid_inputs(self):
        result = simulate_capacity(5, 10, 20)
        self.assertEqual(result.backpressured, 0)
        self.assertEqual(result.bottleneck, "LOCAL_IN_MEMORY_CPU_ONLY")
        with self.assertRaises(ValueError):
            simulate_capacity(500, 800, 0)

    def test_no_game_core_imports_or_external_mutating_apis(self):
        root = Path(__file__).resolve().parents[2] / "oversight_operations"
        banned = {"godot", "fmod", "requests", "subprocess", "socket", "boto3", "github", "sqlite3",
                  "httpx", "urllib", "googleapiclient", "pygit2", "gspread"}
        names = set()
        for source in root.glob("*.py"):
            parsed = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    names.update(i.name.split(".")[0] for i in node.names)
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
        self.assertFalse(names & banned)


if __name__ == "__main__":
    unittest.main()
