import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from main import Simulation
from metrics import aggregate_intervals, summarize_intervals


class MetricTests(unittest.TestCase):
    def test_exact_durations_weighted_ratios_and_event_boundaries(self):
        records = [
            {"type": "driver_online", "start_seconds": 0, "end_seconds": 1800},
            {"type": "driver_online", "start_seconds": 0, "end_seconds": 900},
            {"type": "driver_active", "start_seconds": 450, "end_seconds": 1350},
            {"type": "search", "at_seconds": 0, "drivers_available": True},
            {"type": "search", "at_seconds": 899.9, "drivers_available": False},
            {"type": "search", "at_seconds": 900, "drivers_available": True},
            {"type": "search", "at_seconds": 1800, "drivers_available": False},
            {"type": "order_completed", "at_seconds": 900},
            {"type": "order_completed", "at_seconds": 1800},
        ]
        rows = aggregate_intervals(records, 0, 1800)
        self.assertEqual([row["searches"] for row in rows], [2, 2])
        self.assertEqual([row["covered_searches"] for row in rows], [1, 1])
        self.assertEqual([row["online_driver_seconds"] for row in rows], [1800, 900])
        self.assertEqual([row["active_driver_seconds"] for row in rows], [450, 450])
        self.assertEqual([row["utilization_pct"] for row in rows], [25, 50])
        self.assertEqual([row["completed_orders"] for row in rows], [0, 2])
        self.assertEqual([row["cumulative_completed_orders"] for row in rows], [0, 2])
        self.assertEqual(rows[0]["clock_start"], "06:00:00")
        totals = summarize_intervals(rows)
        self.assertAlmostEqual(totals["utilization_pct"], 100 / 3)
        self.assertEqual(totals["completed_orders"], 2)
        for row in rows:
            self.assertEqual(row["online_driver_seconds"], row["active_driver_seconds"] + row["idle_driver_seconds"])

    def test_partial_intervals_clip_carried_sessions_and_orders(self):
        records = [
            {"type": "driver_online", "start_seconds": 0, "end_seconds": 2000},
            {"type": "driver_active", "start_seconds": 800, "end_seconds": 1100},
            {"type": "order_completed", "at_seconds": 500},
        ]
        rows = aggregate_intervals(records, 600, 1000, interval_minutes=5)
        self.assertEqual([row["online_driver_seconds"] for row in rows], [300, 100])
        self.assertEqual([row["active_driver_seconds"] for row in rows], [100, 100])
        self.assertEqual([row["completed_orders"] for row in rows], [0, 0])
        self.assertEqual(rows[-1]["end_seconds"], 1000)

    def test_empty_denominators_are_gaps_and_zero_duration_events_are_counted(self):
        empty = aggregate_intervals([], 0, 1800)
        self.assertTrue(all(row["coverage_pct"] is None and row["utilization_pct"] is None for row in empty))
        point = aggregate_intervals([
            {"type": "search", "at_seconds": 0, "drivers_available": False},
            {"type": "order_completed", "at_seconds": 0},
        ], 0, 0)
        self.assertEqual(len(point), 1)
        self.assertEqual(point[0]["coverage_pct"], 0)
        self.assertIsNone(point[0]["utilization_pct"])
        self.assertEqual(point[0]["completed_orders"], 1)

    def test_coverage_summary_is_weighted_by_search_count(self):
        records = [{"type": "search", "at_seconds": 0, "drivers_available": True}]
        records += [{"type": "search", "at_seconds": 1000, "drivers_available": False}] * 9
        rows = aggregate_intervals(records, 0, 1800)
        self.assertEqual([row["coverage_pct"] for row in rows], [100, 0])
        self.assertEqual(summarize_intervals(rows)["coverage_pct"], 10)

    def test_clock_labels_include_midnight_and_day_offset(self):
        rows = aggregate_intervals([], 0, 7200, interval_minutes=60, start_hour=23)
        self.assertEqual(rows[-1]["clock_start"], "D+1 00:00:00")


class ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external_scripts = []
        self.in_report_data = False
        self.report_data = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.in_report_data = attrs.get("id") == "report-data"
            if "src" in attrs:
                self.external_scripts.append(attrs["src"])

    def handle_data(self, data):
        if self.in_report_data:
            self.report_data += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_report_data = False


class ReportingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def run_simulation(self, sim, end_seconds=600, **kwargs):
        with redirect_stdout(io.StringIO()), patch("main.time.sleep"):
            sim.run(start=0, end=end_seconds / 3600, log_dir=self.directory, **kwargs)
        return json.loads((sim.run_directory / "summary.json").read_text())

    def make_trip(self):
        sim = Simulation(driver_count=1, rider_count=2, speed_kmh=3600,
                         order_delay_seconds=0, accept_delay_seconds=0, boarding_delay_seconds=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (300, 0))
        return sim

    def test_run_saves_portable_report_data_configuration_and_log_together(self):
        sim = self.make_trip()
        summary = self.run_simulation(sim, time_scale=False, interval_minutes=5)
        self.assertEqual({path.name for path in sim.run_directory.iterdir()}, {
            "simulation.log", "config.json", "summary.json", "events.jsonl", "metrics.csv", "report.html",
        })
        configuration = json.loads((sim.run_directory / "config.json").read_text())
        self.assertIs(configuration["run"]["time_scale"], False)
        self.assertEqual(configuration["run"]["status"], "completed")
        self.assertEqual(configuration["simulation"]["seed"], 0)
        self.assertEqual(len(configuration["scheduled_sessions"]), 2)
        self.assertEqual(summary["completed_orders"], 1)
        self.assertEqual(summary["coverage_pct"], 100)
        self.assertEqual(summary["utilization_pct"], 50)
        self.assertIn(str(sim.report_path), sim.log_path.read_text())
        with (sim.run_directory / "metrics.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(int(row["completed_orders"]) for row in rows), 1)
        self.assertEqual(rows[-1]["coverage_pct"], "")

        parser = ScriptParser()
        parser.feed(sim.report_path.read_text())
        self.assertEqual(parser.external_scripts, [])
        payload = json.loads(parser.report_data)
        self.assertEqual(set(payload["series"]), {"5", "15", "30", "60"})
        self.assertEqual(payload["summary"], summary)
        for rows in payload["series"].values():
            totals = summarize_intervals(rows)
            for key, value in summary.items():
                self.assertAlmostEqual(totals[key], value)

    def test_continuations_count_boundary_events_once_and_include_new_searches(self):
        sim = self.make_trip()
        first = self.run_simulation(sim, end_seconds=300, time_scale=False)
        first_report = sim.report_path
        saved = first_report.read_bytes()
        self.assertEqual(first["completed_orders"], 1)
        sim.schedule_rider_session(300, sim.riders[1], (300, 0), (600, 0))
        second = self.run_simulation(sim, end_seconds=600, time_scale=False)
        self.assertEqual(second["searches"], 1)
        self.assertEqual(second["completed_orders"], 1)
        self.assertEqual(second["utilization_pct"], 100)
        self.assertEqual(first_report.read_bytes(), saved)
        third = self.run_simulation(sim, end_seconds=900, time_scale=False)
        self.assertEqual(third["searches"], 0)
        self.assertEqual(third["completed_orders"], 0)
        self.assertEqual(third["utilization_pct"], 0)

    def test_each_search_is_an_independent_observation(self):
        sim = self.make_trip()
        # The original search at 0 sees an idle driver. During the trip, a
        # repeat search sees no eligible driver and must not replace it.
        sim._schedule_at(10, lambda: sim._search(sim.active_rider_sessions[sim.riders[0]]))
        summary = self.run_simulation(sim, time_scale=False)
        self.assertEqual(summary["searches"], 2)
        self.assertEqual(summary["coverage_pct"], 50)
        records = [json.loads(line) for line in (sim.run_directory / "events.jsonl").read_text().splitlines()]
        searches = [record for record in records if record["type"] == "search"]
        self.assertEqual([record["at_seconds"] for record in searches], [0, 10])
        self.assertEqual([record["drivers_available"] for record in searches], [True, False])
        self.assertEqual(searches[0]["rider_id"], searches[1]["rider_id"])

    def test_metrics_are_identical_at_different_playback_speeds(self):
        fast = self.make_trip()
        self.run_simulation(fast, time_scale=False)
        paced = self.make_trip()
        self.run_simulation(paced, time_scale=0.5)
        for name in ("metrics.csv", "events.jsonl", "summary.json"):
            self.assertEqual((fast.run_directory / name).read_bytes(), (paced.run_directory / name).read_bytes())

    def test_empty_run_has_valid_report_with_gaps(self):
        sim = Simulation(driver_count=0, rider_count=0)
        summary = self.run_simulation(sim, end_seconds=0, time_scale=False)
        self.assertIsNone(summary["coverage_pct"])
        self.assertIsNone(summary["utilization_pct"])
        self.assertEqual(summary["completed_orders"], 0)
        self.assertTrue(sim.report_path.is_file())

    def test_failed_run_preserves_log_and_configuration_without_success_report(self):
        sim = Simulation(driver_count=1, rider_count=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        sim.schedule_driver_session(1, sim.drivers[0], (0, 0))
        with self.assertRaises(ValueError):
            self.run_simulation(sim, time_scale=False)
        self.assertIn("already has an active session", sim.log_path.read_text())
        self.assertIsNone(sim.report_path)
        self.assertFalse((sim.run_directory / "report.html").exists())
        self.assertEqual(json.loads((sim.run_directory / "config.json").read_text())["run"]["status"], "failed")

    def test_invalid_interval_fails_before_events_and_output_creation(self):
        for interval in (0, -1, True, None, 10, float("nan"), "15"):
            with self.subTest(interval=interval):
                sim = self.make_trip()
                with self.assertRaisesRegex(ValueError, "interval_minutes"):
                    self.run_simulation(sim, interval_minutes=interval)
                self.assertIsNone(sim.run_directory)
                self.assertEqual(sim.search_history, [])

    def test_parameter_text_cannot_break_out_of_embedded_data(self):
        sim = Simulation(driver_count=0, rider_count=0, seed="</script><script>alert(1)</script>")
        self.run_simulation(sim, time_scale=False)
        html = sim.report_path.read_text()
        self.assertNotIn("</script><script>alert(1)</script>", html)
        parser = ScriptParser()
        parser.feed(html)
        self.assertEqual(json.loads(parser.report_data)["configuration"]["simulation"]["seed"], sim.seed)


if __name__ == "__main__":
    unittest.main()
