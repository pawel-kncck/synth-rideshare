"""Platform decisions. Only detached platform-visible observations enter here.

See plans/architecture/marketplace-policy.md for the normative contract.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from decimal import Decimal, ROUND_HALF_UP

from policy_contracts import (Cancel, Declaration, Decision, Stop, Transfer, Wait,
                              finite_number, positive_int, plain, freeze)


def rounded(value):
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def memory_key(driver_id):
    """Key a driver into platform memory the same way PolicyRuntime.key('driver', id) does."""
    return f'driver:{driver_id}'


def inside(box, point):
    """Whether point lies in box's closed rectangle. box=None (no declared service area) is always True.

    box must already be resolved to ((x0,y0),(x1,y1)); a raw zone-id string reaching here is a
    compiler bug, not a silent no-op (plans/architecture/scenario-definition.md's service_area note).
    """
    if box is None:
        return True
    if isinstance(box, str):
        raise ValueError('service_area must be resolved to a box')
    (x0, y0), (x1, y1) = box
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


@dataclass(frozen=True)
class Campaign:
    id: str
    start: float = 0
    end: float = 86400
    discount_minor: int = 0
    discount_fraction: float = 0
    discount_cap_minor: int | None = None
    bonus_minor: int = 0
    segment: str | None = None
    new_user_only: bool = False
    awareness: str = 'announced'
    budget_minor: int | None = None          # None = unlimited (off); reserved at quote, consumed at completion
    max_completed_rides: int | None = None   # None = unlimited (off); first-N redemptions per person

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError('Campaign ID is required')
        finite_number(self.start, 'campaign start')
        finite_number(self.end, 'campaign end', strictly_positive=True)
        if self.end <= self.start:
            raise ValueError('Campaign end must follow start')
        for name in ('discount_minor', 'bonus_minor', 'discount_cap_minor', 'budget_minor'):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f'{name} must be nonnegative integer minor units')
        finite_number(self.discount_fraction, 'discount_fraction', maximum=1)
        if self.discount_minor and self.discount_fraction:
            raise ValueError('Choose fixed or percentage discount')
        if not isinstance(self.new_user_only, bool):
            raise ValueError('new_user_only must be boolean')
        if self.awareness not in ('announced', 'in_app'):
            raise ValueError('Unknown campaign awareness')
        if self.max_completed_rides is not None:
            positive_int(self.max_completed_rides, 'max_completed_rides')

    def eligible(self, now, visible):
        return (self.start <= now < self.end
                and (self.segment is None or visible.get('segment') == self.segment)
                and (not self.new_user_only or visible.get('new_user') is True)
                and (self.max_completed_rides is None or visible.get('completed_rides', 0) < self.max_completed_rides))

    def discount(self, gross):
        amount = self.discount_minor or rounded(Decimal(gross) * Decimal(str(self.discount_fraction)))
        return min(gross, amount, self.discount_cap_minor if self.discount_cap_minor is not None else gross)


@dataclass(frozen=True)
class MarketplaceParameters:
    base_fare_minor: int = 200
    per_km_minor: int = 150
    per_minute_minor: int = 0
    minimum_fare_minor: int = 0
    multiplier: float = 1
    commission_fraction: float = .2
    estimated_speed_kmh: float = 30
    estimated_boarding_seconds: float = 30
    estimator: str = 'own_service'
    matching: str = 'nearest'
    max_local_commitments: int = 2
    back_to_back_within_seconds: float = 1800
    max_attempts: int = 5
    retry_drivers: bool = False
    retry_seconds: float = 1
    order_patience_seconds: float = 60
    offer_seconds: float = 10
    quote_seconds: float = 30
    rider_cancellation_fee_minor: int = 0
    driver_cancellation_compensation_minor: int = 0
    driver_cancellation_penalty_minor: int = 0
    surcharge_minor: int = 0                 # after the multiplier, before rounding
    surcharge_driver_share: float = 0        # 0..1, commission-exempt share of the surcharge
    commission_binding: str = 'offer'        # 'offer' (today) or 'quote'
    driver_lockout_seconds: float = 0        # 0 = off; set on a driver-initiated cancel
    guarantee_window_seconds: float = 0      # 0 = off; the platform's single record-keeping/window-close cadence
    announce_terms: bool = False
    service_area: object = None              # None | zone id str (pre-compile) | ((x0,y0),(x1,y1)) (compiled)

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name.endswith('_minor'):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(f'{f.name} must be nonnegative integer minor units')
        for name in ('max_attempts', 'max_local_commitments'):
            positive_int(getattr(self, name), name)
        if self.max_local_commitments > 2:
            raise ValueError('A platform cannot request more than two accepted commitments')
        for name in ('estimated_speed_kmh', 'retry_seconds', 'order_patience_seconds',
                     'offer_seconds', 'quote_seconds'):
            finite_number(getattr(self, name), name, strictly_positive=True)
        for name in ('multiplier', 'estimated_boarding_seconds', 'back_to_back_within_seconds',
                     'driver_lockout_seconds', 'guarantee_window_seconds'):
            finite_number(getattr(self, name), name)
        finite_number(self.commission_fraction, 'commission_fraction', maximum=1)
        finite_number(self.surcharge_driver_share, 'surcharge_driver_share', maximum=1)
        if self.estimator not in ('own_service', 'current_position'):
            raise ValueError('Unknown estimator')
        if self.matching not in ('nearest', 'pickup_eta'):
            raise ValueError('Unknown matching policy')
        if not isinstance(self.retry_drivers, bool):
            raise ValueError('retry_drivers must be boolean')
        if self.commission_binding not in ('offer', 'quote'):
            raise ValueError('Unknown commission binding')
        if not isinstance(self.announce_terms, bool):
            raise ValueError('announce_terms must be boolean')
        if self.service_area is not None:
            if isinstance(self.service_area, str):
                if not self.service_area:
                    raise ValueError('service_area must be a nonempty zone id or a box')
            else:
                try:
                    (x0, y0), (x1, y1) = self.service_area
                except (TypeError, ValueError):
                    raise ValueError('service_area must be a zone id string or an ((x0,y0),(x1,y1)) box') from None
                for value in (x0, y0, x1, y1):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError('service_area box coordinates must be finite numbers')
                if x1 <= x0 or y1 <= y0:
                    raise ValueError('service_area box needs max greater than min on both axes')
                # Normalized so a snapshot round-trip through plain()/lists is idempotent.
                object.__setattr__(self, 'service_area', ((float(x0), float(y0)), (float(x1), float(y1))))


VISIBLE_FIELDS = {'segment', 'new_user', 'completed_rides', 'role', 'origin_zone', 'destination_zone'}


@dataclass(frozen=True)
class ConditionalRule:
    id: str
    priority: int
    when: dict
    parameters: dict
    start: float | None = None   # seconds; half-open [start, end), same semantics as a campaign's window
    end: float | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError('Rules need an ID and an integer priority')
        if not self.when or set(self.when) - VISIBLE_FIELDS:
            raise ValueError('Conditions must use declared platform-visible fields')
        if set(self.parameters) - {f.name for f in fields(MarketplaceParameters)}:
            raise ValueError('Unknown policy parameter in rule')
        if (self.start is None) != (self.end is None):
            raise ValueError('A rule window needs both start and end, or neither')
        if self.start is not None:
            finite_number(self.start, 'rule start')
            finite_number(self.end, 'rule end', strictly_positive=True)
            if self.end <= self.start:
                raise ValueError('Rule window end must follow start')
        object.__setattr__(self, 'when', freeze(self.when))
        object.__setattr__(self, 'parameters', freeze(self.parameters))


@dataclass(frozen=True)
class Program:
    """An hourly_guarantee: pays a driver floor_minor minus their own window earnings when they
    qualify (see PlatformPolicy.resolve/MarketplacePolicy.observe). window_seconds must equal the
    platform's own guarantee_window_seconds (PlatformPolicy.__post_init__); there is one clock."""
    id: str
    kind: str = 'hourly_guarantee'
    floor_minor: int = 0
    window_seconds: float = 3600
    min_acceptance_rate: float = 0
    min_online_seconds: float = 0
    zero_dispatch_qualifies: bool = True

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError('Program ID is required')
        if self.kind != 'hourly_guarantee':
            raise ValueError('Unknown program kind')
        if isinstance(self.floor_minor, bool) or not isinstance(self.floor_minor, int) or self.floor_minor < 0:
            raise ValueError('floor_minor must be nonnegative integer minor units')
        finite_number(self.window_seconds, 'window_seconds', strictly_positive=True)
        finite_number(self.min_acceptance_rate, 'min_acceptance_rate', maximum=1)
        finite_number(self.min_online_seconds, 'min_online_seconds')
        if not isinstance(self.zero_dispatch_qualifies, bool):
            raise ValueError('zero_dispatch_qualifies must be boolean')


@dataclass(frozen=True)
class PlatformPolicy:
    version: str = 'modern-v1'
    parameters: MarketplaceParameters = field(default_factory=MarketplaceParameters)
    rules: tuple = ()
    campaigns: tuple = ()
    fallback: str = 'default'
    programs: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, 'rules', tuple(self.rules))
        object.__setattr__(self, 'campaigns', tuple(self.campaigns))
        object.__setattr__(self, 'programs', tuple(self.programs))
        if not isinstance(self.parameters, MarketplaceParameters):
            raise ValueError('parameters must be compiled MarketplaceParameters')
        if (not isinstance(self.version, str) or not isinstance(self.fallback, str)
                or not self.version or not self.fallback):
            raise ValueError('A policy requires a version and explicit fallback')
        if len({r.priority for r in self.rules}) != len(self.rules):
            raise ValueError('Conditional priorities must be unique')
        if len({r.id for r in self.rules} | {self.fallback}) != len(self.rules) + 1:
            raise ValueError('Rule IDs and fallback must be unique')
        if len({c.id for c in self.campaigns}) != len(self.campaigns):
            raise ValueError('Campaign IDs must be unique')
        if len({p.id for p in self.programs}) != len(self.programs):
            raise ValueError('Program IDs must be unique')
        for rule in self.rules:
            merged = MarketplaceParameters(**(plain(self.parameters) | plain(rule.parameters)))
            # guarantee_window_seconds is the platform's single window-close cadence (scheduled once,
            # from the base parameters, in Simulation.__init__); a rule may not diverge from it, or
            # window bucketing/scheduling would disagree about which cadence is in force.
            if merged.guarantee_window_seconds != self.parameters.guarantee_window_seconds:
                raise ValueError(f'Rule {rule.id!r} may not change guarantee_window_seconds; it sets the '
                                 'platform-wide record-keeping and window-close cadence')
        for program in self.programs:
            if program.window_seconds != self.parameters.guarantee_window_seconds:
                raise ValueError(f'Program {program.id!r} window_seconds ({program.window_seconds:g}) must equal '
                                 f'guarantee_window_seconds ({self.parameters.guarantee_window_seconds:g})')
        if self.programs and not self.parameters.guarantee_window_seconds:
            raise ValueError('Programs require a positive guarantee_window_seconds')

    @classmethod
    def compile(cls, *, defaults=None, overrides=None, rules=(), campaigns=(), programs=(),
                version='modern-v1', fallback='default'):
        """Strict typed binding; unknown keys and invalid conditional values fail now."""
        return cls(version, MarketplaceParameters(**((defaults or {}) | (overrides or {}))),
                   tuple(ConditionalRule(**r) for r in rules),
                   tuple(Campaign(**c) for c in campaigns), fallback,
                   tuple(Program(**p) for p in programs))

    def resolve(self, visible, now=None):
        for rule in sorted(self.rules, key=lambda r: -r.priority):
            if (all(visible.get(k) == v for k, v in rule.when.items())
                    and (rule.start is None or (now is not None and rule.start <= now < rule.end))):
                return MarketplaceParameters(**(plain(self.parameters) | plain(rule.parameters))), rule.id
        return self.parameters, self.fallback


