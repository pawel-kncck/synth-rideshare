"""Scenario scheduling and raw run logging over explicit policy layers."""

import hashlib
import json
import math
import random
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from behavior_policy import PersonProfile, RiderTraits, DriverTraits, EvolutionTraits
from marketplace_policy import PlatformPolicy
from policy_contracts import RandomValues, finite_number, plain
from policy_runtime import PolicyRuntime
from event_engine import HandlerFailure, HandlerRegistry, Scheduler
from marketplace_engine import MarketplaceEngine, World


def generate_ids(prefix, count, upper, seed):
    """Stable person IDs: a role prefix digit followed by a seeded random number."""
    rng = random.Random(seed)
    return [int(f"{prefix}{id_}") for id_ in rng.sample(range(1, upper), count)]


class Simulation:
    """A ride-hailing market driven by a simulated clock.

    A scenario only schedules when driver shifts and rider trips begin, via
    schedule_driver_session() and schedule_rider_session(). Every later
    event (quotes, orders, offers, acceptance, travel, boarding, drop-off,
    and going offline) is triggered from inside the simulation.
    """

    def __init__(self, driver_count=10, rider_count=100, seed=0, *, world=None,
                 platforms=None, rider_profiles=None, driver_profiles=None):
        self.seed = seed
        self.world = world or World()
        configs = ({p: PlatformPolicy() for p in ('rebu', 'blot', 'flyt')}
                   if platforms is None else dict(platforms))
        if not configs or any(not isinstance(c, PlatformPolicy) for c in configs.values()):
            raise ValueError('platforms must map IDs to compiled PlatformPolicy values')
        for name, count in (('driver_count', driver_count), ('rider_count', rider_count)):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f'{name} must be a nonnegative integer')
        registry = HandlerRegistry()
        self.engine = MarketplaceEngine(self.world, registry)
        for platform_id in configs:
            self.engine.add_platform(platform_id)
        self.drivers = generate_ids(9, driver_count, 1_000_000_000, seed)
        self.riders = generate_ids(8, rider_count, 10_000_000_000, seed)
        profiles = {}
        default = PersonProfile(apps=tuple(configs), preferred_app=next(iter(configs)), awareness=tuple(configs))
        for role, ids, selected in (('rider', self.riders, rider_profiles), ('driver', self.drivers, driver_profiles)):
            if selected is None:
                selected = [default] * len(ids)
            elif isinstance(selected, PersonProfile):
                selected = [selected] * len(ids)
            elif callable(selected):
                selected = [selected(pid, RandomValues(seed, ('profile', role, pid))) for pid in ids]
            if len(selected) != len(ids):
                raise ValueError(f'{role}_profiles must have one profile per person')
            for person_id, profile in zip(ids, selected):
                if not isinstance(profile, PersonProfile):
                    raise ValueError('Population profiles must be PersonProfile values')
                if (set(profile.apps) | set(profile.awareness) | set(profile.registrations)
                        | set(profile.disclosed_to)) - set(configs):
                    raise ValueError('Profile references an unknown platform')
                profiles[PolicyRuntime.key(role, person_id)] = profile
                if role == 'driver':
                    if profile.preferred_app not in profile.registrations:
                        raise ValueError('Driver preferred app requires vehicle registration')
                    self.engine.add_car(person_id, profile.registrations)
                    self.engine.add_driver(person_id, profile.apps, person_id, accounts=profile.accounts)
                else:
                    self.engine.add_rider(person_id, profile.apps, accounts=profile.accounts)
        self.policies = PolicyRuntime(self.engine, registry, seed, configs, profiles)
        self._register_sessions(registry)
        self.scheduler = Scheduler(registry)
        self.engine.bind(self.scheduler)
        self._initialize_runner()

    def _initialize_runner(self):
        self.decisions = self.policies.decisions
        self.scenario_parameters = {}
        self.session_schedule = []
        self.log_path = self.run_directory = None
        self._log_stream = None
        self.engine.add_listener(self._log_notification)

    def _register_sessions(self, registry):
        for kind, handler in (('driver_session.start', self._on_driver_session_start),
                              ('driver_session.end_shift', self._on_shift_end),
                              ('rider_session.start', self._on_rider_session_start)):
            registry.register(kind, handler)

    def snapshot(self):
        """Serializable event-boundary checkpoint, including all private memory and random identities."""
        return {'schema_version': 1, 'seed': self.seed, 'engine': self.engine.snapshot(),
                'scheduler': self.scheduler.snapshot(), 'policies': self.policies.snapshot(),
                'profiles': {key: plain(profile) for key, profile in self.policies.profiles.items()},
                'drivers': self.drivers, 'riders': self.riders, 'scenario_parameters': self.scenario_parameters,
                'session_schedule': self.session_schedule}

    @classmethod
    def restore(cls, snapshot):
        import copy
        snapshot = copy.deepcopy(snapshot)
        if snapshot['schema_version'] != 1:
            raise ValueError('Unsupported simulation checkpoint schema')
        sim = cls.__new__(cls)
        sim.seed = snapshot['seed']
        registry = HandlerRegistry()
        sim.engine = MarketplaceEngine.restore(snapshot['engine'], registry)
        sim.world = sim.engine.world
        sim.drivers, sim.riders = snapshot['drivers'], snapshot['riders']
        configs = {p: PlatformPolicy.compile(overrides=c['parameters'], rules=c['rules'], campaigns=c['campaigns'],
                    version=c['version'], fallback=c['fallback']) for p, c in snapshot['policies']['platforms'].items()}
        profiles = {key: PersonProfile(**(p | {'rider': RiderTraits(**p['rider']), 'driver': DriverTraits(**p['driver']),
                    'evolution': EvolutionTraits(**p['evolution'])})) for key, p in snapshot['profiles'].items()}
        sim.policies = PolicyRuntime(sim.engine, registry, sim.seed, configs, profiles)
        sim.policies.restore_memory(snapshot['policies'])
        sim._register_sessions(registry)
        sim.scheduler = Scheduler.restore(snapshot['scheduler'], registry)
        sim.engine.bind(sim.scheduler)
        sim._initialize_runner()
        sim.scenario_parameters = snapshot['scenario_parameters']
        sim.session_schedule = snapshot['session_schedule']
        return sim

    # ----------------------------------------------------------------------
    # External interface: scheduling sessions and moving the clock
    # ----------------------------------------------------------------------

    def schedule_driver_session(self, at_seconds, driver_id, location, shift_seconds=None):
        """Bring a driver on shift at a simulated time.

        The driver opens the app and waits for offers until the shift length
        elapses (or until the run ends when no shift length is given). A
        shift that ends with accepted orders finishes them first.
        """
        if driver_id not in self.engine.drivers:
            raise ValueError(f"Unknown driver {driver_id!r}")
        location = _point(location, "Location")
        if shift_seconds is not None:
            finite_number(shift_seconds, 'shift_seconds', strictly_positive=True)
        self.scheduler.schedule_at(at_seconds, "driver_session.start", {
            "driver_id": driver_id, "location": location, "shift_seconds": shift_seconds,
        })
        self.session_schedule.append({
            "type": "driver", "at_seconds": at_seconds, "driver_id": driver_id,
            "location": location, "shift_seconds": shift_seconds,
        })

    def schedule_rider_session(self, at_seconds, rider_id, location, destination):
        """Have a rider appear at a simulated time wanting to travel.

        The rider opens the app and gets a quote at once, then weighs price
        and pickup ETA after the order delay. They may leave without ordering.
        """
        if rider_id not in self.engine.riders:
            raise ValueError(f"Unknown rider {rider_id!r}")
        location = _point(location, "Location")
        destination = _point(destination, "Destination")
        self.scheduler.schedule_at(at_seconds, "rider_session.start", {
            "rider_id": rider_id, "location": location, "destination": destination,
        })
        self.session_schedule.append({
            "type": "rider", "at_seconds": at_seconds, "rider_id": rider_id,
            "location": location, "destination": destination,
        })

    @property
    def current_time(self):
        return self.scheduler.now

    def advance_to(self, target_time):
        """Process every event due up to the target time, then set the clock there."""
        self.scheduler.advance_to(target_time)

    def _write_log(self, kind, **data):
        """Write raw observations only; metric calculations live in metrics.py."""
        if self._log_stream is not None:
            record = {"type": kind, "at_seconds": self.current_time, **data}
            self._log_stream.write(json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n")

    def _log_notification(self, notification):
        if self._log_stream is not None:
            self._write_log("notification", notification=plain(notification))

    def run(self, start=6, end=23, time_scale=3600, log_dir="logs"):
        """Advance the simulation and save one JSON Lines simulation.log.

        No summary, interval aggregation, or analysis runs here. After this
        returns, use ``python metrics.py PATH/TO/simulation.log`` separately.
        time_scale is simulated seconds per real second; False skips sleeping.
        Initial/final checkpoints preserve raw histories, pending work, policy
        diagnostics and exact continuation boundaries for offline analysis.
        """
        if time_scale is not False and (
            isinstance(time_scale, bool)
            or not isinstance(time_scale, (int, float))
            or not math.isfinite(time_scale)
            or time_scale <= 0
        ):
            raise ValueError("time_scale must be a finite positive number or False")
        if not all(isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
                   for t in (start, end)):
            raise ValueError("Run hours must be finite numbers")
        end_time = (end - start) * 3600
        if not math.isfinite(end_time):
            raise ValueError("Run duration must be finite")
        if end_time < self.current_time:
            raise ValueError("Run end time is before the current simulation time")

        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.run_directory = Path(mkdtemp(
            dir=directory, prefix=f"simulation-{datetime.now():%Y%m%d-%H%M%S}-"
        )).resolve()
        self.log_path = self.run_directory / "simulation.log"
        source_dir = Path(__file__).resolve().parent
        sources = ("main.py", "marketplace_engine.py", "event_engine.py", "behavior_policy.py",
                   "marketplace_policy.py", "policy_runtime.py", "policy_contracts.py", "demand.py")
        with self.log_path.open("w", encoding="utf-8") as stream:
            self._log_stream = stream
            try:
                self._write_log("run_started", schema_version=1, run_id=self.run_directory.name,
                    created_at=datetime.now(timezone.utc).isoformat(), start_hour=start,
                    target_end_seconds=end_time, time_scale=time_scale,
                    source_sha256={name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                                   for name in sources})
                self._write_log("state", boundary="initial", snapshot=self.snapshot())
                stream.flush()
                started_at = time.perf_counter()
                try:
                    if time_scale is False:
                        self.advance_to(end_time)
                    else:
                        self.advance_to(self.current_time)
                        while self.current_time < end_time:
                            next_time = self.scheduler.next_time()
                            next_time = end_time if next_time is None else min(next_time, end_time)
                            time.sleep((next_time - self.current_time) / time_scale)
                            self.advance_to(next_time)
                except BaseException as error:
                    failure = {"error_type": type(error).__name__, "error": str(error),
                               "traceback": traceback.format_exc()}
                    if isinstance(error, HandlerFailure):
                        failure["failed_event"] = error.record.to_dict()
                        failure["events_before_failure"] = [record.to_dict() for record in error.recent]
                    runtime = time.perf_counter() - started_at
                    self._write_log("state", boundary="final", snapshot=self.snapshot())
                    self._write_log("run_finished", status="failed", runtime_seconds=runtime, **failure)
                    raise
                else:
                    runtime = time.perf_counter() - started_at
                    self._write_log("state", boundary="final", snapshot=self.snapshot())
                    self._write_log("run_finished", status="completed", runtime_seconds=runtime)
            finally:
                self._log_stream = None

        print(f"Simulation finished. Log: {self.log_path}")

    def _schedule(self, delay_seconds, kind, **payload):
        return self.scheduler.schedule_after(delay_seconds, kind, payload)

    def _on_driver_session_start(self, event):
        driver_id = event.payload['driver_id']
        shift = self.engine.start_shift(driver_id, tuple(event.payload['location']))
        if event.payload['shift_seconds'] is not None:
            self._schedule(event.payload['shift_seconds'], 'driver_session.end_shift',
                           driver_id=driver_id, shift_id=shift.id)

    def _on_shift_end(self, event):
        driver = self.engine.drivers[event.payload['driver_id']]
        if driver.shift_id == event.payload['shift_id']:
            self.engine.end_shift(driver.id)

    def _on_rider_session_start(self, event):
        self.engine.begin_intent(event.payload['rider_id'], tuple(event.payload['location']),
                                 tuple(event.payload['destination']))


def _point(value, name):
    """Return a finite (x, y) tuple of kilometres or raise a clear error."""
    try:
        x, y = value
        point = (float(x), float(y))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an (x, y) pair of kilometres") from None
    if not all(math.isfinite(coordinate) for coordinate in point):
        raise ValueError(f"{name} must be finite")
    return point
