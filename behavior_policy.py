"""Personal decisions, traits and learning, separate from commercial policy.

The complete behavior contract and model definitions live in
plans/architecture/behavior-policy.md. No policy receives an engine or scheduler.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

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
        # Iterate this class's own declared fields, not plain(self): a V2 subclass (behavior-policy.md
        # "Participant policies v2") adds string/bool/tuple traits that must never reach finite_number.
        for f in fields(RiderTraits):
            name, value = f.name, getattr(self, f.name)
            if name in ('outside_utility', 'purchase_bias'):
                finite_number(value, name, minimum=-1e6)
            elif name.startswith('max_') or name == 'attempts_per_app':
                positive_int(value, name)
            else:
                finite_number(value, name, strictly_positive=(name.endswith('_seconds')
                              or name == 'acceptable_price_ratio' or name == 'reference_base_minor'))


@dataclass(frozen=True)
class RiderTraitsV2(RiderTraits):
    """Additive `@2` traits (behavior-policy.md "Participant policies v2"). Every default reproduces
    `@1`: choice_rule='utility' keeps the inherited purchase comparison, and every other new field is
    0/False/'stable'/'best_other'/'utility' (off)."""
    choice_rule: str = 'utility'
    choice_keys: tuple = (('price', 50), ('eta', 0))
    compare_all_apps: bool = False
    tie_break: str = 'stable'
    failure_wait_seconds: float = 720
    fatigue_threshold: int = 0
    fatigue_target: str = 'best_other'
    sticky: bool = False
    eta_drift_cancel_seconds: float = 0
    install_trigger_eta_seconds: float = 0

    def __post_init__(self):
        super().__post_init__()
        if self.choice_rule not in ('utility', 'lexicographic'):
            raise ValueError('Unknown rider choice rule')
        object.__setattr__(self, 'choice_keys', tuple(tuple(pair) for pair in self.choice_keys))
        keys = [key for key, _ in self.choice_keys]
        if not keys or len(set(keys)) != len(keys) or any(key not in ('price', 'eta') for key in keys):
            raise ValueError('choice_keys must be a nonempty sequence of unique (price|eta, tolerance) pairs')
        for _, tolerance in self.choice_keys:
            finite_number(tolerance, 'choice tolerance')
        if not isinstance(self.compare_all_apps, bool):
            raise ValueError('compare_all_apps must be boolean')
        if self.tie_break not in ('stable', 'random'):
            raise ValueError('Unknown rider tie-break')
        finite_number(self.failure_wait_seconds, 'failure_wait_seconds', strictly_positive=True)
        if (isinstance(self.fatigue_threshold, bool) or not isinstance(self.fatigue_threshold, int)
                or self.fatigue_threshold < 0):
            raise ValueError('fatigue_threshold must be a nonnegative integer')
        if not isinstance(self.fatigue_target, str) or not self.fatigue_target:
            raise ValueError('fatigue_target must be a nonempty string')
        if not isinstance(self.sticky, bool):
            raise ValueError('sticky must be boolean')
        finite_number(self.eta_drift_cancel_seconds, 'eta_drift_cancel_seconds')
        finite_number(self.install_trigger_eta_seconds, 'install_trigger_eta_seconds')


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
class DriverTraitsV2(DriverTraits):
    """Additive `@2` traits. Every default reproduces `@1`: `response_rule='independent'` and
    `cancel_rule='patience'` keep the inherited logistic/patience-only behavior; `availability=
    'always_open'` never pauses an app."""
    open_apps_at_start: str = 'preferred'
    response_rule: str = 'independent'
    response_objective: str = 'total_payout'
    hold_for_better: bool = False
    reservation_payout_minor: int = 0
    hold_max_wait_seconds: float = 0
    cancel_rule: str = 'patience'
    cancel_eta_threshold_seconds: float = 3600
    cancel_check_seconds: float = 0
    penalty_tolerance_minor: int = 0
    availability: str = 'always_open'
    tie_break: str = 'stable'

    def __post_init__(self):
        super().__post_init__()
        if self.open_apps_at_start not in ('preferred', 'all'):
            raise ValueError('Unknown open_apps_at_start policy')
        if self.response_rule not in ('independent', 'best_pending'):
            raise ValueError('Unknown driver response rule')
        if self.response_objective not in ('total_payout', 'payout_per_minute'):
            raise ValueError('Unknown driver response objective')
        if not isinstance(self.hold_for_better, bool):
            raise ValueError('hold_for_better must be boolean')
        finite_number(self.reservation_payout_minor, 'reservation_payout_minor', minimum=0)
        finite_number(self.hold_max_wait_seconds, 'hold_max_wait_seconds', minimum=0)
        if self.cancel_rule not in ('patience', 'private_eta'):
            raise ValueError('Unknown driver cancel rule')
        finite_number(self.cancel_eta_threshold_seconds, 'cancel_eta_threshold_seconds', strictly_positive=True)
        finite_number(self.cancel_check_seconds, 'cancel_check_seconds', minimum=0)
        finite_number(self.penalty_tolerance_minor, 'penalty_tolerance_minor', minimum=0)
        if self.availability not in ('always_open', 'pause_when_full', 'pause_while_serving_other'):
            raise ValueError('Unknown driver availability policy')
        if self.tie_break not in ('stable', 'random'):
            raise ValueError('Unknown driver tie-break')


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
        # Iterate this class's own declared fields, not plain(self): a V2 subclass (behavior-policy.md
        # "Participant policies v2") adds string/bool/tuple traits that must never reach finite_number.
        for f in fields(EvolutionTraits):
            name, value = f.name, getattr(self, f.name)
            if name != 'onboard_car':
                finite_number(value, name, strictly_positive=name.startswith('prior_'))
        finite_number(self.learning_rate, 'learning_rate', maximum=1)
        if not isinstance(self.onboard_car, bool):
            raise ValueError('onboard_car must be boolean')


@dataclass(frozen=True)
class EvolutionTraitsV2(EvolutionTraits):
    """Additive `@2` traits. Every default reproduces `@1`: `install_trigger_peer_share=0` is off
    and `adoption_phase='any'` never suppresses a checkpoint's own hazard download."""
    install_trigger_peer_share: float = 0
    vicinity_km: float = 2
    adoption_phase: str = 'any'

    def __post_init__(self):
        super().__post_init__()
        finite_number(self.install_trigger_peer_share, 'install_trigger_peer_share', maximum=1)
        finite_number(self.vicinity_km, 'vicinity_km', strictly_positive=True)
        if self.adoption_phase not in ('any', 'idle'):
            raise ValueError('Unknown adoption phase gate')


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


