"""Personal decisions, traits and learning, separate from commercial policy.

The complete behavior contract and model definitions live in
plans/architecture/behavior-policy.md. No policy receives an engine or scheduler.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from policy_contracts import (Cancel, Declaration, Decision, Stop, Wait,
                              finite_number, positive_int, plain)


def logistic(score):
    if score >= 0:
        return 1 / (1 + math.exp(-score))
    odds = math.exp(score)
    return odds / (1 + odds)


@dataclass(frozen=True)
class RiderTraits:
    acceptable_price_ratio: float = 1.2
    eta_tolerance_seconds: float = 300
    price_sensitivity: float = 1
    eta_sensitivity: float = .5
    loyalty: float = .3
    search_cost: float = .1
    outside_utility: float = 0
    purchase_bias: float = 1
    taste_scale: float = .25
    decision_seconds: float = 5
    opening_seconds: float = 2
    retry_seconds: float = 2
    patience_seconds: float = 300
    cancellation_after_seconds: float = 600
    max_app_visits: int = 3
    max_quote_refreshes: int = 1
    max_order_attempts: int = 3
    attempts_per_app: int = 1
    reference_base_minor: int = 200
    reference_per_km_minor: int = 150

    def __post_init__(self):
        for name, value in plain(self).items():
            if name in ('outside_utility', 'purchase_bias'):
                finite_number(value, name, minimum=-1e6)
            elif name.startswith('max_') or name == 'attempts_per_app':
                positive_int(value, name)
            else:
                finite_number(value, name, strictly_positive=(name.endswith('_seconds')
                              or name == 'acceptable_price_ratio' or name == 'reference_base_minor'))


@dataclass(frozen=True)
class DriverTraits:
    response_seconds: float = 3
    no_offer_seconds: float = 60
    further_opening_seconds: float = 30
    expansion: str = 'multi_app'
    after_service: str = 'retain'
    expand_while_busy: bool = False
    second_order_probability: float = 1
    acceptance_bias: float = 1
    payout_sensitivity: float = 1
    delay_sensitivity: float = .5
    reference_payout_minor: float = 1000
    reference_delay_seconds: float = 300
    loyalty: float = .2
    max_private_pickup_seconds: float = 3600
    cancel_after_seconds: float = 7200
    shift_exit: str = 'drain'

    def __post_init__(self):
        if self.expansion not in ('multi_app', 'exclusive_switch'):
            raise ValueError('Unknown app expansion policy')
        if self.after_service not in ('retain', 'preferred'):
            raise ValueError('Unknown post-service app policy')
        if self.shift_exit not in ('drain', 'cancel_queued'):
            raise ValueError('Unknown shift exit policy')
        if not isinstance(self.expand_while_busy, bool):
            raise ValueError('expand_while_busy must be boolean')
        for name in ('response_seconds', 'no_offer_seconds', 'further_opening_seconds',
                     'reference_payout_minor', 'reference_delay_seconds', 'max_private_pickup_seconds',
                     'cancel_after_seconds'):
            finite_number(getattr(self, name), name, strictly_positive=True)
        for name in ('payout_sensitivity', 'delay_sensitivity', 'loyalty'):
            finite_number(getattr(self, name), name)
        finite_number(self.acceptance_bias, 'acceptance_bias', minimum=-1e6)
        finite_number(self.second_order_probability, 'second_order_probability', maximum=1)


@dataclass(frozen=True)
class EvolutionTraits:
    adoption_rate_per_day: float = 0
    adoption_friction: float = 0
    learning_rate: float = 0
    preference_margin: float = .1
    preference_cooldown_seconds: float = 86400
    prior_offer_rate_per_second: float = 1 / 300
    prior_exposure_seconds: float = 300
    onboard_car: bool = False

    def __post_init__(self):
        for name, value in plain(self).items():
            if name != 'onboard_car':
                finite_number(value, name, strictly_positive=name.startswith('prior_'))
        finite_number(self.learning_rate, 'learning_rate', maximum=1)
        if not isinstance(self.onboard_car, bool):
            raise ValueError('onboard_car must be boolean')


@dataclass(frozen=True)
class PersonProfile:
    """Stable traits/access initialization, independent of learned and episode state."""
    apps: tuple = ('rebu', 'blot', 'flyt')
    preferred_app: str = 'rebu'
    accounts: tuple | None = None
    registrations: tuple | None = None
    awareness: tuple = ('rebu', 'blot', 'flyt')
    segment: str = 'default'
    disclosed_to: tuple = ()
    rider: RiderTraits = field(default_factory=RiderTraits)
    driver: DriverTraits = field(default_factory=DriverTraits)
    evolution: EvolutionTraits = field(default_factory=EvolutionTraits)

    def __post_init__(self):
        if not (isinstance(self.rider, RiderTraits) and isinstance(self.driver, DriverTraits)
                and isinstance(self.evolution, EvolutionTraits)):
            raise ValueError('Profile traits must be typed RiderTraits, DriverTraits and EvolutionTraits')
        for name in ('apps', 'awareness', 'disclosed_to'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, 'accounts', tuple(self.apps if self.accounts is None else self.accounts))
        object.__setattr__(self, 'registrations', tuple(self.accounts if self.registrations is None else self.registrations))
        if not self.apps or len(set(self.apps)) != len(self.apps):
            raise ValueError('Access must be a nonempty app subset without duplicates')
        if not set(self.accounts) <= set(self.apps) or self.preferred_app not in self.accounts:
            raise ValueError('Preferred app must have an installed app and usable account')


@dataclass(frozen=True)
class OpenApp:
    platform_id: str
    refresh: bool = False


@dataclass(frozen=True)
class OrderQuote:
    quote_id: int


@dataclass(frozen=True)
class Respond:
    offer_id: int
    accept: bool
    probability: float
    displayed_eta_seconds: float | None
    private_eta_seconds: float


@dataclass(frozen=True)
class ExpandApps:
    open_app: str
    close_apps: tuple = ()


@dataclass(frozen=True)
class Evolve:
    download: str | None
    preferred_app: str
    register_car: bool


def ranked_apps(apps, preferred, scores, incentives):
    return sorted(apps, key=lambda p: (-(scores.get(p, 0) + incentives.get(p, 0)
                                       + (1 if p == preferred else 0)), p))


class RiderPolicy:
    declaration = Declaration('rider_search', '1', RiderTraits,
        ('now', 'intent', 'quotes', 'usable_apps', 'preferred_app', 'scores', 'announced'),
        (OpenApp, OrderQuote, Cancel, Stop, Wait), 1, ('decide', 'progress'),
        (('visited', 'array'), ('attempts', 'object'), ('refreshes', 'integer'),
         ('outside_draw', 'number'), ('taste', 'object'), ('inspection_draw', 'number'), ('generation', 'integer')))

    def __init__(self, traits):
        self.traits = traits

    def decide(self, context, memory, random):
        t, m = self.traits, plain(memory)
        m.setdefault('visited', [])
        m.setdefault('attempts', {})
        m.setdefault('refreshes', 0)
        # Keyed to the intent, not the quote or decision number. Persist for diagnostics/resume.
        m.setdefault('outside_draw', random.uniform('outside'))
        m.setdefault('taste', {p: random.uniform(('taste', p)) for p in context.usable_apps})
        m.setdefault('inspection_draw', random.uniform('inspection'))
        if context.now >= context.intent.created_at + t.patience_seconds:
            return Decision(Stop('search patience exhausted'), m, 'Intent search deadline reached')
        if sum(m['attempts'].values()) >= t.max_order_attempts:
            return Decision(Stop('order budget exhausted'), m, 'Finite order attempts exhausted')
        available = [p for p in context.usable_apps if m['attempts'].get(p, 0) < t.attempts_per_app]
        unseen = [p for p in available if p not in m['visited']]
        unseen = ranked_apps(unseen, context.preferred_app, context.scores, context.announced)
        if not m['visited'] and unseen:
            first = context.preferred_app if context.preferred_app in unseen else unseen[0]
            m['visited'].append(first)
            return Decision(OpenApp(first), m, 'Start on preferred usable app')
        reference = t.reference_base_minor + t.reference_per_km_minor * math.dist(context.intent.origin, context.intent.destination)
        quotes = [q for q in context.quotes if q.expires_at > context.now and q.platform_id in available]
        viable = [q for q in quotes if q.eta_seconds is not None]
        latest = max(quotes, key=lambda q: q.at, default=None)
        price_excess = max(0, ((latest.fare.gross_minor - latest.fare.discount_minor) / reference
                              - t.acceptable_price_ratio)) if latest else 0
        eta_excess = max(0, latest.eta_seconds / t.eta_tolerance_seconds - 1) if latest and latest.eta_seconds is not None else 0
        inspect_probability = -math.expm1(-t.price_sensitivity * price_excess - t.eta_sensitivity * eta_excess)
        inspect = not viable or m['inspection_draw'] < inspect_probability
        if inspect and unseen and len(m['visited']) < t.max_app_visits:
            m['visited'].append(unseen[0])
            return Decision(OpenApp(unseen[0]), m, 'Inspect an unseen app after price, ETA or supply dissatisfaction')

        def utility(q):
            p = q.platform_id
            taste = m['taste'].setdefault(p, random.uniform(('taste', p)))
            return (t.purchase_bias - t.price_sensitivity * ((q.fare.gross_minor - q.fare.discount_minor) / reference)
                    - t.eta_sensitivity * q.eta_seconds / t.eta_tolerance_seconds
                    + t.loyalty * (p == context.preferred_app) + context.scores.get(p, 0)
                    - t.search_cost * m['visited'].index(p)
                    + t.taste_scale * math.log(taste / (1 - taste)))

        best = min(viable, key=lambda q: (-utility(q), q.platform_id, -q.at, -q.id), default=None)
        outside = t.outside_utility + t.taste_scale * math.log(m['outside_draw'] / (1 - m['outside_draw']))
        if best is not None and utility(best) >= outside:
            p = best.platform_id
            m['attempts'][p] = m['attempts'].get(p, 0) + 1
            return Decision(OrderQuote(best.id), m, 'Best observed valid quote exceeds the fixed outside option')
        if not quotes and m['refreshes'] < t.max_quote_refreshes:
            refreshable = [p for p in m['visited'] if p in available]
            if refreshable:
                m['refreshes'] += 1
                return Decision(OpenApp(refreshable[-1], refresh=True), m, 'Refresh an expired quote within budget')
        return Decision(Stop('outside option chosen'), m, 'No observed quote justifies purchase')

    def progress(self, context, memory, random):
        order = context.order
        if 'boarded' not in order.timeline and context.now - order.created_at >= self.traits.cancellation_after_seconds:
            return Decision(Cancel(order.id, 'rider', 'pickup patience exhausted'), plain(memory), 'Request cancellation before retry')
        return Decision(Stop('continue current ride'), plain(memory), 'Keep the existing commitment')


class DriverPolicy:
    declaration = Declaration('driver_participation', '1', DriverTraits,
        ('now', 'position', 'commitments', 'offer', 'private_eta_seconds', 'free_slots',
         'open_apps', 'usable_apps', 'preferred_app', 'scores', 'announced', 'exit_requested'),
        (ExpandApps, Respond, Cancel, Stop, Wait), 1, ('expand', 'respond', 'progress'),
        (('no_offer_since', 'number'), ('visited', 'array'), ('expanded_at', 'number'), ('last_result', 'string')))

    def __init__(self, traits):
        self.traits = traits

    def expand(self, context, memory, random):
        t, m = self.traits, plain(memory)
        if context.exit_requested or context.free_slots == 0 or (context.commitments and not t.expand_while_busy):
            return Decision(Stop('not seeking additional offers'), m, 'Search suspended')
        unseen = set(context.usable_apps) - set(context.open_apps) - set(m.get('visited', []))
        apps = ranked_apps(unseen, context.preferred_app, context.scores, context.announced)
        if not apps:
            return Decision(Stop('all usable apps visited'), m, 'No expansion loop remains')
        threshold = t.no_offer_seconds if not m.get('expanded_at') else t.further_opening_seconds
        due = max(m['no_offer_since'] + t.no_offer_seconds,
                  m.get('expanded_at', m['no_offer_since']) + threshold)
        if context.now < due:
            return Decision(Wait(due - context.now), m, 'Await the personal no-offer threshold')
        required = {c.platform_id for c in context.commitments}
        close = tuple(sorted(set(context.open_apps) - required)) if t.expansion == 'exclusive_switch' else ()
        m.setdefault('visited', []).append(apps[0])
        m['expanded_at'] = context.now
        return Decision(ExpandApps(apps[0], close), m, 'Open an alternative without resetting no-offer time')

    def respond(self, context, memory, random):
        t = self.traits
        payout = context.offer.payout.payout_minor + context.offer.payout.bonus_minor
        score = (t.acceptance_bias + t.payout_sensitivity * (payout / t.reference_payout_minor - 1)
                 - t.delay_sensitivity * context.private_eta_seconds / t.reference_delay_seconds
                 + t.loyalty * (context.offer.platform_id == context.preferred_app)
                 + context.scores.get(context.offer.platform_id, 0))
        probability = logistic(score)
        if context.commitments:
            probability *= t.second_order_probability
        if context.free_slots == 0 or context.exit_requested or context.private_eta_seconds > t.max_private_pickup_seconds:
            probability = 0
        accept = random.uniform('accept') < probability
        return Decision(Respond(context.offer.id, accept, probability, context.offer.eta_seconds,
                                context.private_eta_seconds), plain(memory), 'Evaluate payout and private remaining workload')

    def progress(self, context, memory, random):
        order = context.order
        if 'boarded' not in order.timeline and context.now - order.assignment.accepted_at >= self.traits.cancel_after_seconds:
            return Decision(Cancel(order.id, 'driver', 'commitment patience exhausted'), plain(memory), 'Request cancellation of overdue work')
        return Decision(Stop('keep commitment'), plain(memory), 'Continue serving accepted work')


class EvolutionPolicy:
    declaration = Declaration('personal_evolution', '1', EvolutionTraits,
        ('now', 'elapsed_days', 'known_launched_apps', 'apps', 'usable_apps', 'preferred_app',
         'observations', 'exposure', 'offer_counts', 'scores'), (Evolve,), 1, ('checkpoint',),
        (('estimated_wait_seconds', 'object'), ('switched_at', 'number'),
         ('scores', 'object'), ('service_scores', 'object')))

    def __init__(self, traits):
        self.traits = traits

    def checkpoint(self, context, memory, random):
        t, m = self.traits, plain(memory)
        scores = dict(m.get('service_scores', context.scores) if t.learning_rate else context.scores)
        # Process only new personal outcomes. No additional promotional reward.
        for obs in (context.observations if t.learning_rate else ()):
            p = obs.platform_id
            scores[p] = max(-2, min(2, scores.get(p, 0) + t.learning_rate * (obs.reward - scores.get(p, 0))))
        m['service_scores'] = dict(scores)
        opportunity = {}
        for p, phases in context.exposure.items():
            opportunity[p] = {}
            for phase, seconds in phases.items():
                offers = context.offer_counts.get(p, {}).get(phase, 0)
                rate = ((offers + t.prior_offer_rate_per_second * t.prior_exposure_seconds)
                        / (seconds + t.prior_exposure_seconds))
                opportunity[p][phase] = 1 / rate
        m['estimated_wait_seconds'] = opportunity
        # Phase-specific exposure weights never add queued commitment waits to working hours.
        learned = dict(scores)
        if t.learning_rate:
            for p, phases in opportunity.items():
                total = sum(context.exposure[p].values())
                if total:
                    wait = sum(phases[phase] * seconds for phase, seconds in context.exposure[p].items()) / total
                    learned[p] = max(-2, min(2, scores.get(p, 0) - t.learning_rate * min(1, wait / 600)))
        preferred = context.preferred_app
        if t.learning_rate and context.usable_apps and context.now - m.get('switched_at', -1e30) >= t.preference_cooldown_seconds:
            candidate = min(context.usable_apps, key=lambda p: (-learned.get(p, 0), p))
            if learned.get(candidate, 0) > learned.get(preferred, 0) + t.preference_margin:
                preferred = candidate
                m['switched_at'] = context.now
        m['scores'] = learned
        rates = {p: t.adoption_rate_per_day * math.exp(-t.adoption_friction)
                 * (1 + max(0, -scores.get(context.preferred_app, 0)))
                 for p in context.known_launched_apps if p not in context.apps}
        total = sum(rates.values())
        download = None
        if total and random.uniform('download') < -math.expm1(-total * context.elapsed_days):
            target = random.uniform('target') * total
            for p, rate in sorted(rates.items()):
                target -= rate
                if target < 0:
                    download = p
                    break
        return Decision(Evolve(download, preferred, t.onboard_car), m,
                        'Snapshot-based learning and at most one combined-hazard download')


def personal_pickup_eta(now, position, orders, speed_kmh, boarding_seconds):
    """Personal workload estimator; all orders belong to this driver, across apps."""
    remaining = 0
    for order in orders:
        if 'boarded' not in order.timeline:
            remaining += math.dist(position, order.pickup) * 3600 / speed_kmh
            remaining += (max(0, boarding_seconds - (now - order.timeline['arrived']))
                          if 'arrived' in order.timeline else boarding_seconds)
            position = order.pickup
        remaining += math.dist(position, order.destination) * 3600 / speed_kmh
        position = order.destination
    return remaining, position


def rider_reward(traits, order, quote):
    reference = traits.reference_base_minor + traits.reference_per_km_minor * math.dist(order.pickup, order.destination)
    wait = order.timeline['arrived'] - order.created_at
    error = abs(wait - (quote.eta_seconds or 0)) / traits.eta_tolerance_seconds
    return (1 - (order.fare.gross_minor - order.fare.discount_minor) / reference
            - .25 * wait / traits.eta_tolerance_seconds - .25 * error)


def driver_reward(traits, payout_minor, physical_service_seconds):
    return payout_minor / max(1, physical_service_seconds) / (traits.reference_payout_minor / 600) - 1
