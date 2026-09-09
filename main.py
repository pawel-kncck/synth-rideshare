"""Single-scenario execution and raw run logging.

A `Simulation` is instantiated from prepared scenario inputs (`scenario.Plan.prepare`).
It owns fresh engine, scheduler, policy runtime and randomness for one run. All
scheduling of exogenous shifts, trips, checkpoints and interventions comes from
the scenario; nothing here invents demand or supply.
"""

import copy
import hashlib
import json
import math
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from event_engine import HandlerFailure, HandlerRegistry, Scheduler
from marketplace_engine import MarketplaceEngine
from marketplace_policy import PlatformPolicy
from policy_contracts import plain
from policy_runtime import PolicyRuntime
from scenario import HOUR, SOURCE_FILES, Inputs, compile_scenario, load_profile

SNAPSHOT_SCHEMA_VERSION = 2


def run_scenario(scenario, *, seed=0, inputs=None, time_scale=False, log_dir='logs', until_seconds=None):
    """Compile, prepare inputs for one seed (or reuse shared ones), run once and return the simulation."""
    plan = compile_scenario(scenario)
    inputs = plan.prepare(seed) if inputs is None else inputs.reuse_for(plan)
    sim = Simulation(inputs)
    sim.run(until_seconds=until_seconds, time_scale=time_scale, log_dir=log_dir)
    return sim