@dataclass(frozen=True)
class Download:
    """`rider_search@2` decide: install a known, launched, uninstalled app mid-search
    (`install_trigger_eta_seconds`). Applied by the runtime like a checkpoint's own download."""
    platform_id: str


@dataclass(frozen=True)
class SwitchPreferred:
    """`rider_search@2` decide: a fatigue-triggered switch of `preferred_app` (`fatigue_threshold`).
    `sticky` records whether `personal_evolution@2` should block learning-driven reversion."""
    platform_id: str
    sticky: bool = False


def ranked_apps(apps, preferred, scores, incentives):
    return sorted(apps, key=lambda p: (-(scores.get(p, 0) + incentives.get(p, 0)
                                       + (1 if p == preferred else 0)), p))


def offer_value(o, objective, speed_kmh):
    """A pending offer's value under a driver's chosen objective (driver_participation@2
    response_rule='best_pending' / hold_for_better). `o` is a context.pending_offers entry."""
    payout = o.payout_minor + o.bonus_minor
    if objective == 'payout_per_minute':
        minutes = max(1e-9, (o.private_eta_seconds + math.dist(o.pickup, o.destination) * 3600 / speed_kmh) / 60)
        return payout / minutes
    return payout


def lexicographic_best(candidates, keys):
    """Narrow `candidates` (viable quotes) to the survivors of each `(key, tolerance)` pair in
    order -- 'price' (net of discount) or 'eta' -- keeping every quote within `tolerance` of the
    tightest survivor at each step. `rider_search@2` choice_rule='lexicographic'; never empty when
    `candidates` is not."""
    survivors = list(candidates)
    for key, tolerance in keys:
        value = (lambda q: q.fare.gross_minor - q.fare.discount_minor) if key == 'price' else (lambda q: q.eta_seconds)
        floor = min(value(q) for q in survivors)
        survivors = [q for q in survivors if value(q) <= floor + tolerance]
    return survivors


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


