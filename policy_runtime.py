"""Policy/engine adapter: scope observations, schedule proposals, validate and apply.

Only this adapter can see both the engine and policies. Policy implementations
receive detached immutable snapshots, explicit memory, and keyed random values.
"""
import math
from dataclasses import asdict

from behavior_policy import (DriverPolicy, DriverPolicyV2, Download, EvolutionPolicy, EvolutionPolicyV2,
                             ExpandApps, Extend, OpenApp, OrderQuote, Reposition, Respond, RiderPolicy,
                             RiderPolicyV2, SwitchPreferred, personal_pickup_eta, rider_reward, driver_reward)
from marketplace_policy import (EtaProposal, MarketplacePolicy, OfferProposal, PlatformPolicy,
                                QuoteProposal)
from marketplace_engine import MAX_COMMITMENTS, CommandRejected, RegulationRejected
from policy_contracts import Cancel, Decision, RandomValues, Stop, Transfer, Wait, finite_number, freeze, plain


# Policy implementations are selected by identity and version, never by an
# anonymous callback embedded in scenario data. A replacement must consume the
# family's parameter schema (or a subclass) because profiles and platform
# configurations are typed values, and must support the family's hooks.
POLICY_FAMILIES = {
    'rider': (RiderPolicy, ('decide', 'progress')),
    'driver': (DriverPolicy, ('expand', 'respond', 'progress')),
    'evolution': (EvolutionPolicy, ('checkpoint',)),
    'marketplace': (MarketplacePolicy, ('quote', 'dispatch', 'revise', 'cancel', 'controller', 'observe')),
}
POLICY_REGISTRY = {family: {} for family in POLICY_FAMILIES}


def policy_id(implementation):
    declaration = implementation.declaration
    return f'{declaration.name}@{declaration.version}'


def register_policy(family, implementation):
    """Register a trusted implementation under its declared name and version."""
    if family not in POLICY_FAMILIES:
        raise ValueError(f'Unknown policy family {family!r}')
    builtin, hooks = POLICY_FAMILIES[family]
    declaration = getattr(implementation, 'declaration', None)
    if declaration is None or not isinstance(declaration.name, str) or not isinstance(declaration.version, str):
        raise ValueError(f'{family} policy needs a Declaration with a name and version')
    if not issubclass(declaration.parameter_schema, builtin.declaration.parameter_schema):
        raise ValueError(f'{family} policy must accept {builtin.declaration.parameter_schema.__name__} parameters')
    missing = [hook for hook in hooks if not callable(getattr(implementation, hook, None))]
    if missing or set(hooks) - set(declaration.hooks):
        raise ValueError(f'{family} policy must implement hooks {hooks}')
    identifier = policy_id(implementation)
    existing = POLICY_REGISTRY[family].get(identifier)
    if existing is not None and existing is not implementation:
        raise ValueError(f'{family} policy {identifier} is already registered')
    POLICY_REGISTRY[family][identifier] = implementation
    return identifier


def policy_class(family, identifier):
    try:
        return POLICY_REGISTRY[family][identifier]
    except KeyError:
        known = ', '.join(sorted(POLICY_REGISTRY.get(family, {}))) or 'none'
        raise ValueError(f'Unknown {family} policy {identifier!r}; registered: {known}') from None


for _family, (_implementation, _) in POLICY_FAMILIES.items():
    register_policy(_family, _implementation)

BUILTIN_IMPLEMENTATIONS = {family: policy_id(implementation) for family, (implementation, _) in POLICY_FAMILIES.items()}

# Participant policies v2 (AST-209): additive registered implementations. BUILTIN_IMPLEMENTATIONS
# still names @1, so every preset and every existing scenario keeps selecting @1; register_policy's
# issubclass(parameter_schema, ...) check passes because the V2 trait classes subclass the V1 ones.
for _family, _implementation in (('rider', RiderPolicyV2), ('driver', DriverPolicyV2), ('evolution', EvolutionPolicyV2)):
    register_policy(_family, _implementation)