@dataclass(frozen=True)
class QuoteProposal:
    gross_minor: int
    discount_minor: int
    distance_km: float
    duration_seconds: float
    eta_seconds: float | None
    expires_at: float
    policy_version: str
    selected_rule: str
    campaign_id: str | None
    commission_fraction: float | None = None  # binds the offer-time commission when commission_binding='quote'
    driver_surcharge_minor: int = 0           # commission-exempt flat share of a surcharge already in gross_minor


@dataclass(frozen=True)
class OfferProposal:
    driver_id: object
    payout_minor: int
    bonus_minor: int
    eta_seconds: float
    expires_at: float
    policy_version: str
    selected_rule: str
    campaign_id: str | None


@dataclass(frozen=True)
class EtaProposal:
    eta_seconds: float
    policy_version: str
    selected_rule: str


class MarketplacePolicy:
    declaration = Declaration('marketplace', '1', PlatformPolicy,
        ('now', 'platform_id', 'request', 'order', 'quote', 'drivers', 'own_orders', 'own_offers', 'visible',
         'kind', 'data', 'offer', 'driver_id'),
        (QuoteProposal, OfferProposal, EtaProposal, Wait, Stop, Cancel, Transfer), 1,
        ('quote', 'dispatch', 'revise', 'cancel', 'controller', 'observe'),
        (('completed', 'object'), ('last_controller_at', 'number'), ('locked_until', 'object'),
         ('windows', 'object'), ('open', 'object'), ('budgets', 'object'), ('reservations', 'object')),
        ('order_created', 'order_canceled', 'order_completed', 'offer_resolved', 'driver_app_opened',
         'driver_app_closed', 'driver_availability', 'window_closed'))

    def __init__(self, config):
        self.config = config

    def observed_kinds(self):
        """Which platform-audience notification kinds this policy's compiled config actually needs,
        scanned once from the compiled config (base parameters plus every rule's merged parameters,
        since driver_lockout_seconds is rule-overridable). Empty for every shipped preset, so the
        runtime never calls observe() and platform_memory stays untouched -- see
        plans/architecture/marketplace-policy.md.
        """
        configs = [self.config.parameters] + [
            MarketplaceParameters(**(plain(self.config.parameters) | plain(rule.parameters)))
            for rule in self.config.rules]
        kinds = set()
        if any(c.driver_lockout_seconds > 0 for c in configs):
            kinds.add('order_canceled')
        if any(c.guarantee_window_seconds > 0 for c in configs):
            kinds |= {'offer_resolved', 'order_completed', 'driver_app_opened', 'driver_app_closed',
                     'driver_availability', 'window_closed'}
        if any(c.budget_minor is not None for c in self.config.campaigns):
            kinds |= {'order_created', 'order_canceled', 'order_completed'}
        return kinds

    def travel(self, distance, params):
        return distance * 3600 / params.estimated_speed_kmh

    def pickup_eta(self, context, driver, pickup, params):
        position, remaining = driver.position, 0.0
        if params.estimator == 'own_service':
            for order_id in driver.own_order_ids:
                order = context.own_orders[str(order_id)]
                if 'boarded' not in order.timeline:
                    remaining += self.travel(math.dist(position, order.pickup), params)
                    remaining += (max(0, params.estimated_boarding_seconds - (context.now - order.timeline['arrived']))
                                  if 'arrived' in order.timeline else params.estimated_boarding_seconds)
                    position = order.pickup
                remaining += self.travel(math.dist(position, order.destination), params)
                position = order.destination
        return remaining + self.travel(math.dist(position, pickup), params)

    def candidates(self, context, pickup, memory=None):
        locked = (memory or {}).get('locked_until', {})
        result = []
        for driver in context.drivers:
            params, rule = self.config.resolve(driver.visible, context.now)
            if (not driver.accepting or driver.pending_offer_ids
                    or len(driver.own_order_ids) >= params.max_local_commitments
                    or locked.get(memory_key(driver.driver_id), 0) > context.now
                    or not inside(params.service_area, driver.position)):
                continue
            eta = self.pickup_eta(context, driver, pickup, params)
            travel = self.travel(math.dist(driver.position, pickup), params)
            if driver.own_order_ids and eta - travel > params.back_to_back_within_seconds:
                continue
            score = math.dist(driver.position, pickup) if params.matching == 'nearest' else eta
            stable_id = (type(driver.driver_id).__name__, driver.driver_id)
            result.append((score, stable_id, driver, eta, params, rule))
        return sorted(result, key=lambda row: row[:2])

    def _budget_room(self, memory, campaign, amount):
        if campaign.budget_minor is None:
            return True
        b = memory.get('budgets', {}).get(campaign.id, {'reserved': 0, 'consumed': 0})
        return b.get('reserved', 0) + b.get('consumed', 0) + amount <= campaign.budget_minor

    def _sweep_reservations(self, memory, now):
        """Release every reservation whose quote expired without converting to an order -- lazy,
        since there is no engine event for quote expiry. A true no-op when nothing is reserved, so a
        platform with no budgeted campaign never gains these memory keys.
        """
        reservations = memory.get('reservations')
        if not reservations:
            return memory
        reservations = dict(reservations)
        budgets = {k: dict(v) for k, v in memory.get('budgets', {}).items()}
        changed = False
        for key, r in list(reservations.items()):
            if r['order_id'] is None and r['expires_at'] <= now:
                b = budgets.setdefault(r['campaign_id'], {'reserved': 0, 'consumed': 0})
                b['reserved'] -= r['amount_minor']
                del reservations[key]
                changed = True
        return {**memory, 'reservations': reservations, 'budgets': budgets} if changed else memory

    def quote(self, context, memory, random):
        params, rule = self.config.resolve(context.visible, context.now)
        if not inside(params.service_area, context.request.origin) or not inside(params.service_area, context.request.destination):
            return Decision(Stop('outside service area'), plain(memory), 'Request outside the declared service area')
        m = plain(memory)
        budgeted = any(c.budget_minor is not None for c in self.config.campaigns)
        if budgeted:
            m = self._sweep_reservations(m, context.now)
        distance = math.dist(context.request.origin, context.request.destination)
        duration = self.travel(distance, params)
        raw = (Decimal(params.base_fare_minor) + Decimal(params.per_km_minor) * Decimal(str(distance))
               + Decimal(params.per_minute_minor) * Decimal(str(duration)) / 60)
        gross = rounded(max(Decimal(params.minimum_fare_minor), Decimal(str(params.multiplier)) * raw)
                        + Decimal(params.surcharge_minor))
        driver_surcharge = rounded(Decimal(params.surcharge_minor) * Decimal(str(params.surcharge_driver_share)))
        commission = params.commission_fraction if params.commission_binding == 'quote' else None
        eligible = [c for c in self.config.campaigns if c.eligible(context.now, context.visible)]
        if budgeted:
            eligible = [c for c in eligible if self._budget_room(m, c, c.discount(gross))]
        chosen = min(eligible, key=lambda c: (-c.discount(gross), c.id), default=None)
        discount = chosen.discount(gross) if chosen else 0
        if budgeted:
            # One live quote per intent per platform: a fresh quote for this intent releases
            # whatever this platform reserved for it last, then reserves afresh.
            reservations = dict(m.get('reservations', {}))
            budgets = {k: dict(v) for k, v in m.get('budgets', {}).items()}
            prior = reservations.pop(str(context.request.intent_id), None)
            if prior is not None:
                budgets.setdefault(prior['campaign_id'], {'reserved': 0, 'consumed': 0})['reserved'] -= prior['amount_minor']
            if chosen and discount and chosen.budget_minor is not None:
                reservations[str(context.request.intent_id)] = {
                    'campaign_id': chosen.id, 'amount_minor': discount,
                    'expires_at': context.now + params.quote_seconds, 'order_id': None}
                budgets.setdefault(chosen.id, {'reserved': 0, 'consumed': 0})['reserved'] += discount
            m = {**m, 'reservations': reservations, 'budgets': budgets}
        candidates = self.candidates(context, context.request.origin, m)
        proposal = QuoteProposal(gross, discount, distance, duration,
                                 min((c[3] for c in candidates), default=None), context.now + params.quote_seconds,
                                 self.config.version, rule, chosen.id if chosen and discount else None,
                                 commission, driver_surcharge)
        return Decision(proposal, m, 'Quoted using observed local supply and committed fare')

    def dispatch(self, context, memory, random):
        params, _ = self.config.resolve(context.visible, context.now)
        order = context.order
        attempts = [context.own_offers[str(i)] for i in order.offer_ids]
        if any(o.state == 'pending' for o in attempts):
            return Decision(Wait(params.retry_seconds), plain(memory), 'Own offer unresolved')
        if len(attempts) >= params.max_attempts or context.now >= order.created_at + params.order_patience_seconds:
            return Decision(Stop('dispatch exhausted'), plain(memory), 'Bounded dispatch ended')
        tried = {o.driver_id for o in attempts}
        candidates = [row for row in self.candidates(context, order.pickup, plain(memory))
                      if params.retry_drivers or row[2].driver_id not in tried]
        if not candidates:
            return Decision(Wait(min(params.retry_seconds, order.created_at + params.order_patience_seconds - context.now)),
                            plain(memory), 'Waiting for locally eligible supply')
        _, _, driver, eta, driver_params, rule = candidates[0]
        eligible = [c for c in self.config.campaigns if c.eligible(context.now, driver.visible)]
        bonus = min(eligible, key=lambda c: (-c.bonus_minor, c.id), default=None)
        bound = context.quote.commission_fraction
        c = driver_params.commission_fraction if bound is None else bound
        share = context.quote.driver_surcharge_minor
        payout = rounded(Decimal(order.fare.gross_minor - share) * (1 - Decimal(str(c)))) + share
        proposal = OfferProposal(driver.driver_id, payout, bonus.bonus_minor if bonus else 0, eta,
                                 min(context.now + driver_params.offer_seconds, order.created_at + params.order_patience_seconds),
                                 self.config.version, rule, bonus.id if bonus and bonus.bonus_minor else None)
        return Decision(proposal, plain(memory), 'First ranked locally eligible candidate')

    def revise(self, context, memory, random):
        driver = next((d for d in context.drivers if d.driver_id == context.order.assignment.driver_id), None)
        if driver is None:
            return Decision(Stop('no current position observation'), plain(memory), 'Cannot revise without local observation')
        params, rule = self.config.resolve(driver.visible, context.now)
        # Only preceding own orders count. The target's boarding/journey are excluded.
        preceding = list(driver.own_order_ids)
        preceding = preceding[:preceding.index(context.order.id)] if context.order.id in preceding else preceding
        driver = freeze(plain(driver) | {'own_order_ids': preceding})
        eta = self.pickup_eta(context, driver, context.order.pickup, params)
        return Decision(EtaProposal(eta, self.config.version, rule), plain(memory), 'Revised remaining pickup time from own observations')

    def cancel(self, context, memory, random):
        params, _ = self.config.resolve(context.visible, context.now)
        action = Stop('cancellation denied')
        if context.order.state not in ('canceled', 'completed') and 'boarded' not in context.order.timeline:
            action = Cancel(context.order.id, context.party, context.reason,
                            params.rider_cancellation_fee_minor if context.party == 'rider' else 0,
                            params.driver_cancellation_compensation_minor if context.party == 'rider' else 0,
                            params.driver_cancellation_penalty_minor if context.party == 'driver' else 0)
        return Decision(action, plain(memory), 'Cancellation terms evaluated before boarding')

    def controller(self, context, memory, random):
        """Initial fixed controller; timed version interventions are applied by the runtime."""
        updated = plain(memory)
        updated['last_controller_at'] = context.now
        return Decision(Stop('fixed policy'), updated, 'No automatic tariff adjustment')

    @staticmethod
    def _blank_window():
        return {'dispatches': 0, 'accepted': 0, 'rejected': 0, 'expired': 0, 'online_seconds': 0, 'payout_minor': 0}

    def _window_start(self, now):
        window = self.config.parameters.guarantee_window_seconds
        return math.floor(now / window) * window

    def _window_record(self, m, w, key):
        return m.setdefault('windows', {}).setdefault(str(w), {}).setdefault(key, self._blank_window())

    def _release_reservation(self, m, intent_id, *, consume):
        reservations = m.get('reservations')
        if not reservations:
            return
        r = reservations.pop(str(intent_id), None)
        if r is None:
            return
        b = m.setdefault('budgets', {}).setdefault(r['campaign_id'], {'reserved': 0, 'consumed': 0})
        b['reserved'] -= r['amount_minor']
        if consume:
            b['consumed'] += r['amount_minor']

    def observe(self, context, memory, random):
        """Platform-audience notifications this policy asked for (observed_kinds), plus the runtime-
        generated window_closed. Everything here is additive and default-off: absent driver_lockout_
        seconds/guarantee_window_seconds/budgeted campaigns means the corresponding branch below is
        simply never reached (the runtime gate keeps the hook from even being called).
        """
        m = plain(memory)
        kind, data = context.kind, context.data
        if kind == 'order_canceled':
            params, _ = self.config.resolve(context.visible, context.now)
            if data['by'] == 'driver' and params.driver_lockout_seconds > 0:
                key = memory_key(context.order.assignment.driver_id)
                m.setdefault('locked_until', {})[key] = context.now + params.driver_lockout_seconds
            self._release_reservation(m, context.order.intent_id, consume=False)
        elif kind == 'order_created':
            r = m.get('reservations', {}).get(str(context.order.intent_id))
            if r is not None:
                r['order_id'] = context.order.id
        elif kind == 'order_completed':
            self._release_reservation(m, context.order.intent_id, consume=True)
            if self.config.parameters.guarantee_window_seconds:
                w = self._window_start(context.now)
                self._window_record(m, w, memory_key(context.driver_id))['payout_minor'] += (
                    context.order.assignment.payout.payout_minor)
        elif kind == 'offer_resolved':
            w = self._window_start(context.now)
            rec = self._window_record(m, w, memory_key(context.driver_id))
            rec['dispatches'] += 1
            if data['state'] in ('accepted', 'rejected', 'expired'):
                rec[data['state']] += 1
        elif kind in ('driver_app_opened', 'driver_app_closed', 'driver_availability'):
            online = kind == 'driver_app_opened' or (kind == 'driver_availability' and data['accepting'])
            key = memory_key(context.driver_id)
            open_ = m.setdefault('open', {})
            if online:
                open_.setdefault(key, context.now)
            elif key in open_:
                w = self._window_start(context.now)
                self._window_record(m, w, key)['online_seconds'] += context.now - max(open_[key], w)
                del open_[key]
        action = self._close_window(m, context) if kind == 'window_closed' else Stop('observed')
        return Decision(action, m, f'Observed platform notification {kind!r}')

    def _close_window(self, m, context):
        """window_closed for window context.data['window_start'], one call per member driver.

        Records are keyed by window start and popped here, so a same-instant engine event is always
        attributed to whichever window it actually falls in, never to the one being closed.
        """
        now, driver_id = context.now, context.driver_id
        key = memory_key(driver_id)
        w = context.data['window_start']
        open_ = m.setdefault('open', {})
        if key in open_:
            # Leave open_[key]: the next window accrues from max(open_[key], w') = w'.
            self._window_record(m, w, key)['online_seconds'] += now - max(open_[key], w)
        windows = m.setdefault('windows', {})
        record = windows.get(str(w), {}).pop(key, None)
        if str(w) in windows and not windows[str(w)]:
            del windows[str(w)]
        for old_key in [wk for wk in windows if float(wk) < w]:  # bounded memory
            del windows[old_key]
        if record is None:
            return Stop('no window record for this driver')
        for program in sorted((p for p in self.config.programs if p.kind == 'hourly_guarantee'), key=lambda p: p.id):
            considered = record['accepted'] + record['rejected'] + record['expired']
            rate = record['accepted'] / considered if considered else (1.0 if program.zero_dispatch_qualifies else 0.0)
            qualifies = record['online_seconds'] >= program.min_online_seconds and rate >= program.min_acceptance_rate
            topup = program.floor_minor - record['payout_minor']
            if qualifies and topup > 0:
                # Only the first qualifying program pays per driver per window (ordered by program id).
                return Transfer('guarantee_topup', 'driver', driver_id, topup, program_id=program.id)
        return Stop('no qualifying program')