class RiderPolicyV2(RiderPolicy):
    """behavior-policy.md "Participant policies v2". `decide` copies `@1`'s body with four gated
    blocks inserted; with default traits every gate is inert and the memory-seeding/random-draw
    order is untouched, so a default-trait @2 rider consumes the same draws in the same order."""
    declaration = Declaration('rider_search', '2', RiderTraitsV2,
        ('now', 'intent', 'quotes', 'usable_apps', 'preferred_app', 'scores', 'announced',
         'failures', 'known_launched_apps', 'sticky_preference'),
        (OpenApp, OrderQuote, Download, SwitchPreferred, Cancel, Stop, Wait), 1,
        ('decide', 'progress'), RiderPolicy.declaration.memory_schema)

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
        # Fatigue switch: reads only the *current* preferred app's failure counter, which is 0 for
        # the newly chosen platform, so this cannot oscillate on the very next decision.
        if t.fatigue_threshold and context.failures.get(context.preferred_app, 0) >= t.fatigue_threshold:
            others = [p for p in context.usable_apps if p != context.preferred_app]
            best_other = (ranked_apps(others, context.preferred_app, context.scores, context.announced)[0]
                         if others else context.preferred_app)
            target = t.fatigue_target if t.fatigue_target != 'best_other' else best_other
            if target != context.preferred_app and target in context.usable_apps:
                return Decision(SwitchPreferred(target, t.sticky), m, 'Consecutive failures on the preferred app')
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
        inspect = not viable or (t.compare_all_apps and unseen) or m['inspection_draw'] < inspect_probability
        if inspect and unseen and len(m['visited']) < t.max_app_visits:
            m['visited'].append(unseen[0])
            return Decision(OpenApp(unseen[0]), m, 'Inspect an unseen app after price, ETA or supply dissatisfaction')

        if (t.install_trigger_eta_seconds > 0 and latest is not None and latest.eta_seconds is not None
                and latest.eta_seconds > t.install_trigger_eta_seconds):
            install_candidates = [p for p in context.known_launched_apps if p not in context.usable_apps]
            if install_candidates:
                target = ranked_apps(install_candidates, context.preferred_app, context.scores, context.announced)[0]
                # One install per intent per target: the target leaves known_launched_apps-minus-usable
                # once installed anyway, but this also guards against a runtime-refused install.
                if target not in m.setdefault('downloaded', []):
                    m['downloaded'].append(target)
                    return Decision(Download(target), m, 'Quoted pickup ETA exceeds the install trigger threshold')

        def utility(q):
            p = q.platform_id
            taste = m['taste'].setdefault(p, random.uniform(('taste', p)))
            return (t.purchase_bias - t.price_sensitivity * ((q.fare.gross_minor - q.fare.discount_minor) / reference)
                    - t.eta_sensitivity * q.eta_seconds / t.eta_tolerance_seconds
                    + t.loyalty * (p == context.preferred_app) + context.scores.get(p, 0)
                    - t.search_cost * m['visited'].index(p)
                    + t.taste_scale * math.log(taste / (1 - taste)))

        if t.choice_rule == 'lexicographic':
            survivors = lexicographic_best(viable, t.choice_keys) if viable else []
            if not survivors:
                best = None
            elif t.tie_break == 'random' and len(survivors) > 1:
                draw = random.uniform('lexicographic_tie')
                best = survivors[int(draw * len(survivors))]
            else:
                best = min(survivors, key=lambda q: (q.platform_id, -q.at, -q.id))
        else:
            best = min(viable, key=lambda q: (-utility(q), q.platform_id, -q.at, -q.id), default=None)
        # The outside-option gate is unchanged by choice_rule: only the *ranking* among viable
        # quotes changes; purchase still requires utility(best) >= the fixed outside option.
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
        """ETA-drift cancellation before the inherited @1 patience branch. Drift compares *predicted
        arrival instants* (at + eta_seconds), never raw ETA values, so elapsed time alone never
        looks like improvement (behavior-policy.md)."""
        t, order = self.traits, context.order
        if t.eta_drift_cancel_seconds and 'boarded' not in order.timeline:
            promise = next((p for p in order.eta_predictions
                            if p['source'] == 'offer' and p['eta_seconds'] is not None), None)
            latest = next((p for p in reversed(order.eta_predictions)
                           if p['source'] == 'revision' and p['eta_seconds'] is not None), None)
            if promise is not None and latest is not None:
                drift = (latest['at'] + latest['eta_seconds']) - (promise['at'] + promise['eta_seconds'])
                if drift > t.eta_drift_cancel_seconds:
                    return Decision(Cancel(order.id, 'rider', 'pickup eta drift'), plain(memory),
                                    'Predicted pickup arrival instant has drifted past the offer promise')
        return super().progress(context, memory, random)


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


