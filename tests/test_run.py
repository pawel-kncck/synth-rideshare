import io
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from main import Simulation


class RunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_dir = Path(temporary.name) / "logs"
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def run_simulation(self, sim, end_seconds=30, **kwargs):
        kwargs.setdefault("time_scale", False)
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            sim.run(start=0, end=end_seconds / 3600, log_dir=self.log_dir, **kwargs)

    def test_time_scale_paces_events_and_final_idle_gap(self):
        for scale in (0.5, 1, 60, 3600):
            with self.subTest(time_scale=scale):
                sim = Simulation(driver_count=1, rider_count=0)
                sim.schedule_driver_session(5, sim.drivers[0], (0, 0), shift_seconds=5)
                with patch("main.time.sleep") as sleep:
                    self.run_simulation(sim, time_scale=scale)
                self.assertEqual(
                    [call.args[0] for call in sleep.call_args_list],
                    [5 / scale, 5 / scale, 20 / scale],
                )
                self.assertEqual(sim.current_time, 30)
                self.assertEqual(sim.driver_session_history[0].started_at, 5)
                self.assertEqual(sim.driver_session_history[0].ended_at, 10)

    def test_default_preserves_one_hour_per_runtime_second(self):
        sim = Simulation(driver_count=0, rider_count=0)
        with redirect_stdout(self.stdout), patch("main.time.sleep") as sleep:
            sim.run(start=0, end=1, log_dir=self.log_dir)
        sleep.assert_called_once_with(1)

    def test_false_never_calls_sleep_and_processes_end_boundary(self):
        sim = Simulation(driver_count=1, rider_count=0)
        sim.schedule_driver_session(30, sim.drivers[0], (0, 0))
        with patch("main.time.sleep") as sleep:
            self.run_simulation(sim)
        sleep.assert_not_called()
        self.assertEqual(sim.current_time, 30)
        self.assertIn(sim.drivers[0], sim.active_driver_sessions)

    def test_invalid_time_scales_fail_before_any_events_or_log_creation(self):
        for scale in (0, 0.0, -0.0, -1, float("nan"), float("inf"), -float("inf"), True, None, "60"):
            with self.subTest(time_scale=scale):
                sim = Simulation(driver_count=1, rider_count=0)
                sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
                with patch("main.time.sleep") as sleep:
                    with self.assertRaisesRegex(ValueError, "time_scale"):
                        self.run_simulation(sim, time_scale=scale)
                sleep.assert_not_called()
                self.assertFalse(sim.active_driver_sessions)
                self.assertIsNone(sim.log_path)
                self.assertFalse(self.log_dir.exists())

    def test_events_are_logged_and_console_only_shows_summary(self):
        sim = Simulation(driver_count=1, rider_count=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        root_output = io.StringIO()
        root_handler = logging.StreamHandler(root_output)
        logging.getLogger().addHandler(root_handler)
        self.addCleanup(logging.getLogger().removeHandler, root_handler)
        self.run_simulation(sim)

        log = sim.log_path.read_text(encoding="utf-8")
        self.assertIn("Run started:", log)
        self.assertIn("[     0s] Driver", log)
        self.assertIn("went online", log)
        self.assertIn(self.stdout.getvalue().strip(), log)
        self.assertTrue(self.stdout.getvalue().startswith("Simulation run summary\n"))
        self.assertEqual(self.stdout.getvalue().count("Simulation run summary"), 1)
        self.assertNotIn("went online", self.stdout.getvalue())
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(root_output.getvalue(), "")
        self.assertEqual(sim._logger.handlers, [])

    def test_summary_counts_outcomes_and_remaining_work(self):
        sim = Simulation(
            driver_count=1, rider_count=4, speed_kmh=3600,
            order_delay_seconds=1, accept_delay_seconds=1, boarding_delay_seconds=2,
        )
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0), shift_seconds=10)
        # Both riders get a quote; only the first can secure the one driver.
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (3, 0))
        sim.schedule_rider_session(0, sim.riders[1], (0, 0), (3, 0))
        sim.schedule_rider_session(20, sim.riders[2], (0, 0), (3, 0))
        sim.schedule_driver_session(25, sim.drivers[0], (0, 0))
        sim.schedule_rider_session(28, sim.riders[3], (0, 0), (100, 0))
        self.run_simulation(sim)

        output = self.stdout.getvalue()
        self.assertIn("Driver sessions: 2 started; 1 active at end", output)
        self.assertIn("Rider sessions: 4 started; 1 active at end", output)
        self.assertIn("Orders: 3 created; 1 completed; 1 canceled; 1 active at end", output)
        self.assertIn("Riders leaving without a ride: 2", output)
        self.assertIn("Completed trips: 3.00 km; total fares: 6.50", output)
        self.assertIn("Average order-to-pickup wait (completed trips): 1.00s", output)
        self.assertIn("Log: " + str(sim.log_path), output)

    def test_continuation_writes_new_log_and_counts_only_that_run(self):
        sim = Simulation(
            driver_count=1, rider_count=1, speed_kmh=3600,
            order_delay_seconds=1, accept_delay_seconds=1, boarding_delay_seconds=2,
        )
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        sim.schedule_rider_session(0, sim.riders[0], (0, 0), (3, 0))
        self.run_simulation(sim, end_seconds=3)
        first_path = sim.log_path
        first_log = first_path.read_text(encoding="utf-8")
        self.assertIn("Orders: 1 created; 0 completed; 0 canceled; 1 active at end", first_log)

        self.run_simulation(sim, end_seconds=30)
        second_log = sim.log_path.read_text(encoding="utf-8")
        self.assertNotEqual(first_path, sim.log_path)
        self.assertEqual(first_path.read_text(encoding="utf-8"), first_log)
        self.assertIn("Simulated: 27.00s", second_log)
        self.assertIn("Orders: 0 created; 1 completed; 0 canceled; 0 active at end", second_log)
        self.assertIn("Rider sessions: 0 started; 0 active at end", second_log)
        self.assertNotIn("went online", second_log)

        self.run_simulation(sim, end_seconds=60)
        third_log = sim.log_path.read_text(encoding="utf-8")
        self.assertIn("Orders: 0 created; 0 completed; 0 canceled; 0 active at end", third_log)
        self.assertIn("total fares: 0.00", third_log)
        self.assertIn("Average order-to-pickup wait (completed trips): n/a", third_log)
        self.assertEqual(len(list(self.log_dir.glob("*.log"))), 3)

    def test_failure_and_interrupt_are_logged_without_success_summary(self):
        for error in (RuntimeError("callback failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                sim = Simulation(driver_count=0, rider_count=0)

                def fail():
                    raise error

                sim._schedule_at(5, fail)
                with self.assertRaises(type(error)):
                    self.run_simulation(sim)
                log = sim.log_path.read_text(encoding="utf-8")
                self.assertIn("Run stopped at simulated time 5s", log)
                self.assertIn(type(error).__name__, log)
                self.assertNotIn("Simulation run summary", log)
                self.assertEqual(self.stdout.getvalue(), "")
                self.assertEqual(sim._logger.handlers, [])

    def test_zero_duration_runs_create_distinct_logs(self):
        for _ in range(2):
            sim = Simulation(driver_count=0, rider_count=0)
            with patch("main.time.sleep") as sleep:
                self.run_simulation(sim, end_seconds=0, time_scale=1)
            sleep.assert_not_called()
            self.assertIn("Simulated: 0.00s", sim.log_path.read_text(encoding="utf-8"))
        self.assertEqual(len(list(self.log_dir.glob("*.log"))), 2)

    def test_direct_advance_is_silent_and_does_not_create_a_run_log(self):
        sim = Simulation(driver_count=1, rider_count=0)
        sim.schedule_driver_session(0, sim.drivers[0], (0, 0))
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            sim.advance_to(10)
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertIsNone(sim.log_path)


if __name__ == "__main__":
    unittest.main()
