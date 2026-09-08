"""Scenario scheduling and run/report orchestration over explicit policy layers."""

import logging
import math
import random
import time
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from behavior_policy import PersonProfile, RiderTraits, DriverTraits, EvolutionTraits
from marketplace_policy import PlatformPolicy
from policy_contracts import RandomValues, finite_number, plain
from policy_runtime import PolicyRuntime
from event_engine import HandlerFailure, HandlerRegistry, Scheduler
from marketplace_engine import MarketplaceEngine, World
from metrics import INTERVAL_MINUTES


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
        self.log_path = self.run_directory = self.report_path = None
        self._logger = logging.Logger(__name__, level=logging.INFO)
        self._logger.propagate = False
        self.engine.add_listener(lambda n: self._log(f'{n.audience}:{n.audience_id} {n.kind} {dict(n.data)}'))

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

    def run(self, start=6, end=23, time_scale=3600, log_dir="logs", interval_minutes=15):
        """Play the clock, save a run report and log, and print a run summary.

        time_scale is simulated seconds per runtime second: 1 is real time,
        0.5 is half speed, and 60 plays a simulated minute in one second.
        False skips sleeping entirely. Numeric zero is invalid.
        """
        if time_scale is not False and (
            isinstance(time_scale, bool)
            or not isinstance(time_scale, (int, float))
            or not math.isfinite(time_scale)
            or time_scale <= 0
        ):
            raise ValueError("time_scale must be a finite positive number or False")
        end_time = (end - start) * 3600
        if not math.isfinite(end_time):
            raise ValueError("Run duration must be finite")
        if end_time < self.current_time:
            raise ValueError("Run end time is before the current simulation time")
        if isinstance(interval_minutes, bool) or interval_minutes not in INTERVAL_MINUTES:
            raise ValueError("interval_minutes must be 5, 15, 30, or 60")

        # Load the reporting dependency before processing any simulation events.
        from reporting import collect_records, run_configuration, write_json, write_report

        initial_time = self.current_time
        cursor = self._cursor()
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.run_directory = Path(mkdtemp(
            dir=directory, prefix=f"simulation-{datetime.now():%Y%m%d-%H%M%S}-"
        )).resolve()
        self.log_path = self.run_directory / "simulation.log"
        self.report_path = None
        configuration = run_configuration(self, start, end, time_scale, initial_time, int(interval_minutes))
        write_json(self.run_directory / "config.json", configuration)
        with self.log_path.open("w", encoding="utf-8") as log_file:
            handler = logging.StreamHandler(log_file)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self._logger.addHandler(handler)
            started_at = time.perf_counter()
            try:
                self._log(
                    f"Run started: hours {start:g} to {end:g}, end at {end_time:g}s, "
                    f"time_scale={time_scale}, seed={self.seed}, "
                    f"drivers={len(self.drivers)}, riders={len(self.riders)}"
                )
                if time_scale is False:
                    self.advance_to(end_time)
                else:
                    self.advance_to(self.current_time)
                    while self.current_time < end_time:
                        next_time = self.scheduler.next_time()
                        next_time = end_time if next_time is None else min(next_time, end_time)
                        time.sleep((next_time - self.current_time) / time_scale)
                        self.advance_to(next_time)

                runtime = time.perf_counter() - started_at
                configuration["run"].update(status="completed", runtime_seconds=runtime)
                records = collect_records(self, cursor, initial_time)
                self.report_path = write_report(self.run_directory, configuration, records)
                summary = self._run_summary(cursor, initial_time, runtime, time_scale)
                self._logger.info("%s", summary)
            except BaseException as error:
                self._logger.exception("Run stopped at simulated time %gs", self.current_time)
                configuration["run"].update(status="failed", stopped_at_seconds=self.current_time)
                if isinstance(error, HandlerFailure):
                    # Enough to find the event again in a rerun of the same scenario.
                    configuration["run"]["failed_event"] = error.record.to_dict()
                    configuration["run"]["events_before_failure"] = [
                        record.to_dict() for record in error.recent
                    ]
                write_json(self.run_directory / "config.json", configuration)
                raise
            finally:
                self._logger.removeHandler(handler)
                handler.close()

        print(summary)

    # ----------------------------------------------------------------------
    # Run accounting. Engine records have sequential ids, so a cursor of
    # the counts at run start selects what this run created.
    # ----------------------------------------------------------------------

    def _cursor(self):
        engine = self.engine
        return {
            "shifts": len(engine.shifts), "intents": len(engine.intents), "quotes": len(engine.quotes),
            "orders": len(engine.orders), "offers": len(engine.offers),
            "settlements": len(engine.settlements), "decisions": len(self.decisions),
            "events": self.scheduler.processed_count, "observations": len(self.policies.observations),
        }

    def _new_records(self, table, cursor_count):
        return [record for record in table.values() if record.id > cursor_count]

    def _driver_hours(self, initial_time):
        """Physical online and service time within this run's interval."""
        now = self.current_time

        def duration(started_at, ended_at):
            end = now if ended_at is None else min(ended_at, now)
            return max(0, end - max(started_at, initial_time))

        online = math.fsum(duration(shift.started_at, shift.ended_at)
                           for shift in self.engine.shifts.values()) / 3600
        # Service is pickup travel, boarding, and transport actually performed;
        # a queued commitment adds nothing until its own pickup travel starts.
        in_service = math.fsum(duration(service.started_at, service.ended_at)
                               for service in self.engine.services.values()) / 3600
        return online, in_service, online - in_service

    def _run_summary(self, cursor, initial_time, runtime, time_scale):
        engine = self.engine
        intents = self._new_records(engine.intents, cursor["intents"])
        quotes = self._new_records(engine.quotes, cursor["quotes"])
        orders = self._new_records(engine.orders, cursor["orders"])
        offers = self._new_records(engine.offers, cursor["offers"])
        settlements = [s for s in self._new_records(engine.settlements, cursor["settlements"])
                       if s.reason == "completed_ride"]
        completed = [order for order in orders if order.state == "completed"]
        canceled = sum(order.state == "canceled" for order in orders)
        unserved = sum(intent.outcome == "abandoned" for intent in intents)
        undecided = sum(intent.live and intent.converted_at is None for intent in intents)
        converted = sum(intent.converted_at is not None for intent in intents)
        offer_states = {state: sum(offer.state == state for offer in offers)
                        for state in ("accepted", "rejected", "expired", "canceled", "acceptance_failed", "pending")}
        distance = sum(math.dist(leg.origin, leg.destination) for order in completed
                       for leg in engine.services[order.service_id].legs if leg.kind == 'transport')
        payments = sum(s.rider_payment_minor for s in settlements) / self.world.minor_units_per_major
        payouts = sum(s.driver_payout_minor for s in settlements) / self.world.minor_units_per_major
        contribution = sum(s.platform_contribution_minor for s in settlements) / self.world.minor_units_per_major
        pickup_waits = [order.timeline["arrived"] - order.timeline["created"] for order in completed]
        pickup_wait = f"{sum(pickup_waits) / len(pickup_waits):.2f}s" if pickup_waits else "n/a"
        simulated = self.current_time - initial_time
        playback = "as fast as possible (no sleep)" if time_scale is False else f"{time_scale:g}x"
        online_hours, service_hours, idle_hours = self._driver_hours(initial_time)
        utilization = f"{service_hours / online_hours:.2%}" if online_hours else "n/a"
        covered = sum(quote.drivers_available for quote in quotes)
        coverage = f"{covered / len(quotes):.2%}" if quotes else "n/a"
        conversion = f"{converted / len(intents):.2%}" if intents else "n/a"
        acceptance = f"{offer_states['accepted'] / len(offers):.2%}" if offers else "n/a"
        on_shift = sum(driver.shift_id is not None for driver in engine.drivers.values())
        live_intents = sum(intent.live for intent in engine.intents.values())
        active_orders = sum(not order.terminal for order in engine.orders.values())
        queued = sum(max(0, len(driver.commitments) - 1) for driver in engine.drivers.values())
        return "\n".join([
            "Simulation run summary",
            f"  Simulated: {simulated:.2f}s ({simulated / 3600:.2f}h); "
            f"runtime: {runtime:.3f}s; speed: {playback}",
            f"  Driver shifts: {len(engine.shifts) - cursor['shifts']} started; {on_shift} on shift at end",
            f"  Driver hours: {online_hours:.2f} online; {service_hours:.2f} in service; "
            f"{idle_hours:.2f} idle; utilization: {utilization}",
            f"  Rider trips: {len(intents)} started; {live_intents} live at end",
            f"  Search coverage: {coverage} ({covered} of {len(quotes)} quotes)",
            f"  Trip conversion: {conversion} ({converted} of {len(intents)} trips; {undecided} undecided)",
            f"  Offer acceptance: {acceptance} ({offer_states['accepted']} of {len(offers)} offers; "
            f"{offer_states['rejected']} rejected; {offer_states['expired']} expired; "
            f"{offer_states['canceled']} canceled; {offer_states['acceptance_failed']} failed; "
            f"{offer_states['pending']} pending)",
            f"  Orders: {len(orders)} created; {len(completed)} completed; {canceled} canceled; "
            f"{active_orders} active at end ({queued} queued behind another ride)",
            f"  Riders leaving without a ride: {unserved}",
            f"  Completed trips: {distance:.2f} km; rider payments: {payments:.2f}; "
            f"driver payouts: {payouts:.2f}; platform contribution: {contribution:.2f}",
            f"  Average order-to-pickup wait (completed trips): {pickup_wait}",
            f"  Events: {self.scheduler.processed_count - cursor['events']} processed; "
            f"{self.scheduler.pending_count} pending at end",
            f"  Log: {self.log_path}",
            f"  Report: {self.report_path}",
        ])

    def _schedule(self, delay_seconds, kind, **payload):
        return self.scheduler.schedule_after(delay_seconds, kind, payload)

    def _log(self, message):
        self._logger.info("[%6.0fs] %s", self.current_time, message)

    def _money(self, minor):
        return minor / self.world.minor_units_per_major

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