class DriverPolicyV2(DriverPolicy):
    """behavior-policy.md "Participant policies v2". `expand` is inherited unchanged;
    `open_apps_at_start` and `availability` are applied by the runtime, exactly as `@1`'s
    `after_service='preferred'` already is (policy_runtime.PolicyRuntime.sync_driver/on_notification)."""
    declaration = Declaration('driver_participation', '2', DriverTraitsV2,
        ('now', 'position', 'commitments', 'offer', 'private_eta_seconds', 'free_slots',
         'open_apps', 'usable_apps', 'preferred_app', 'scores', 'announced', 'exit_requested',
         'pending_offers', 'terms', 'estimated_wait_seconds', 'speed_kmh', 'tie_draw'),
        (ExpandApps, Respond, Cancel, Stop, Wait), 1, ('expand', 'respond', 'progress'),
        (('no_offer_since', 'number'), ('visited', 'array'), ('expanded_at', 'number'),
         ('last_result', 'string')))

    def respond(self, context, memory, random):
        """Layers response_rule/hold_for_better on the unchanged @1 logistic (identical score,
        identical `RandomValues(('response', driver_id, platform_id, offer_id))` draw), so a
        default-trait @2 driver decides byte-for-byte like @1. Each new stage may force
        `probability=0` deterministically; the explanation names whichever stage decided it."""
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
        explanation = 'Evaluate payout and private remaining workload'
        mine = next(o for o in context.pending_offers if o.offer_id == context.offer.id)
        if probability > 0 and t.response_rule == 'best_pending':
            best = max(offer_value(o, t.response_objective, context.speed_kmh) for o in context.pending_offers)
            if offer_value(mine, t.response_objective, context.speed_kmh) < best:
                probability, explanation = 0, 'another pending offer scores higher'
            else:
                tied = sorted((o for o in context.pending_offers
                               if offer_value(o, t.response_objective, context.speed_kmh) == best),
                              key=lambda o: (o.platform_id, o.offer_id))
                winner = tied[int(context.tie_draw * len(tied))] if t.tie_break == 'random' else tied[0]
                if winner.offer_id != context.offer.id:
                    probability, explanation = 0, 'another pending offer scores higher'
        if (probability > 0 and t.hold_for_better and not context.commitments
                and context.free_slots > 0 and not context.exit_requested):
            if t.reservation_payout_minor > 0:
                reservation, eligible = t.reservation_payout_minor, True
            else:
                reservation = t.reference_payout_minor
                wait = min((context.estimated_wait_seconds.get(p, {}).get('idle', math.inf)
                            for p in context.open_apps if p != context.offer.platform_id), default=math.inf)
                eligible = wait <= t.hold_max_wait_seconds
            if eligible and offer_value(mine, t.response_objective, context.speed_kmh) < reservation:
                probability, explanation = 0, 'holding out for a better offer'
        accept = random.uniform('accept') < probability
        return Decision(Respond(context.offer.id, accept, probability, context.offer.eta_seconds,
                                context.private_eta_seconds), plain(memory), explanation)

    def progress(self, context, memory, random):
        """A private-ETA cancel rule layered before the inherited @1 patience branch. A lockout is
        valued at `lockout_seconds * reference_payout_minor / 600`, the same money-per-second scale
        `driver_reward` already uses (behavior-policy.md)."""
        t, order = self.traits, context.order
        if t.cancel_rule == 'private_eta' and 'boarded' not in order.timeline:
            lockout_value = context.terms['lockout_seconds'] * t.reference_payout_minor / 600
            exposure = context.terms['cancellation_penalty_minor'] + lockout_value
            if (context.private_eta_seconds > t.cancel_eta_threshold_seconds
                    and exposure <= t.penalty_tolerance_minor):
                return Decision(Cancel(order.id, 'driver', 'private pickup eta exceeds threshold'), plain(memory),
                                'Cancel an overlong pickup whose declared penalty exposure is tolerable')
        return super().progress(context, memory, random)


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


class EvolutionPolicyV2(EvolutionPolicy):
    """behavior-policy.md "Participant policies v2". `checkpoint` calls the inherited body
    unchanged (identical random draw order -- the hazard draw is always consumed, even when
    `adoption_phase` discards its result) and applies three overrides to the result: sticky
    preference, an idle-only adoption phase, and a deterministic peer-installed-share cascade."""
    declaration = Declaration('personal_evolution', '2', EvolutionTraitsV2,
        EvolutionPolicy.declaration.inputs + ('phase', 'neighbor_install_share', 'sticky_preference'),
        (Evolve,), 1, ('checkpoint',), EvolutionPolicy.declaration.memory_schema)

    def checkpoint(self, context, memory, random):
        t = self.traits
        decision = super().checkpoint(context, memory, random)
        preferred_app, download = decision.action.preferred_app, decision.action.download
        if context.sticky_preference:
            # Learning-driven switching is suppressed; fatigue switching still works because it
            # runs in the rider policy, not here.
            preferred_app = context.preferred_app
        if t.adoption_phase == 'idle' and context.phase != 'idle':
            download = None
        if download is None and t.install_trigger_peer_share > 0:
            candidates = sorted(p for p in context.known_launched_apps if p not in context.apps
                                and context.neighbor_install_share.get(p, 0) >= t.install_trigger_peer_share)
            if candidates:
                download = candidates[0]
        return Decision(Evolve(download, preferred_app, t.onboard_car), decision.memory, decision.explanation)


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