class Simulation:
    """A ride-hailing market driven by a simulated clock, built from prepared inputs."""

    def __init__(self, inputs):
        if not isinstance(inputs, Inputs):
            raise ValueError('Simulation needs prepared scenario inputs (Plan.prepare(seed))')
        plan = inputs.plan
        self.seed = inputs.seed
        self.world = plan.world
        self.scenario = plan.manifest()
        self.inputs = inputs.to_dict()
        registry = HandlerRegistry()
        self.engine = MarketplaceEngine(self.world, registry)
        for platform_id, platform in plan.platforms.items():
            self.engine.add_platform(platform_id, launched=platform.launched)
        profiles = {}
        for person in inputs.people:
            profile = load_profile(person['profile'])
            profiles[PolicyRuntime.key(person['role'], person['id'])] = profile
            if person['role'] == 'driver':
                self.engine.add_car(person['car']['id'], person['car']['registrations'])
                self.engine.add_driver(person['id'], profile.apps, person['car']['id'], accounts=profile.accounts)
            else:
                self.engine.add_rider(person['id'], profile.apps, accounts=profile.accounts)
        self.policies = PolicyRuntime(self.engine, registry, self.seed, {p: item.policy for p, item in plan.platforms.items()},
                                      profiles, implementations=plan.implementations)
        for person in inputs.people:
            self.policies.state(person['role'], person['id'])['scores'] = dict(person['initial_scores'])
        self._register_sessions(registry)
        self.scheduler = Scheduler(registry)
        self.engine.bind(self.scheduler)
        for session in inputs.sessions:
            payload = {k: v for k, v in session.items() if k != 'kind'}
            self.scheduler.schedule_at(session['at_seconds'], f"{session['kind']}.start", payload)
        for at in plan.checkpoints:
            self.policies.schedule_checkpoint(at)
        for platform_id, platform in plan.platforms.items():
            if platform.controller is not None:
                self.policies.schedule_controller(0, platform_id, interval_seconds=platform.controller[0],
                                                  until_seconds=platform.controller[1])
        for item in plan.interventions:
            self._schedule_intervention(item, inputs.people)
        self._initialize_runner()

    def _schedule_intervention(self, item, people):
        if item['kind'] == 'launch':
            self.policies.schedule_intervention(item['at_seconds'], launch=item['platform'])
        elif item['kind'] == 'policy':
            config = item['policy']
            self.policies.schedule_intervention(item['at_seconds'], platform_id=item['platform'],
                config=PlatformPolicy.compile(overrides=config['parameters'], rules=config['rules'],
                                              campaigns=config['campaigns'], version=config['version'],
                                              fallback=config['fallback']))
        else:
            targets = [p['id'] for p in people if p['role'] == item['role']
                       and (p['id'] in item['people'] or (item['segment'] is not None and p['segment'] == item['segment']))]
            for person_id in targets:
                self.policies.schedule_intervention(item['at_seconds'], role=item['role'], person_id=person_id,
                                                    preferred_app=item['preferred_app'])

    def _initialize_runner(self):
        self.decisions = self.policies.decisions
        self.log_path = self.run_directory = None
        self._log_stream = None
        self.engine.add_listener(self._log_notification)

    def _register_sessions(self, registry):
        for kind, handler in (('shift.start', self._on_shift_start), ('shift.end', self._on_shift_end),
                              ('trip.start', self._on_trip_start)):
            registry.register(kind, handler)

    @property
    def horizon_seconds(self):
        return self.scenario['horizon_seconds']

    # ----------------------------------------------------------------------
    # Checkpoints
    # ----------------------------------------------------------------------

    def snapshot(self):
        """Serializable event-boundary checkpoint, including all private memory and random identities."""
        return {'schema_version': SNAPSHOT_SCHEMA_VERSION, 'seed': self.seed, 'engine': self.engine.snapshot(),
                'scheduler': self.scheduler.snapshot(), 'policies': self.policies.snapshot(),
                'profiles': {key: plain(profile) for key, profile in self.policies.profiles.items()},
                'scenario': self.scenario, 'inputs': self.inputs}

    @classmethod
    def restore(cls, snapshot):
        """Rebuild a run from a checkpoint using only the saved artifacts, not today's defaults."""
        snapshot = copy.deepcopy(snapshot)
        if snapshot['schema_version'] != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError('Unsupported simulation checkpoint schema')
        sim = cls.__new__(cls)
        sim.seed = snapshot['seed']
        registry = HandlerRegistry()
        sim.engine = MarketplaceEngine.restore(snapshot['engine'], registry)
        sim.world = sim.engine.world
        sim.scenario, sim.inputs = snapshot['scenario'], snapshot['inputs']
        configs = {p: PlatformPolicy.compile(overrides=c['parameters'], rules=c['rules'], campaigns=c['campaigns'],
                    version=c['version'], fallback=c['fallback']) for p, c in snapshot['policies']['platforms'].items()}
        profiles = {key: load_profile(values) for key, values in snapshot['profiles'].items()}
        sim.policies = PolicyRuntime(sim.engine, registry, sim.seed, configs, profiles,
                                     implementations=snapshot['policies']['implementations'])
        sim.policies.restore_memory(snapshot['policies'])
        sim._register_sessions(registry)
        sim.scheduler = Scheduler.restore(snapshot['scheduler'], registry)
        sim.engine.bind(sim.scheduler)
        sim._initialize_runner()
        return sim

    # ----------------------------------------------------------------------
    # Clock and execution
    # ----------------------------------------------------------------------

    @property
    def current_time(self):
        return self.scheduler.now

    def advance_to(self, target_time):
        """Process every event due up to the target time, then set the clock there."""
        self.scheduler.advance_to(target_time)

    def _write_log(self, kind, **data):
        """Write raw observations only; metric calculations live in metrics.py."""
        if self._log_stream is not None:
            record = {'type': kind, 'at_seconds': self.current_time, **data}
            self._log_stream.write(json.dumps(record, allow_nan=False, separators=(',', ':')) + '\n')

    def _log_notification(self, notification):
        if self._log_stream is not None:
            self._write_log('notification', notification=plain(notification))

    def run(self, *, until_seconds=None, time_scale=False, log_dir='logs'):
        """Advance to the scenario horizon (or an earlier time) and save one JSON Lines simulation.log.

        No summary, interval aggregation, or analysis runs here. After this
        returns, use ``python metrics.py PATH/TO/simulation.log`` separately.
        time_scale is simulated seconds per real second; False skips sleeping.
        """
        if time_scale is not False and (isinstance(time_scale, bool) or not isinstance(time_scale, (int, float))
                                        or not math.isfinite(time_scale) or time_scale <= 0):
            raise ValueError('time_scale must be a finite positive number or False')
        end_time = self.horizon_seconds if until_seconds is None else until_seconds
        if isinstance(end_time, bool) or not isinstance(end_time, (int, float)) or not math.isfinite(end_time):
            raise ValueError('until_seconds must be a finite number')
        if end_time < self.current_time:
            raise ValueError('Run end time is before the current simulation time')
        if end_time > self.horizon_seconds:
            raise ValueError('Run end time is after the scenario horizon')

        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.run_directory = Path(mkdtemp(dir=directory, prefix=f'simulation-{datetime.now():%Y%m%d-%H%M%S}-')).resolve()
        self.log_path = self.run_directory / 'simulation.log'
        source_dir = Path(__file__).resolve().parent
        calendar = self.scenario['resolved']['world']['calendar']
        with self.log_path.open('w', encoding='utf-8') as stream:
            self._log_stream = stream
            try:
                self._write_log('run_started', schema_version=2, run_id=self.run_directory.name,
                    created_at=datetime.now(timezone.utc).isoformat(), start_hour=calendar['hour'],
                    start_weekday=calendar['weekday'], target_end_seconds=end_time, time_scale=time_scale,
                    scenario={'name': self.scenario['name'], 'preset': self.scenario['preset'],
                              'seed': self.seed, 'fingerprints': self.scenario['fingerprints'],
                              'inputs_fingerprint': self.inputs['fingerprint']},
                    source_sha256={name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                                   for name in SOURCE_FILES})
                self._write_log('state', boundary='initial', snapshot=self.snapshot())
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
                    failure = {'error_type': type(error).__name__, 'error': str(error),
                               'traceback': traceback.format_exc()}
                    if isinstance(error, HandlerFailure):
                        failure['failed_event'] = error.record.to_dict()
                        failure['events_before_failure'] = [record.to_dict() for record in error.recent]
                    runtime = time.perf_counter() - started_at
                    self._write_log('state', boundary='final', snapshot=self.snapshot())
                    self._write_log('run_finished', status='failed', runtime_seconds=runtime, **failure)
                    raise
                else:
                    runtime = time.perf_counter() - started_at
                    self._write_log('state', boundary='final', snapshot=self.snapshot())
                    self._write_log('run_finished', status='completed', runtime_seconds=runtime)
            finally:
                self._log_stream = None

        print(f'Simulation finished. Log: {self.log_path}')

    # ----------------------------------------------------------------------
    # Exogenous session handlers: the only work the scenario schedules directly
    # ----------------------------------------------------------------------

    def _on_shift_start(self, event):
        driver_id = event.payload['driver']
        shift = self.engine.start_shift(driver_id, tuple(event.payload['location']))
        if event.payload['shift_seconds'] is not None:
            self.scheduler.schedule_after(event.payload['shift_seconds'], 'shift.end',
                                          {'driver': driver_id, 'shift_id': shift.id})

    def _on_shift_end(self, event):
        driver = self.engine.drivers[event.payload['driver']]
        if driver.shift_id == event.payload['shift_id']:
            self.engine.end_shift(driver.id)

    def _on_trip_start(self, event):
        self.engine.begin_intent(event.payload['rider'], tuple(event.payload['origin']),
                                 tuple(event.payload['destination']), source_id=event.payload['id'])


if __name__ == '__main__':
    import argparse
    from scenario import PRESETS, Scenario

    parser = argparse.ArgumentParser(description='Run one published preset or a saved resolved definition once.')
    parser.add_argument('scenario', help=f"preset id ({', '.join(sorted(PRESETS))}) or a JSON definition path")
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--log-dir', default='logs')
    parser.add_argument('--hours', type=float, default=None, help='stop before the horizon, in simulated hours')
    arguments = parser.parse_args()
    selected = Scenario(preset=arguments.scenario) if arguments.scenario in PRESETS else Scenario.load(arguments.scenario)
    run_scenario(selected, seed=arguments.seed, log_dir=arguments.log_dir,
                 until_seconds=None if arguments.hours is None else arguments.hours * HOUR)
