"""Policy/engine adapter: scope observations, schedule proposals, validate and apply.

Only this adapter can see both the engine and policies. Policy implementations
receive detached immutable snapshots, explicit memory, and keyed random values.
"""
import math
from dataclasses import asdict

from behavior_policy import (DriverPolicy, EvolutionPolicy, ExpandApps, OpenApp,
                             OrderQuote, Respond, RiderPolicy, personal_pickup_eta, rider_reward, driver_reward)
from marketplace_policy import (EtaProposal, MarketplacePolicy, OfferProposal, PlatformPolicy,
                                QuoteProposal)
from marketplace_engine import CommandRejected
from policy_contracts import Cancel, Decision, RandomValues, Stop, Transfer, Wait, finite_number, freeze, plain


# Policy implementations are selected by identity and version, never by an
# anonymous callback embedded in scenario data. A replacement must consume the
# family's parameter schema (or a subclass) because profiles and platform
# configurations are typed values, and must support the family's hooks.
POLICY_FAMILIES = {
    'rider': (RiderPolicy, ('decide', 'progress')),
    'driver': (DriverPolicy, ('expand', 'respond', 'progress')),
    'evolution': (EvolutionPolicy, ('checkpoint',)),
    'marketplace': (MarketplacePolicy, ('quote', 'dispatch', 'revise', 'cancel', 'controller')),
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


class PolicyRuntime:
    schema_version = 2

    def __init__(self, engine, registry, seed, platforms, profiles, implementations=None):
        self.engine, self.seed = engine, seed
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
        for kind, handler in (
            ('policy.rider.decide', self._rider_decide), ('policy.rider.open', self._rider_open),
            ('policy.rider.progress', self._rider_progress), ('policy.rider.deadline', self._rider_deadline),
            ('policy.dispatch', self._dispatch),
            ('policy.driver.expand', self._driver_expand), ('policy.driver.respond', self._driver_respond),
            ('policy.driver.progress', self._driver_progress), ('policy.checkpoint', self._checkpoint),
            ('policy.intervention', self._intervene), ('policy.controller', self._controller),
        ):
            registry.register(kind, handler)
        engine.add_listener(self.on_notification)

    @property
    def now(self):
        return self.engine.now

    @staticmethod
    def key(role, person_id):
        return f'{role}:{person_id}'

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
        return freeze({'now': self.now, 'platform_id': platform_id, 'request': request, 'order': order,
                       'drivers': drivers, 'own_orders': orders, 'own_offers': offers,
                       'visible': self.visible(platform_id, 'rider', rider_id), **extra})

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
            result[p] = max((c.discount_minor / 1000 + c.discount_fraction if role == 'rider'
                             else c.bonus_minor / 1000 for c in campaigns), default=0)
        return result

    def rider_context(self, intent):
        s = self.state('rider', intent.rider_id)
        return freeze({'now': self.now, 'intent': intent,
                       'quotes': self.engine.rider_view(intent.rider_id).quotes(),
                       'usable_apps': self.usable_apps('rider', intent.rider_id),
                       'preferred_app': s['preferred_app'], 'scores': s['scores'],
                       'announced': self.announced('rider', intent.rider_id)})

    def driver_context(self, driver_id, **extra):
        view, s = self.engine.driver_view(driver_id), self.state('driver', driver_id)
        return freeze({'now': self.now, 'position': view.position, 'commitments': view.commitments(),
                       'free_slots': view.free_slots, 'exit_requested': view.exit_requested,
                       'open_apps': view.open_apps, 'usable_apps': self.usable_apps('driver', driver_id),
                       'preferred_app': s['preferred_app'], 'scores': s['scores'],
                       'announced': self.announced('driver', driver_id), **extra})

    def quote(self, platform_id, request):
        policy = self.platforms[platform_id]
        context = self.platform_context(platform_id, request=request)
        decision = policy.quote(context, freeze(self.platform_memory[platform_id]),
                                RandomValues(self.seed, ('quote', platform_id, request.intent_id)))
        self.record('platform', platform_id, 'quote', decision, policy.config.version)
        if not isinstance(decision.action, QuoteProposal):
            raise ValueError('Quote hook must produce QuoteProposal')
        self.platform_memory[platform_id] = decision.memory
        self.engine.issue_quote(platform_id, request.intent_id, **asdict(decision.action))

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

    def queue_expansion(self, driver_id, delay=None):
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
        context = self.driver_context(driver_id, offer=offer, private_eta_seconds=self.private_eta(driver_id, order.pickup))
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
        s = self.state('driver', driver_id)
        decision = self.bindings['driver'](self.profile('driver', driver_id).driver).progress(
            freeze({'now': self.now, 'order': order}), freeze(s.get('progress', {})),
            RandomValues(self.seed, ('driver_progress', driver_id, order.id)))
        self.record('driver', driver_id, 'progress', decision)
        s['progress'] = decision.memory
        if isinstance(decision.action, Cancel):
            self.cancel(decision.action)

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
                self.revise(e.orders[d['order_id']])
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
        elif n.audience == 'rider':
            t = self.profile('rider', person_id).rider
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
                self.queue_rider(order.intent_id, t.retry_seconds)
            elif kind == 'order_completed':
                order = e.orders[d['order_id']]
                quote = e.quotes[order.quote_id]
                reward = rider_reward(t, freeze(order), freeze(quote))
                self.add_reward('rider', person_id, order.platform_id, reward, 'completed', order.id)
            elif kind == 'intent_ended':
                for p in sorted(e.riders[person_id].open_apps):
                    e.close_app('rider', person_id, p)
        elif n.audience == 'driver':
            t, s = self.profile('driver', person_id).driver, self.state('driver', person_id)
            self.sync_driver(person_id)
            if kind == 'shift_started':
                apps = self.usable_apps('driver', person_id)
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
                    if not e.drivers[person_id].commitments:
                        s['post_dropoff_since'] = self.now
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
                              preferred_app=None, launch=None):
        # Config is compiled before scheduling; execution only selects an immutable version.
        finite_number(at_seconds, 'intervention time', minimum=self.now)
        if launch is not None and launch not in self.platforms:
            raise ValueError('Unknown launch platform')
        if platform_id is not None and platform_id not in self.platforms:
            raise ValueError('Unknown platform')
        if config is not None and (not isinstance(config, PlatformPolicy) or platform_id is None):
            raise ValueError('A policy update requires a compiled PlatformPolicy and platform ID')
        if preferred_app is not None and preferred_app not in self.accessible_apps(role, person_id):
            raise ValueError('Preferred intervention requires installed, account-enabled and registered access')
        item = {'at': at_seconds, 'platform_id': platform_id, 'config': plain(config), 'role': role,
                'person_id': person_id, 'preferred_app': preferred_app, 'launch': launch, 'applied': False}
        self.interventions.append(item)
        self.engine.scheduler.schedule_at(at_seconds, 'policy.intervention', {})

    def _intervene(self, event):
        for item in self.interventions:
            if item['applied'] or item['at'] > self.now:
                continue
            if item['launch'] is not None:
                self.engine.launch_platform(item['launch'])
            if item['config'] is not None:
                config = item['config']
                implementation = policy_class('marketplace', self.implementations['marketplace'][item['platform_id']])
                self.platforms[item['platform_id']] = implementation(PlatformPolicy.compile(
                    overrides=config['parameters'], rules=config['rules'], campaigns=config['campaigns'],
                    version=config['version'], fallback=config['fallback']))
            if item['preferred_app'] is not None:
                if item['preferred_app'] not in self.usable_apps(item['role'], item['person_id']):
                    raise ValueError('Intervention preference is no longer usable')
                self.state(item['role'], item['person_id'])['preferred_app'] = item['preferred_app']
            item['applied'] = True
            self.observations.append({'type': 'intervention', 'at_seconds': self.now, **plain(item)})

    def _checkpoint(self, event):
        self._intervene(event)
        for driver_id in self.engine.drivers:
            self.sync_driver(driver_id)
        proposals = []
        for role, table in (('rider', self.engine.riders), ('driver', self.engine.drivers)):
            for person_id, person in table.items():
                s, profile = self.state(role, person_id), self.profile(role, person_id)
                if self.now <= s['last_checkpoint']:
                    continue
                context = freeze({'now': self.now, 'elapsed_days': (self.now - s['last_checkpoint']) / 86400,
                    'known_launched_apps': [p for p in profile.awareness if self.engine.platforms[p].launched],
                    'apps': person.apps, 'usable_apps': self.usable_apps(role, person_id),
                    'preferred_app': s['preferred_app'], 'scores': s['scores'], 'observations': s['observations'],
                    'exposure': s['exposure'], 'offer_counts': s['offer_counts']})
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