class PolicyRuntime:
    schema_version = 2

    def __init__(self, engine, registry, seed, platforms, profiles, implementations=None, zones=None):
        self.engine, self.seed = engine, seed
        self.zones = dict(zones or {})
        selected = dict(implementations or {})
        marketplace = selected.pop('marketplace', None)
        marketplace = ({p: BUILTIN_IMPLEMENTATIONS['marketplace'] for p in platforms} if marketplace is None
                       else dict(marketplace))
        if set(marketplace) != set(platforms):
            raise ValueError('Marketplace implementations must name exactly the configured platforms')
        self.implementations = {family: selected.get(family, BUILTIN_IMPLEMENTATIONS[family])
                                for family in ('rider', 'driver', 'evolution')}
        self.implementations['marketplace'] = marketplace
        self.bindings = {family: policy_class(family, identifier)
                         for family, identifier in self.implementations.items() if family != 'marketplace'}
        self.platforms = {p: policy_class('marketplace', marketplace[p])(c) for p, c in platforms.items()}
        self.observing = {p: self._observed_kinds(policy) for p, policy in self.platforms.items()}
        self.profiles = profiles
        self.platform_memory = {p: {} for p in platforms}
        self.people = {key: {'preferred_app': profile.preferred_app, 'scores': {},
                            'evolution': {}, 'observations': [], 'exposure': {}, 'offer_counts': {},
                            'generation': 0, 'last_checkpoint': 0, 'phase': 'off', 'apps': [], 'at': 0}
                       for key, profile in profiles.items()}
        self.intents = {}
        self.dispatch_generation = {}
        self.decisions = []
        self.observations = []
        self.interventions = []
        # Transient re-entrancy guard for apply_availability (pause_app/resume_app's
        # driver_availability notification re-enters sync_driver); always False at an event
        # boundary, so it is deliberately not part of snapshot().
        self._pausing = False
        for kind, handler in (
            ('policy.rider.decide', self._rider_decide), ('policy.rider.open', self._rider_open),
            ('policy.rider.progress', self._rider_progress), ('policy.rider.deadline', self._rider_deadline),
            ('policy.dispatch', self._dispatch),
            ('policy.driver.expand', self._driver_expand), ('policy.driver.respond', self._driver_respond),
            ('policy.driver.progress', self._driver_progress), ('policy.driver.idle', self._driver_idle),
            ('policy.checkpoint', self._checkpoint),
            ('policy.intervention', self._intervene), ('policy.controller', self._controller),
            ('policy.observe_window', self._observe_window), ('policy.revise', self._revise_tick),
            ('policy.ledger.lease', self._ledger_lease), ('policy.ledger.dividend', self._ledger_dividend),
        ):
            registry.register(kind, handler)
        engine.add_listener(self.on_notification)

    @property
    def now(self):
        return self.engine.now

    @staticmethod
    def key(role, person_id):
        return f'{role}:{person_id}'

    def _observed_kinds(self, policy):
        """Config-derived gate: which platform-audience kinds this policy's observe hook actually
        needs. A policy may not request an undeclared kind; empty for every shipped preset, so
        on_notification never calls observe() and platform_memory stays untouched."""
        declared = set(policy.declaration.observations)
        requested = set(policy.observed_kinds())
        if requested - declared:
            raise ValueError(f'Policy requested undeclared observation kinds {sorted(requested - declared)}')
        return frozenset(requested)

    def zone_of(self, point):
        """First zone id (sorted) whose closed box contains point, else None -- matches
        scenario._check_zones' semantics; overlap is resolved deterministically by sorted id."""
        for zid in sorted(self.zones):
            box = self.zones[zid]
            if box['min'][0] <= point[0] <= box['max'][0] and box['min'][1] <= point[1] <= box['max'][1]:
                return zid
        return None

    def zone_boxes(self):
        """{zid: {'min', 'max', 'centroid'}} from self.zones ({} with no zones configured) --
        AST-210's idle hook input, so a driver's reposition destination is always a real zone
        centroid, never a value the runtime invents past what the scenario declared."""
        return {zid: {'min': tuple(box['min']), 'max': tuple(box['max']),
                      'centroid': ((box['min'][0] + box['max'][0]) / 2, (box['min'][1] + box['max'][1]) / 2)}
                for zid, box in self.zones.items()}

    def state(self, role, person_id):
        return self.people[self.key(role, person_id)]

    def profile(self, role, person_id):
        return self.profiles[self.key(role, person_id)]

    def schedule(self, delay, kind, **payload):
        return self.engine.scheduler.schedule_after(delay, kind, payload)

    def record(self, role, identity, hook, decision, version=None):
        if not isinstance(decision, Decision):
            raise ValueError('Policies must return a typed Decision')
        if version is None:
            version = self.bindings[role].declaration.version
        self.decisions.append({'type': 'policy_decision', 'at_seconds': self.now, 'role': role,
                               'identity': identity, 'hook': hook, 'policy_version': version,
                               'action': type(decision.action).__name__, 'proposal': plain(decision.action),
                               'explanation': decision.explanation})

    def visible(self, platform_id, role, person_id):
        profile = self.profile(role, person_id)
        count = self.platform_memory[platform_id].get('completed', {}).get(self.key(role, person_id), 0)
        return {'role': role, 'completed_rides': count, 'new_user': count == 0,
                **({'segment': profile.segment} if platform_id in profile.disclosed_to else {})}

    def platform_context(self, platform_id, *, request=None, order=None, **extra):
        view = self.engine.platform_view(platform_id)
        # Only own orders/offers and authorized presence enter the policy context.
        # Materialize just records referenced by visible drivers and this order.
        drivers, orders, offers = [], {}, {}
        for d in view.drivers():
            drivers.append({'driver_id': d.driver_id, 'position': d.position, 'accepting': d.accepting,
                            'own_order_ids': d.own_order_ids, 'pending_offer_ids': d.pending_offer_ids,
                            'visible': self.visible(platform_id, 'driver', d.driver_id)})
            for order_id in d.own_order_ids:
                orders[str(order_id)] = view.order(order_id)
        if order:
            for offer_id in order.offer_ids:
                offers[str(offer_id)] = view.offer(offer_id)
        rider_id = request.rider_id if request else order.rider_id
        visible = self.visible(platform_id, 'rider', rider_id)
        if self.zones and (request is not None or order is not None):
            origin = request.origin if request is not None else order.pickup
            destination = request.destination if request is not None else order.destination
            visible = {**visible, 'origin_zone': self.zone_of(origin), 'destination_zone': self.zone_of(destination)}
        if order is not None:
            extra = {'quote': view.quote(order.quote_id), **extra}
        return freeze({'now': self.now, 'platform_id': platform_id, 'request': request, 'order': order,
                       'drivers': drivers, 'own_orders': orders, 'own_offers': offers,
                       'visible': visible, **extra})

    def usable_apps(self, role, person_id):
        person = self.engine._person(role, person_id)
        usable = person.apps & person.accounts
        if role == 'driver':
            usable &= self.engine.cars[person.car_id].registrations
        return sorted(p for p in usable if self.engine.platforms[p].launched)

    def accessible_apps(self, role, person_id):
        """Access independent of launch state; a launch intervention can precede a preference change."""
        person = self.engine._person(role, person_id)
        usable = person.apps & person.accounts
        if role == 'driver':
            usable &= self.engine.cars[person.car_id].registrations
        return sorted(usable)

    def announced(self, role, person_id):
        """Only public campaigns on personally known apps; never query an unseen quote/supply."""
        profile = self.profile(role, person_id)
        result = {}
        for p in profile.awareness:
            if p not in self.platforms or not self.engine.platforms[p].launched:
                continue
            campaigns = [c for c in self.platforms[p].config.campaigns
                         if c.awareness == 'announced' and c.eligible(self.now, self.visible(p, role, person_id))]
            # This is an announced incentive salience, not a hidden route-specific quote.
            salience = max((c.discount_minor / 1000 + c.discount_fraction if role == 'rider'
                            else c.bonus_minor / 1000 for c in campaigns), default=0)
            if role == 'driver' and self.platforms[p].config.parameters.announce_terms:
                # A driver also learns the platform's public (base, not rule-specific) commission --
                # comparable in scale to the preferred-app bonus of 1 in behavior_policy.ranked_apps.
                salience += 1 - self.platforms[p].config.parameters.commission_fraction
            result[p] = salience
        return result

    def rider_context(self, intent):
        s = self.state('rider', intent.rider_id)
        profile = self.profile('rider', intent.rider_id)
        return freeze({'now': self.now, 'intent': intent,
                       'quotes': self.engine.rider_view(intent.rider_id).quotes(),
                       'usable_apps': self.usable_apps('rider', intent.rider_id),
                       'preferred_app': s['preferred_app'], 'scores': s['scores'],
                       'announced': self.announced('rider', intent.rider_id),
                       'failures': s.get('failures', {}), 'sticky_preference': s.get('sticky_preference', False),
                       'known_launched_apps': [p for p in profile.awareness
                                               if p in self.engine.platforms and self.engine.platforms[p].launched],
                       # rider_search@2 install trigger (behavior_policy.RiderPolicyV2.decide): this
                       # rider's own installed apps, deliberately not usable_apps (apps & accounts &
                       # launched, above) -- an installed app the rider has no account on yet
                       # (accounts may be an authored subset of apps; scenario._check_access permits
                       # it) is not usable, but must not be re-offered as a Download candidate. This
                       # is exactly what the Download branch below rejects as already-installed.
                       'installed_apps': sorted(self.engine.riders[intent.rider_id].apps)})

    def driver_context(self, driver_id, **extra):
        view, s = self.engine.driver_view(driver_id), self.state('driver', driver_id)
        pending = view.pending_offers()
        return freeze({'now': self.now, 'position': view.position, 'commitments': view.commitments(),
                       'free_slots': view.free_slots, 'exit_requested': view.exit_requested,
                       'open_apps': view.open_apps, 'usable_apps': self.usable_apps('driver', driver_id),
                       'preferred_app': s['preferred_app'], 'scores': s['scores'],
                       'announced': self.announced('driver', driver_id),
                       # driver_participation@2 (plan section 4.D): an enriched, id-sorted view of
                       # this driver's own pending offers (behind-the-scenes information already
                       # disclosed by offer_received); @1 never reads any of these five keys.
                       'pending_offers': tuple({'offer_id': o.offer_id, 'platform_id': o.platform_id,
                                                'order_id': o.order_id, 'payout_minor': o.payout_minor,
                                                'bonus_minor': o.bonus_minor, 'eta_seconds': o.eta_seconds,
                                                'expires_at': o.expires_at, 'created_at': o.created_at,
                                                'pickup': o.pickup, 'destination': o.destination,
                                                'private_eta_seconds': self.private_eta(driver_id, o.pickup)}
                                              for o in pending),
                       'estimated_wait_seconds': s['evolution'].get('estimated_wait_seconds', {}),
                       'speed_kmh': self.engine.world.speed_kmh,
                       'tie_draw': self.tie_draw(driver_id, [o.offer_id for o in pending]),
                       **extra})

    def driver_terms(self, platform_id, driver_id):
        """The terms a real app shows this driver on this platform: their own resolved
        cancellation penalty and lockout, plus the active guarantee program. Resolution uses this
        driver's own `visible` dict, so nothing about another person leaks (driver_participation@2,
        plan section 4.D)."""
        policy = self.platforms[platform_id]
        params, _ = policy.config.resolve(self.visible(platform_id, 'driver', driver_id), self.now)
        programs = sorted((p for p in policy.config.programs if p.kind == 'hourly_guarantee'), key=lambda p: p.id)
        return {'platform_id': platform_id,
                'cancellation_penalty_minor': params.driver_cancellation_penalty_minor,
                'lockout_seconds': params.driver_lockout_seconds,
                'guarantee': (None if not programs else
                              {'program_id': programs[0].id, 'floor_minor': programs[0].floor_minor,
                               'window_seconds': programs[0].window_seconds,
                               'min_acceptance_rate': programs[0].min_acceptance_rate,
                               'min_online_seconds': programs[0].min_online_seconds})}

    def tie_draw(self, driver_id, offer_ids):
        """A symmetric draw so two co-pending responses from the same driver agree on a coin flip
        (driver_participation@2 response_rule='best_pending', tie_break='random'). Identity
        excludes the offer id -- deliberately different from the per-offer acceptance RandomValues
        identity, which must never change, or every @1 acceptance draw would move."""
        return RandomValues(self.seed, ('tie_break', 'driver', driver_id)).uniform(sorted(offer_ids))

    def quote(self, platform_id, request):
        policy = self.platforms[platform_id]
        context = self.platform_context(platform_id, request=request)
        decision = policy.quote(context, freeze(self.platform_memory[platform_id]),
                                RandomValues(self.seed, ('quote', platform_id, request.intent_id)))
        self.record('platform', platform_id, 'quote', decision, policy.config.version)
        if isinstance(decision.action, Stop):
            self.platform_memory[platform_id] = decision.memory
            self.observations.append({'type': 'quote_refused', 'at_seconds': self.now, 'platform_id': platform_id,
                                      'intent_id': request.intent_id, 'rider_id': request.rider_id,
                                      'reason': decision.action.reason})
            # Nothing else wakes the rider without a quote_received; re-queue so a missing quote is
            # treated like missing supply and the rider can inspect another app.
            self.queue_rider(request.intent_id, self.profile('rider', request.rider_id).rider.retry_seconds)
            return
        if not isinstance(decision.action, QuoteProposal):
            raise ValueError('Quote hook must produce QuoteProposal')
        self.platform_memory[platform_id] = decision.memory
        try:
            self.engine.issue_quote(platform_id, request.intent_id, **asdict(decision.action))
        except RegulationRejected:
            self.observations.append({'type': 'command_result', 'at_seconds': self.now, 'platform_id': platform_id,
                                      'intent_id': request.intent_id, 'result': 'regulation_rejected'})
            self.queue_rider(request.intent_id, self.profile('rider', request.rider_id).rider.retry_seconds)

    def queue_dispatch(self, order_id, delay):
        generation = self.dispatch_generation.get(str(order_id), 0) + 1
        self.dispatch_generation[str(order_id)] = generation
        self.schedule(delay, 'policy.dispatch', order_id=order_id, generation=generation)

    def _dispatch(self, event):
        order = self.engine.orders[event.payload['order_id']]
        if order.state != 'open' or event.payload['generation'] != self.dispatch_generation[str(order.id)]:
            return
        p = order.platform_id
        policy = self.platforms[p]
        decision = policy.dispatch(self.platform_context(p, order=order), freeze(self.platform_memory[p]),
                                   RandomValues(self.seed, ('dispatch', p, order.id, len(order.offer_ids))))
        self.record('platform', p, 'dispatch', decision, policy.config.version)
        self.platform_memory[p] = decision.memory
        action = decision.action
        if isinstance(action, OfferProposal):
            try:
                self.engine.create_offer(p, order.id, **asdict(action))
            except RegulationRejected:
                self.observations.append({'type': 'command_result', 'at_seconds': self.now,
                                          'platform_id': p, 'order_id': order.id, 'result': 'regulation_rejected'})
                self.queue_dispatch(order.id, policy.config.parameters.retry_seconds)
            except CommandRejected:
                # Runtime legality can differ from the visible proposal. Do not disclose private cause.
                self.observations.append({'type': 'command_result', 'at_seconds': self.now,
                                          'platform_id': p, 'order_id': order.id, 'result': 'offer_unavailable'})
                self.queue_dispatch(order.id, policy.config.parameters.retry_seconds)
        elif isinstance(action, Wait):
            self.queue_dispatch(order.id, action.seconds)
        elif isinstance(action, Stop):
            self.cancel(Cancel(order.id, 'platform', action.reason))
        else:
            raise ValueError('Unsupported dispatch action')

    def revise(self, order):
        if order.terminal or not order.assignment or 'arrived' in order.timeline:
            return
        p, policy = order.platform_id, self.platforms[order.platform_id]
        decision = policy.revise(self.platform_context(p, order=order), freeze(self.platform_memory[p]),
                                 RandomValues(self.seed, ('eta', p, order.id, self.now)))
        self.record('platform', p, 'revise', decision, policy.config.version)
        self.platform_memory[p] = decision.memory
        if isinstance(decision.action, EtaProposal):
            self.engine.revise_pickup_eta(p, order.id, **asdict(decision.action))
        elif not isinstance(decision.action, Stop):
            raise ValueError('Unsupported ETA proposal')

    def _revise_tick(self, event):
        """A bounded periodic re-revision for one order (`revise_interval_seconds` > 0), so a
        rider's `eta_drift_cancel_seconds` sees drift without competitor telemetry. Started from
        order_assigned; reschedules itself only while the order is still awaiting pickup."""
        order = self.engine.orders[event.payload['order_id']]
        if order.terminal or not order.assignment or 'arrived' in order.timeline:
            return
        self.revise(order)
        interval = self.platforms[order.platform_id].config.parameters.revise_interval_seconds
        if interval > 0:
            self.schedule(interval, 'policy.revise', order_id=order.id)

    def cancel(self, request):
        order = self.engine.orders[request.order_id]
        if order.terminal:
            return False
        p, policy = order.platform_id, self.platforms[order.platform_id]
        decision = policy.cancel(self.platform_context(p, order=order, party=request.party, reason=request.reason),
                                 freeze(self.platform_memory[p]), RandomValues(self.seed, ('cancel', order.id, request.party)))
        self.record('platform', p, 'cancel', decision, policy.config.version)
        self.platform_memory[p] = decision.memory
        if isinstance(decision.action, Stop):
            return False
        if not isinstance(decision.action, Cancel):
            raise ValueError('Invalid cancellation proposal')
        a = decision.action
        if a.order_id != request.order_id or a.party != request.party:
            raise ValueError('Cancellation may only affect the requested order and party')
        self.engine.cancel_order(a.order_id, a.party, a.reason, rider_fee_minor=a.rider_fee_minor,
                                 driver_compensation_minor=a.driver_compensation_minor,
                                 driver_penalty_minor=a.driver_penalty_minor)
        return True

    def apply_transfer(self, platform_id, action):
        """Apply a Transfer proposal from platform `platform_id`. A platform may only fund its own."""
        if action.counterparty == 'platform' and action.platform_id not in (None, platform_id):
            raise ValueError('A platform may only post its own transfers')
        self.engine.post_transfer(action.reason, action.role, action.person_id, action.amount_minor,
                                  platform_id=platform_id if action.counterparty == 'platform' else None,
                                  counterparty=action.counterparty, order_id=action.order_id,
                                  program_id=action.program_id)

    def observe_context(self, platform_id, kind, data, **extra):
        """Detached context for a platform-audience notification the policy asked for (or the
        runtime-generated window_closed). order/offer are this platform's own records (own_orders/
        own_offers precedent); driver_id is the driver the notification is about, when there is
        one; visible matches the driver-side (or rider-side) dict quote/dispatch would resolve."""
        view = self.engine.platform_view(platform_id)
        order = view.order(data['order_id']) if kind.startswith('order_') else None
        offer = view.offer(data['offer_id']) if kind == 'offer_resolved' else None
        driver_id = data.get('driver_id')
        if driver_id is None and offer is not None:
            driver_id = offer.driver_id
        if driver_id is None and order is not None and order.assignment is not None:
            driver_id = order.assignment.driver_id
        visible = (self.visible(platform_id, 'driver', driver_id) if driver_id is not None
                  else self.visible(platform_id, 'rider', order.rider_id))
        return freeze({'now': self.now, 'platform_id': platform_id, 'kind': kind, 'data': data,
                       'order': order, 'offer': offer, 'driver_id': driver_id, 'visible': visible, **extra})

    def observe(self, platform_id, kind, data, **extra):
        context = self.observe_context(platform_id, kind, data, **extra)
        discriminator = data.get('order_id', data.get('offer_id', data.get('driver_id', data.get('window_start'))))
        policy = self.platforms[platform_id]
        decision = policy.observe(context, freeze(self.platform_memory[platform_id]),
                                  RandomValues(self.seed, ('observe', platform_id, kind, self.now, discriminator)))
        self.record('platform', platform_id, 'observe', decision, policy.config.version)
        self.platform_memory[platform_id] = decision.memory
        if isinstance(decision.action, Transfer):
            self.apply_transfer(platform_id, decision.action)
        elif not isinstance(decision.action, Stop):
            raise ValueError('Unsupported observe action')

    def schedule_program_windows(self):
        """Schedule each platform's first window-close event, aligned to the world clock: window
        start w = floor(now/window)*window, close at w+window. Call only from Simulation.__init__
        after engine.bind (self.engine.scheduler is not yet set inside PolicyRuntime.__init__);
        never from restore, where the pending event already lives in the scheduler snapshot."""
        for platform_id, policy in self.platforms.items():
            window = policy.config.parameters.guarantee_window_seconds
            if window > 0:
                start = math.floor(self.now / window) * window
                self.engine.scheduler.schedule_at(start + window, 'policy.observe_window',
                                                  {'platform_id': platform_id, 'window_seconds': window})

    def _observe_window(self, event):
        platform_id, window = event.payload['platform_id'], event.payload['window_seconds']
        close_at = self.now
        window_start = close_at - window
        members = sorted((driver_id for driver_id, driver in self.engine.drivers.items()
                          if platform_id in driver.apps & driver.accounts),
                         key=lambda d: (type(d).__name__, d))
        for driver_id in members:
            self.observe(platform_id, 'window_closed',
                        {'driver_id': driver_id, 'window_start': window_start, 'window_end': close_at},
                        driver_id=driver_id)
        # Reschedules unconditionally: a trailing pending event past the horizon is identical in
        # continuous and restored runs.
        self.engine.scheduler.schedule_at(close_at + window, 'policy.observe_window',
                                          {'platform_id': platform_id, 'window_seconds': window})

    def queue_rider(self, intent_id, delay):
        m = self.intents[str(intent_id)]
        m['generation'] = m.get('generation', 0) + 1
        self.schedule(delay, 'policy.rider.decide', intent_id=intent_id, generation=m['generation'])

    def _live_rider_event(self, event):
        intent = self.engine.intents[event.payload['intent_id']]
        m = self.intents[str(intent.id)]
        if not intent.live or intent.live_order_id is not None or m['generation'] != event.payload['generation']:
            return None
        return intent

    def _rider_decide(self, event):
        intent = self._live_rider_event(event)
        if intent is None:
            return
        t = self.profile('rider', intent.rider_id).rider
        decision = self.bindings['rider'](t).decide(self.rider_context(intent), freeze(self.intents[str(intent.id)]),
                                         RandomValues(self.seed, ('rider', intent.rider_id, intent.id)))
        self.record('rider', intent.rider_id, 'decide', decision)
        self.intents[str(intent.id)] = decision.memory
        action = decision.action
        if isinstance(action, OpenApp):
            if action.platform_id not in self.usable_apps('rider', intent.rider_id):
                raise ValueError('Policy requested an unusable app')
            self.schedule(t.opening_seconds, 'policy.rider.open', intent_id=intent.id,
                          generation=decision.memory['generation'], platform_id=action.platform_id)
        elif isinstance(action, OrderQuote):
            try:
                self.engine.place_order(intent.id, action.quote_id)
            except CommandRejected:
                self.queue_rider(intent.id, t.retry_seconds)
        elif isinstance(action, Stop):
            self.engine.end_intent(intent.id, action.reason)
        elif isinstance(action, Wait):
            self.queue_rider(intent.id, action.seconds)
        elif isinstance(action, SwitchPreferred):
            if action.platform_id not in self.usable_apps('rider', intent.rider_id):
                raise ValueError('Policy switched to an unusable app')
            rs = self.state('rider', intent.rider_id)
            rs['preferred_app'] = action.platform_id
            rs['sticky_preference'] = bool(action.sticky)
            self.observations.append({'type': 'preference_switched', 'at_seconds': self.now,
                'role': 'rider', 'person_id': intent.rider_id, 'platform_id': action.platform_id,
                'sticky': bool(action.sticky), 'cause': 'fatigue'})
            self.queue_rider(intent.id, t.retry_seconds)
        elif isinstance(action, Download):
            # Installing mid-search changes usable_apps, validated on the *next* decision; always
            # re-queue rather than acting on the new app within this same decision.
            p = action.platform_id
            profile = self.profile('rider', intent.rider_id)
            if (p not in profile.awareness or p not in self.engine.platforms
                    or not self.engine.platforms[p].launched or p in self.engine.riders[intent.rider_id].apps):
                raise ValueError('Policy downloaded an unknown, unlaunched or already-installed app')
            self.engine.install_app('rider', intent.rider_id, p)
            self.observations.append({'type': 'app_installed', 'at_seconds': self.now,
                'role': 'rider', 'person_id': intent.rider_id, 'platform_id': p})
            self.engine.activate_account('rider', intent.rider_id, p)
            self.observations.append({'type': 'account_activated', 'at_seconds': self.now,
                'role': 'rider', 'person_id': intent.rider_id, 'platform_id': p})
            self.queue_rider(intent.id, t.retry_seconds)
        else:
            raise ValueError('Unsupported rider action')

    def _rider_open(self, event):
        intent = self._live_rider_event(event)
        if intent is None:
            return
        t = self.profile('rider', intent.rider_id).rider
        if self.now >= intent.created_at + t.patience_seconds:
            self.queue_rider(intent.id, t.retry_seconds)
            return
        p = event.payload['platform_id']
        person = self.engine.riders[intent.rider_id]
        if p in person.open_apps:
            self.quote(p, self.engine.platform_view(p).rider_request(person.id))
        else:
            if p not in self.usable_apps('rider', person.id):
                # AST-210 robustness: a policy.rider.open event scheduled before a platform shut
                # down would otherwise call open_app on an unlaunched platform and crash the run.
                # usable_apps can only ever grow before phase 6 (no installs/accounts/launches are
                # ever revoked), so this branch is unreachable pre-phase-6 and changes no @1 trace.
                self.queue_rider(intent.id, t.retry_seconds)
                return
            self.engine.open_app('rider', person.id, p)

    def _rider_deadline(self, event):
        intent = self.engine.intents[event.payload['intent_id']]
        if intent.live and intent.live_order_id is None:
            self.engine.end_intent(intent.id, 'search patience exhausted')

    def _rider_progress(self, event):
        order = self.engine.orders[event.payload['order_id']]
        if order.terminal:
            return
        policy = self.bindings['rider'](self.profile('rider', order.rider_id).rider)
        decision = policy.progress(freeze({'now': self.now, 'order': order}),
                                   freeze(self.intents[str(order.intent_id)]), RandomValues(self.seed, ('rider_progress', order.id)))
        self.record('rider', order.rider_id, 'progress', decision)
        self.intents[str(order.intent_id)] = decision.memory
        if isinstance(decision.action, Cancel):
            self.cancel(decision.action)

    def sync_driver(self, driver_id):
        """Accrue opportunity from the previously observed phase and open apps."""
        s, driver = self.state('driver', driver_id), self.engine.drivers[driver_id]
        elapsed = self.now - s['at']
        if s['phase'] in ('idle', 'busy') and elapsed:
            for p in s['apps']:
                phases = s['exposure'].setdefault(p, {'idle': 0, 'busy': 0})
                phases[s['phase']] += elapsed
                self.observations.append({'type': 'opportunity_exposure', 'driver_id': driver_id,
                    'platform_id': p, 'phase': s['phase'], 'start_seconds': s['at'], 'end_seconds': self.now})
        view = self.engine.driver_view(driver_id)
        old_phase = s['phase']
        s['at'] = self.now
        s['apps'] = sorted(driver.open_apps)
        s['phase'] = ('off' if not view.on_shift or view.exit_requested else
                      'full' if view.free_slots == 0 else 'busy' if driver.commitments else 'idle')
        if s['phase'] != old_phase:
            s['generation'] += 1
            if s['phase'] in ('idle', 'busy'):
                s['search'] = {'no_offer_since': self.now, 'visited': sorted(driver.open_apps)}
                self.queue_expansion(driver_id)
        # One call site so no branch can forget it: after_service='preferred' precedent.
        self.apply_availability(driver_id)

    def apply_availability(self, driver_id):
        """Trait-selected app pausing (driver_participation@2 `availability`), applied by the
        runtime exactly as `after_service='preferred'` already is. `pause_app`/`resume_app`
        publish `driver_availability`, whose listener (`on_notification`) calls `sync_driver`,
        which calls back here; `self._pausing` makes the re-entrant inner call a no-op so the
        outer loop finishes the remaining apps without recursing."""
        mode = getattr(self.profile('driver', driver_id).driver, 'availability', 'always_open')
        if mode == 'always_open' or self._pausing:
            return
        driver = self.engine.drivers[driver_id]
        open_apps = sorted(driver.open_apps)
        if not driver.shift_id:
            wanted = set()
        elif mode == 'pause_when_full':
            wanted = set(open_apps) if len(driver.commitments) >= MAX_COMMITMENTS else set()
        else:  # pause_while_serving_other
            serving = {self.engine.orders[o].platform_id for o in driver.commitments}
            wanted = {p for p in open_apps if p not in serving} if driver.commitments else set()
        self._pausing = True
        try:
            for p in open_apps:
                if p in wanted and p not in driver.paused_apps:
                    self.engine.pause_app(driver_id, p)
                elif p not in wanted and p in driver.paused_apps:
                    self.engine.resume_app(driver_id, p)
        finally:
            self._pausing = False

    def queue_idle(self, driver_id, delay=None):
        """Queue the driver idle hook (AST-210): a no-op unless the bound driver policy declares
        an `idle` hook -- the default-off switch, so `@1` (and a default-trait `@2`) never gains
        this scheduled event. Generation-guarded like every other driver hook."""
        if 'idle' not in self.bindings['driver'].declaration.hooks:
            return
        s, t = self.state('driver', driver_id), self.profile('driver', driver_id).driver
        self.schedule(t.no_offer_seconds if delay is None else delay, 'policy.driver.idle',
                      driver_id=driver_id, generation=s['generation'])

    def queue_expansion(self, driver_id, delay=None):
        # Queued first, before this function's own early returns, so a driver with no unopened
        # apps left (which would make queue_expansion itself return early below) still gets the
        # idle hook at the same no-offer threshold.
        self.queue_idle(driver_id)
        s, t = self.state('driver', driver_id), self.profile('driver', driver_id).driver
        if s['phase'] not in ('idle', 'busy') or (s['phase'] == 'busy' and not t.expand_while_busy):
            return
        unseen = set(self.usable_apps('driver', driver_id)) - set(s['apps']) - set(s.get('search', {}).get('visited', []))
        if not unseen:
            return
        self.schedule(t.no_offer_seconds if delay is None else delay, 'policy.driver.expand',
                      driver_id=driver_id, generation=s['generation'])

    def _driver_expand(self, event):
        driver_id = event.payload['driver_id']
        s = self.state('driver', driver_id)
        if event.payload['generation'] != s['generation']:
            return
        self.sync_driver(driver_id)
        if event.payload['generation'] != s['generation']:
            return
        t = self.profile('driver', driver_id).driver
        decision = self.bindings['driver'](t).expand(self.driver_context(driver_id), freeze(s.get('search', {})),
                                          RandomValues(self.seed, ('expansion', driver_id, s['generation'])))
        self.record('driver', driver_id, 'expand', decision)
        s['search'] = decision.memory
        a = decision.action
        if isinstance(a, Wait):
            self.queue_expansion(driver_id, a.seconds)
        elif isinstance(a, ExpandApps):
            required = {c.platform_id for c in self.engine.driver_view(driver_id).commitments()}
            if set(a.close_apps) & required or a.open_app not in self.usable_apps('driver', driver_id):
                raise ValueError('App changes must retain commitments and use usable apps')
            for p in a.close_apps:
                self.engine.close_app('driver', driver_id, p)
            self.engine.open_app('driver', driver_id, a.open_app)
            self.sync_driver(driver_id)
            s['generation'] += 1
            self.queue_expansion(driver_id, t.further_opening_seconds)
        elif not isinstance(a, Stop):
            raise ValueError('Unsupported expansion action')

    def _driver_idle(self, event):
        """AST-210: fires after an unqueued drop-off and at the no-offer threshold (queue_idle).
        The precondition re-check covers ordinary races (a commitment, exit request, or
        deactivation landed first) with a silent return, not a bug. Arrival at a reposition
        destination does not queue another idle event and does not bump generation
        (marketplace_engine.reposition/_end_relocation touch no PolicyRuntime state), so a driver
        cannot oscillate between two zones inside one idle spell."""
        driver_id = event.payload['driver_id']
        s = self.state('driver', driver_id)
        if event.payload['generation'] != s['generation'] or 'idle' not in self.bindings['driver'].declaration.hooks:
            return
        driver, view = self.engine.drivers[driver_id], self.engine.driver_view(driver_id)
        if (driver.shift_id is None or driver.commitments or driver.service_id is not None
                or driver.relocation_id is not None or view.exit_requested or driver.deactivated_at is not None):
            return
        t = self.profile('driver', driver_id).driver
        context = self.driver_context(driver_id, zones=self.zone_boxes(), current_zone=self.zone_of(view.position),
                                      zone_scores=s['evolution'].get('zone_scores', {}))
        decision = self.bindings['driver'](t).idle(context, freeze(s.get('idle', {})),
                                                    RandomValues(self.seed, ('idle', driver_id, s['generation'])))
        self.record('driver', driver_id, 'idle', decision)
        s['idle'] = decision.memory
        a = decision.action
        if isinstance(a, Reposition):
            self.engine.reposition(driver_id, tuple(a.destination))
        elif not isinstance(a, Stop):
            raise ValueError('Unsupported idle action')

    def private_eta(self, driver_id, pickup):
        """Personal estimate may use the driver's own service across apps."""
        orders = [freeze(self.engine.orders[i]) for i in self.engine.drivers[driver_id].commitments]
        remaining, position = personal_pickup_eta(self.now, self.engine.position_of('driver', driver_id),
            orders, self.engine.world.speed_kmh, self.engine.world.boarding_seconds)
        return remaining + self.engine.world.travel_seconds(math.dist(position, pickup))

    def _driver_respond(self, event):
        offer = self.engine.offers[event.payload['offer_id']]
        if offer.state != 'pending':
            return
        order = self.engine.orders[offer.order_id]
        driver_id = offer.driver_id
        context = self.driver_context(driver_id, offer=offer, private_eta_seconds=self.private_eta(driver_id, order.pickup),
                                      terms=self.driver_terms(offer.platform_id, driver_id))
        s = self.state('driver', driver_id)
        decision = self.bindings['driver'](self.profile('driver', driver_id).driver).respond(context,
                    freeze(s.get('response', {})), RandomValues(self.seed, ('response', driver_id, offer.platform_id, offer.id)))
        self.record('driver', driver_id, 'respond', decision)
        if not isinstance(decision.action, Respond) or decision.action.offer_id != offer.id:
            raise ValueError('Response must refer to the current offer')
        s['response'] = decision.memory
        diagnostic = self.decisions[-1]
        disposition = self.engine.respond_to_offer(offer.id, decision.action.accept)
        s['response']['last_result'] = disposition
        diagnostic['result'] = disposition
        self.sync_driver(driver_id)

    def _driver_progress(self, event):
        order = self.engine.orders[event.payload['order_id']]
        if order.terminal or not order.assignment:
            return
        driver_id = order.assignment.driver_id
        t = self.profile('driver', driver_id).driver
        s = self.state('driver', driver_id)
        decision = self.bindings['driver'](t).progress(
            freeze({'now': self.now, 'order': order, 'private_eta_seconds': self.private_eta(driver_id, order.pickup),
                   'terms': self.driver_terms(order.platform_id, driver_id)}),
            freeze(s.get('progress', {})), RandomValues(self.seed, ('driver_progress', driver_id, order.id)))
        self.record('driver', driver_id, 'progress', decision)
        s['progress'] = decision.memory
        if isinstance(decision.action, Cancel):
            self.cancel(decision.action)
        elif (getattr(t, 'cancel_check_seconds', 0) > 0 and order.state == 'assigned'
              and 'boarded' not in order.timeline):
            # Bounded by the order reaching boarding or a terminal state (both re-checked here and
            # at this handler's own top-of-function guard next time), so no generation guard is needed.
            self.schedule(t.cancel_check_seconds, 'policy.driver.progress', order_id=order.id)

    def shift_end(self, driver_id):
        """AST-210: called from main._on_shift_end at the shift's own scheduled end. Returns the
        number of seconds to extend by, or 0 to end now. `0` immediately (no decision recorded, no
        state key written) when the bound driver policy does not declare a `shift_end` hook -- the
        `@1` path, so main's call sequence is exactly today's. Otherwise the policy proposes an
        `Extend`; this is the sole place that clamps it to the remaining `max_extension_seconds`
        and persists `extension_used_seconds`, so a policy that ignores its own cap can never
        exceed it. The persisted budget is scoped to `shift.id`: it is read back only when the
        stored `shift_id` matches the shift currently ending, so a later shift for the same driver
        starts with a fresh budget instead of inheriting an earlier shift's leftover usage."""
        if 'shift_end' not in self.bindings['driver'].declaration.hooks:
            return 0
        driver = self.engine.drivers[driver_id]
        shift = self.engine.shifts[driver.shift_id]
        s, t = self.state('driver', driver_id), self.profile('driver', driver_id).driver
        window_start = shift.started_at
        shift_net_minor = (
            sum(settlement.driver_payout_minor for settlement in self.engine.settlements.values()
               if settlement.driver_id == driver_id and settlement.at >= window_start)
            + sum(transfer.amount_minor for transfer in self.engine.transfers.values()
                 if transfer.role == 'driver' and transfer.person_id == driver_id and transfer.at >= window_start))
        extension_state = s.get('shift_extension', {})
        # AST-210 review fix: the budget is per-shift, not per-driver-forever. A prior shift's
        # leftover state (keyed by its own shift.id) must not bleed into this one, or only the
        # first shift a driver ever works could extend -- exactly the bug this scoping closes.
        used = extension_state.get('used_seconds', 0) if extension_state.get('shift_id') == shift.id else 0
        context = self.driver_context(driver_id, shift_net_minor=shift_net_minor, extension_used_seconds=used)
        decision = self.bindings['driver'](t).shift_end(context, freeze(s.get('shift_end', {})),
                    RandomValues(self.seed, ('shift_end', driver_id, shift.id, used)))
        self.record('driver', driver_id, 'shift_end', decision)
        s['shift_end'] = decision.memory
        a = decision.action
        if isinstance(a, Extend):
            granted = min(a.seconds, t.max_extension_seconds - used)
            if granted <= 0:
                return 0
            s['shift_extension'] = {'shift_id': shift.id, 'used_seconds': used + granted}
            self.observations.append({'type': 'shift_extended', 'at_seconds': self.now, 'driver_id': driver_id,
                                      'seconds': granted, 'total_seconds': used + granted})
            return granted
        if isinstance(a, Stop):
            return 0
        raise ValueError('Unsupported shift_end action')

    def on_notification(self, n):
        e, d, kind, person_id = self.engine, n.data, n.kind, n.audience_id
        if n.audience == 'platform':
            if kind in ('driver_app_opened', 'driver_app_closed', 'driver_availability'):
                driver_id = d['driver_id']
                self.sync_driver(driver_id)
                state = self.state('driver', driver_id)
                state['generation'] += 1
                self.queue_expansion(driver_id)
            elif kind == 'rider_app_opened' and d['request'] is not None:
                self.quote(person_id, d['request'])
            elif kind == 'order_created':
                self.queue_dispatch(d['order_id'], 0)
            elif kind == 'offer_resolved' and d['state'] != 'accepted':
                self.queue_dispatch(d['order_id'], self.platforms[person_id].config.parameters.retry_seconds)
            elif kind == 'order_assigned':
                order = e.orders[d['order_id']]
                self.revise(order)
                interval = self.platforms[person_id].config.parameters.revise_interval_seconds
                if interval > 0:
                    self.schedule(interval, 'policy.revise', order_id=order.id)
            elif kind == 'order_completed':
                order = e.orders[d['order_id']]
                completed = self.platform_memory[person_id].setdefault('completed', {})
                for role, pid in (('rider', order.rider_id), ('driver', order.assignment.driver_id)):
                    key = self.key(role, pid)
                    completed[key] = completed.get(key, 0) + 1
                for other_id in e.drivers[order.assignment.driver_id].commitments:
                    other = e.orders[other_id]
                    if other.platform_id == person_id:
                        self.revise(other)
            if kind in self.observing[person_id]:
                self.observe(person_id, kind, d)
        elif n.audience == 'rider':
            t, s = self.profile('rider', person_id).rider, self.state('rider', person_id)
            if kind == 'intent_started':
                self.intents[str(d['intent_id'])] = {}
                self.queue_rider(d['intent_id'], t.decision_seconds)
                self.schedule(t.patience_seconds, 'policy.rider.deadline', intent_id=d['intent_id'])
            elif kind == 'quote_received':
                self.queue_rider(d['intent_id'], t.decision_seconds)
            elif kind == 'order_created':
                self.schedule(t.cancellation_after_seconds, 'policy.rider.progress', order_id=d['order_id'])
            elif kind == 'order_canceled':
                order = e.orders[d['order_id']]
                self.add_reward('rider', person_id, order.platform_id, -1, 'cancellation', order.id)
                # Outcome memory (rider_search@2 fatigue_threshold): a rider's own cancellation is
                # not the platform failing (plan section 10, S7); gated so no @1 rider's state
                # dict gains a 'failures' key.
                if getattr(t, 'fatigue_threshold', 0) and d['by'] != 'rider':
                    failures = s.setdefault('failures', {})
                    failures[order.platform_id] = failures.get(order.platform_id, 0) + 1
                    self.observations.append({'type': 'service_failure', 'at_seconds': self.now,
                        'rider_id': person_id, 'platform_id': order.platform_id, 'cause': 'canceled',
                        'consecutive': failures[order.platform_id]})
                self.queue_rider(order.intent_id, t.retry_seconds)
            elif kind == 'order_completed':
                order = e.orders[d['order_id']]
                quote = e.quotes[order.quote_id]
                reward = rider_reward(t, freeze(order), freeze(quote))
                self.add_reward('rider', person_id, order.platform_id, reward, 'completed', order.id)
                if getattr(t, 'fatigue_threshold', 0):
                    failures = s.setdefault('failures', {})
                    wait = order.timeline['arrived'] - order.created_at
                    if wait > t.failure_wait_seconds:
                        failures[order.platform_id] = failures.get(order.platform_id, 0) + 1
                        self.observations.append({'type': 'service_failure', 'at_seconds': self.now,
                            'rider_id': person_id, 'platform_id': order.platform_id, 'cause': 'long_wait',
                            'consecutive': failures[order.platform_id]})
                    else:
                        failures[order.platform_id] = 0
            elif kind == 'pickup_eta_revised' and getattr(t, 'eta_drift_cancel_seconds', 0) > 0:
                # Zero-delay schedule keeps the decision at an event boundary rather than inside
                # the engine's own notification drain; a terminal order's progress is a no-op.
                self.schedule(0, 'policy.rider.progress', order_id=d['order_id'])
            elif kind == 'intent_ended':
                for p in sorted(e.riders[person_id].open_apps):
                    e.close_app('rider', person_id, p)
        elif n.audience == 'driver':
            t, s = self.profile('driver', person_id).driver, self.state('driver', person_id)
            self.sync_driver(person_id)
            if kind == 'shift_started':
                apps = self.usable_apps('driver', person_id)
                if getattr(t, 'open_apps_at_start', 'preferred') == 'all':
                    for p in apps:  # apps is already sorted (usable_apps)
                        e.open_app('driver', person_id, p)
                else:
                    preferred = s['preferred_app'] if s['preferred_app'] in apps else (apps[0] if apps else None)
                    if preferred:
                        e.open_app('driver', person_id, preferred)
                self.sync_driver(person_id)
                s['search'] = {'no_offer_since': self.now, 'visited': sorted(e.drivers[person_id].open_apps)}
                s['generation'] += 1
                self.queue_expansion(person_id)
            elif kind == 'offer_received':
                s['generation'] += 1
                s.setdefault('search', {})['no_offer_since'] = self.now
                self.queue_expansion(person_id)
                if s['phase'] in ('idle', 'busy'):
                    phases = s['offer_counts'].setdefault(d['platform_id'], {'idle': 0, 'busy': 0})
                    phases[s['phase']] += 1
                self.observations.append({'type': 'personal_offer', 'at_seconds': self.now,
                    'driver_id': person_id, 'platform_id': d['platform_id'], 'phase': s['phase'], 'offer_id': d['offer_id']})
                if s.get('post_dropoff_since') is not None:
                    self.observations.append({'type': 'post_dropoff_offer_wait', 'driver_id': person_id,
                        'start_seconds': s.pop('post_dropoff_since'), 'end_seconds': self.now, 'censored': False})
                self.schedule(t.response_seconds, 'policy.driver.respond', offer_id=d['offer_id'])
            elif kind == 'commitment_added':
                s['generation'] += 1
                self.queue_expansion(person_id)
                order = e.orders[d['order_id']]
                self.schedule(t.cancel_after_seconds, 'policy.driver.progress', order_id=order.id)
                if getattr(t, 'cancel_check_seconds', 0) > 0:
                    self.schedule(t.cancel_check_seconds, 'policy.driver.progress', order_id=order.id)
                for p in s['apps']:
                    if p != order.platform_id:
                        self.observations.append({'type': 'opportunity_wait_censored', 'at_seconds': self.now,
                            'driver_id': person_id, 'platform_id': p, 'phase': s['phase']})
            elif kind == 'service_started':
                order = e.orders[d['order_id']]
                self.observations.append({'type': 'commitment_wait', 'at_seconds': self.now,
                    'driver_id': person_id, 'order_id': order.id, 'seconds': self.now - order.assignment.accepted_at})
            elif kind == 'commitment_released':
                order = e.orders[d['order_id']]
                if d['outcome'] == 'completed':
                    service = e.services[order.service_id]
                    duration = service.ended_at - service.started_at
                    reward = driver_reward(t, d['driver_payout_minor'], duration)
                    self.add_reward('driver', person_id, order.platform_id, reward, 'completed', order.id)
                    # AST-210 zone learning: a completed ride's payout per km, by pickup zone. Gated
                    # on both self.zones and zone_learning_rate so no @1/zone_learning_rate=0
                    # driver's state dict ever gains a 'zone_outcomes' key.
                    if self.zones and getattr(self.profile('driver', person_id).evolution, 'zone_learning_rate', 0) > 0:
                        km = math.fsum(math.dist(leg.origin, leg.destination) for leg in service.legs)
                        s.setdefault('zone_outcomes', []).append(
                            {'zone': self.zone_of(order.pickup), 'payout_minor': d['driver_payout_minor'], 'km': km})
                    if not e.drivers[person_id].commitments:
                        s['post_dropoff_since'] = self.now
                        self.queue_idle(person_id, 0)
                    else:
                        self.observations.append({'type': 'back_to_back_ready', 'at_seconds': self.now, 'driver_id': person_id})
                else:
                    self.add_reward('driver', person_id, order.platform_id, -1, 'cancellation', order.id)
                if not e.drivers[person_id].commitments and t.after_service == 'preferred' and s['phase'] != 'off':
                    preferred = s['preferred_app']
                    for p in sorted(e.drivers[person_id].open_apps - {preferred}):
                        e.close_app('driver', person_id, p)
                    if preferred in self.usable_apps('driver', person_id):
                        e.open_app('driver', person_id, preferred)
                    self.sync_driver(person_id)
                    s['search'] = {'no_offer_since': self.now, 'visited': sorted(e.drivers[person_id].open_apps)}
                    s['generation'] += 1
                    self.queue_expansion(person_id)
            elif kind == 'exit_requested' and t.shift_exit == 'cancel_queued':
                for order_id in list(e.drivers[person_id].commitments)[1:]:
                    self.cancel(Cancel(order_id, 'driver', 'shift exit'))
            elif kind == 'shift_ended' and s.get('post_dropoff_since') is not None:
                self.observations.append({'type': 'post_dropoff_offer_wait', 'driver_id': person_id,
                    'start_seconds': s.pop('post_dropoff_since'), 'end_seconds': self.now, 'censored': True})
            elif kind == 'driver_deactivated':
                self.observations.append({'type': 'driver_deactivated', 'at_seconds': self.now,
                    'driver_id': person_id, 'reason': d['reason']})

    def add_reward(self, role, person_id, platform_id, reward, outcome, order_id):
        obs = {'platform_id': platform_id, 'reward': max(-2, min(2, reward)), 'outcome': outcome,
               'order_id': order_id, 'at_seconds': self.now}
        self.state(role, person_id)['observations'].append(obs)
        self.observations.append({'type': 'personal_reward', 'role': role, 'person_id': person_id, **obs})

    def schedule_controller(self, at_seconds, platform_id, *, interval_seconds=None, until_seconds=None):
        if platform_id not in self.platforms:
            raise ValueError('Unknown controller platform')
        if interval_seconds is None and until_seconds is not None:
            raise ValueError('until_seconds requires a controller interval')
        if interval_seconds is not None:
            finite_number(interval_seconds, 'interval_seconds', strictly_positive=True)
            finite_number(until_seconds, 'until_seconds', minimum=at_seconds)
        self.engine.scheduler.schedule_at(at_seconds, 'policy.controller',
            {'platform_id': platform_id, 'interval_seconds': interval_seconds, 'until_seconds': until_seconds})

    def _controller(self, event):
        self._intervene(event)
        p = event.payload['platform_id']
        policy = self.platforms[p]
        decision = policy.controller(freeze({'now': self.now, 'platform_id': p}),
            freeze(self.platform_memory[p]), RandomValues(self.seed, ('controller', p, self.now)))
        self.record('platform', p, 'controller', decision, policy.config.version)
        action = decision.action
        if isinstance(action, Transfer):
            self.apply_transfer(p, action)
        elif not isinstance(action, Stop):
            raise ValueError('The fixed controller does not support automatic policy updates')
        self.platform_memory[p] = decision.memory
        interval = event.payload['interval_seconds']
        if interval is not None and self.now + interval <= event.payload['until_seconds']:
            self.schedule(interval, 'policy.controller', **plain(event.payload))

    def schedule_checkpoint(self, at_seconds):
        self.engine.scheduler.schedule_at(at_seconds, 'policy.checkpoint', {})

    def schedule_intervention(self, at_seconds, *, platform_id=None, config=None, role=None, person_id=None,
                              preferred_app=None, launch=None, regulation=None, shutdown=None, delay=None):
        # Config is compiled before scheduling; execution only selects an immutable version.
        finite_number(at_seconds, 'intervention time', minimum=self.now)
        if launch is not None and launch not in self.platforms:
            raise ValueError('Unknown launch platform')
        if shutdown is not None and shutdown not in self.platforms:
            raise ValueError('Unknown shutdown platform')
        if platform_id is not None and platform_id not in self.platforms:
            raise ValueError('Unknown platform')
        if config is not None and (not isinstance(config, PlatformPolicy) or platform_id is None):
            raise ValueError('A policy update requires a compiled PlatformPolicy and platform ID')
        if preferred_app is not None and preferred_app not in self.accessible_apps(role, person_id):
            raise ValueError('Preferred intervention requires installed, account-enabled and registered access')
        item = {'at': at_seconds, 'platform_id': platform_id, 'config': plain(config), 'role': role,
                'person_id': person_id, 'preferred_app': preferred_app, 'launch': launch,
                'regulation': regulation, 'shutdown': shutdown, 'delay': delay, 'applied': False}
        self.interventions.append(item)
        self.engine.scheduler.schedule_at(at_seconds, 'policy.intervention', {})

    def _intervene(self, event):
        for item in self.interventions:
            if item['applied'] or item['at'] > self.now:
                continue
            if item['launch'] is not None:
                self.engine.launch_platform(item['launch'])
            if item.get('regulation') is not None:
                self.engine.impose_regulation(**item['regulation'])
            # .get, not [...]: a restored intervention list from a pre-phase-6 snapshot has
            # neither key, and a shutdown/delay intervention is simply not there to apply.
            if item.get('shutdown') is not None:
                self.engine.shutdown_platform(item['shutdown'])
            if item.get('delay') is not None:
                self.engine.impose_delay(**item['delay'])
            if item['config'] is not None:
                config = item['config']
                implementation = policy_class('marketplace', self.implementations['marketplace'][item['platform_id']])
                self.platforms[item['platform_id']] = implementation(PlatformPolicy.compile(
                    overrides=config['parameters'], rules=config['rules'], campaigns=config['campaigns'],
                    programs=config.get('programs', ()), version=config['version'], fallback=config['fallback']))
                self.observing[item['platform_id']] = self._observed_kinds(self.platforms[item['platform_id']])
            if item['preferred_app'] is not None:
                if item['preferred_app'] not in self.usable_apps(item['role'], item['person_id']):
                    raise ValueError('Intervention preference is no longer usable')
                target_state = self.state(item['role'], item['person_id'])
                target_state['preferred_app'] = item['preferred_app']
                # A preference_change intervention overrides fatigue stickiness (plan section 4.E:
                # "unless ... a preference_change intervention runs"); a no-op for an @1 person,
                # whose state dict never reads this key.
                target_state['sticky_preference'] = False
            item['applied'] = True
            self.observations.append({'type': 'intervention', 'at_seconds': self.now, **plain(item)})

    def neighbor_install_shares(self, role):
        """Share of same-role people within `vicinity_km` of each person who have each app
        installed -- participant knowledge (plan section 5.3), never disclosed to a platform.
        `{}` (no O(n^2) work) unless some person's `evolution.install_trigger_peer_share` is
        actually positive. A person with no known position (never began an intent / never
        started a shift) is excluded from both numerator and denominator; share is `{}` (read as
        0 for every app, personal_evolution@2) when there are no positioned neighbours."""
        table = self.engine.riders if role == 'rider' else self.engine.drivers
        if not any(getattr(self.profile(role, pid).evolution, 'install_trigger_peer_share', 0) > 0 for pid in table):
            return {}
        positions = {pid: pos for pid in sorted(table)
                    if (pos := self.engine.position_of(role, pid)) is not None}
        shares = {}
        for person_id in sorted(table):
            origin = positions.get(person_id)
            if origin is None:
                shares[person_id] = {}
                continue
            vicinity_km = getattr(self.profile(role, person_id).evolution, 'vicinity_km', 2)
            neighbors = [other for other, pos in positions.items()
                        if other != person_id and math.dist(pos, origin) <= vicinity_km]
            counts = {}
            for other in neighbors:
                for p in table[other].apps:
                    counts[p] = counts.get(p, 0) + 1
            shares[person_id] = {p: n / len(neighbors) for p, n in counts.items()} if neighbors else {}
        return shares

    def _checkpoint(self, event):
        self._intervene(event)
        for driver_id in self.engine.drivers:
            self.sync_driver(driver_id)
        proposals = []
        neighbor_shares = {role: self.neighbor_install_shares(role) for role in ('rider', 'driver')}
        for role, table in (('rider', self.engine.riders), ('driver', self.engine.drivers)):
            for person_id, person in table.items():
                s, profile = self.state(role, person_id), self.profile(role, person_id)
                if self.now <= s['last_checkpoint']:
                    continue
                context = freeze({'now': self.now, 'elapsed_days': (self.now - s['last_checkpoint']) / 86400,
                    'known_launched_apps': [p for p in profile.awareness if self.engine.platforms[p].launched],
                    'apps': person.apps, 'usable_apps': self.usable_apps(role, person_id),
                    'preferred_app': s['preferred_app'], 'scores': s['scores'], 'observations': s['observations'],
                    'exposure': s['exposure'], 'offer_counts': s['offer_counts'],
                    'phase': s['phase'] if role == 'driver' else 'off',
                    'sticky_preference': s.get('sticky_preference', False),
                    'neighbor_install_share': neighbor_shares[role].get(person_id, {}),
                    # AST-210 zone learning: unconditional, like every other context key -- an @1
                    # (or non-V2) evolution policy simply never reads these two, exactly as it never
                    # read neighbor_install_share before AST-209.
                    'zone_outcomes': s.get('zone_outcomes', []), 'zones': self.zone_boxes()})
                decision = self.bindings['evolution'](profile.evolution).checkpoint(context, freeze(s['evolution']),
                        RandomValues(self.seed, ('checkpoint', role, person_id, self.now)))
                proposals.append((role, person_id, decision))
        for role, person_id, decision in proposals:
            self.record(role, person_id, 'checkpoint', decision, self.bindings['evolution'].declaration.version)
            s, a = self.state(role, person_id), decision.action
            s['evolution'] = decision.memory
            s['scores'] = decision.memory['scores']
            s['preferred_app'] = a.preferred_app
            s['last_checkpoint'] = self.now
            s['observations'] = []
            if 'zone_outcomes' in s:  # only if the commitment_released branch ever wrote one
                s['zone_outcomes'] = []
            if a.download:
                self.engine.install_app(role, person_id, a.download)
                self.observations.append({'type': 'app_installed', 'at_seconds': self.now,
                                          'role': role, 'person_id': person_id, 'platform_id': a.download})
                self.engine.activate_account(role, person_id, a.download)
                self.observations.append({'type': 'account_activated', 'at_seconds': self.now,
                                          'role': role, 'person_id': person_id, 'platform_id': a.download})
                if role == 'driver' and a.register_car:
                    self.engine.register_car(self.engine.drivers[person_id].car_id, a.download)
                    self.observations.append({'type': 'car_registered', 'at_seconds': self.now,
                                              'driver_id': person_id, 'platform_id': a.download})
                if role == 'driver':
                    s['generation'] += 1
                    self.queue_expansion(person_id)

    # ----------------------------------------------------------------------
    # evolution.ledger: lease/bankruptcy postings and platform dividends (AST-210). All
    # configuration travels in the event payload -- nothing enters PolicyRuntime.snapshot(), so a
    # restored run continues the whole schedule from the scheduler snapshot alone (the
    # _controller/schedule_program_windows self-rescheduling precedent).
    # ----------------------------------------------------------------------

    def schedule_ledger(self, *, at_seconds, amount_minor, interval_seconds, bankruptcy_minor):
        self.engine.scheduler.schedule_at(at_seconds, 'policy.ledger.lease',
            {'amount_minor': amount_minor, 'interval_seconds': interval_seconds, 'bankruptcy_minor': bankruptcy_minor})

    def _ledger_lease(self, event):
        amount_minor = event.payload['amount_minor']
        interval_seconds, bankruptcy_minor = event.payload['interval_seconds'], event.payload['bankruptcy_minor']
        # The _observe_window ordering idiom: id-sorted by (type name, str(id)) so a mixed-type
        # driver id table orders identically continuous and restored.
        for driver_id in sorted(self.engine.drivers, key=lambda d: (type(d).__name__, str(d))):
            driver = self.engine.drivers[driver_id]
            if driver.deactivated_at is not None:
                continue
            if amount_minor > 0:
                self.engine.post_transfer('lease', 'driver', driver_id, -amount_minor, counterparty='external')
            if (bankruptcy_minor is not None
                    and self.engine.account('driver', driver_id).balance_minor < bankruptcy_minor):
                self.engine.deactivate_driver(driver_id, 'bankruptcy')
        # Reschedules unconditionally, like _observe_window: a trailing pending event past the
        # horizon is identical in a continuous run and a restored one.
        self.engine.scheduler.schedule_after(interval_seconds, 'policy.ledger.lease',
            {'amount_minor': amount_minor, 'interval_seconds': interval_seconds, 'bankruptcy_minor': bankruptcy_minor})

    def schedule_dividend(self, *, at_seconds, platform_id, reserve_minor, min_completed_rides, period_seconds,
                          period_start_seconds):
        self.engine.scheduler.schedule_at(at_seconds, 'policy.ledger.dividend',
            {'platform_id': platform_id, 'reserve_minor': reserve_minor, 'min_completed_rides': min_completed_rides,
             'period_seconds': period_seconds, 'period_start_seconds': period_start_seconds})

    def _ledger_dividend(self, event):
        pid = event.payload['platform_id']
        reserve_minor, min_completed_rides = event.payload['reserve_minor'], event.payload['min_completed_rides']
        period_seconds, period_start = event.payload['period_seconds'], event.payload['period_start_seconds']
        counts = {}
        for settlement in self.engine.settlements.values():
            if (settlement.platform_id == pid and settlement.reason == 'completed_ride'
                    and period_start <= settlement.at < self.now):
                counts[settlement.driver_id] = counts.get(settlement.driver_id, 0) + 1
        qualifying = sorted(driver_id for driver_id, count in counts.items() if count >= min_completed_rides)
        balance = self.engine.account('platform', pid).balance_minor
        excess = None if balance is None else balance - reserve_minor
        if excess is not None and excess > 0 and qualifying:
            share = excess // len(qualifying)
            if share > 0:  # the integer remainder always stays with the platform
                for driver_id in qualifying:
                    self.engine.post_transfer('dividend', 'driver', driver_id, share, platform_id=pid)
                self.observations.append({'type': 'dividend_paid', 'at_seconds': self.now, 'platform_id': pid,
                                          'qualifying_drivers': len(qualifying), 'share_minor': share,
                                          'excess_minor': excess})
        self.engine.scheduler.schedule_after(period_seconds, 'policy.ledger.dividend',
            {'platform_id': pid, 'reserve_minor': reserve_minor, 'min_completed_rides': min_completed_rides,
             'period_seconds': period_seconds, 'period_start_seconds': self.now})

    def snapshot(self):
        return plain({'schema_version': self.schema_version, 'platform_memory': self.platform_memory,
            'people': self.people, 'intents': self.intents, 'dispatch_generation': self.dispatch_generation,
            'decisions': self.decisions, 'observations': self.observations, 'interventions': self.interventions,
            'implementations': self.implementations,
            'platforms': {p: policy.config for p, policy in self.platforms.items()}})

    def restore_memory(self, snapshot):
        if snapshot['schema_version'] != self.schema_version:
            raise ValueError('Unsupported policy memory schema')
        for key in ('platform_memory', 'people', 'intents', 'dispatch_generation', 'decisions',
                    'observations', 'interventions'):
            setattr(self, key, snapshot[key])
