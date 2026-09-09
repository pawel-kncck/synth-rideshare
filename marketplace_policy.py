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

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError('Campaign ID is required')
        finite_number(self.start, 'campaign start')
        finite_number(self.end, 'campaign end', strictly_positive=True)
        if self.end <= self.start:
            raise ValueError('Campaign end must follow start')
        for name in ('discount_minor', 'bonus_minor', 'discount_cap_minor'):
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

    def eligible(self, now, visible):
        return (self.start <= now < self.end
                and (self.segment is None or visible.get('segment') == self.segment)
                and (not self.new_user_only or visible.get('new_user') is True))

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
        for name in ('multiplier', 'estimated_boarding_seconds', 'back_to_back_within_seconds'):
            finite_number(getattr(self, name), name)
        finite_number(self.commission_fraction, 'commission_fraction', maximum=1)
        if self.estimator not in ('own_service', 'current_position'):
            raise ValueError('Unknown estimator')
        if self.matching not in ('nearest', 'pickup_eta'):
            raise ValueError('Unknown matching policy')
        if not isinstance(self.retry_drivers, bool):
            raise ValueError('retry_drivers must be boolean')


VISIBLE_FIELDS = {'segment', 'new_user', 'completed_rides', 'role'}


@dataclass(frozen=True)
class ConditionalRule:
    id: str
    priority: int
    when: dict
    parameters: dict

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id or isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError('Rules need an ID and an integer priority')
        if not self.when or set(self.when) - VISIBLE_FIELDS:
            raise ValueError('Conditions must use declared platform-visible fields')
        if set(self.parameters) - {f.name for f in fields(MarketplaceParameters)}:
            raise ValueError('Unknown policy parameter in rule')
        object.__setattr__(self, 'when', freeze(self.when))
        object.__setattr__(self, 'parameters', freeze(self.parameters))


@dataclass(frozen=True)
class PlatformPolicy:
    version: str = 'modern-v1'
    parameters: MarketplaceParameters = field(default_factory=MarketplaceParameters)
    rules: tuple = ()
    campaigns: tuple = ()
    fallback: str = 'default'

    def __post_init__(self):
        object.__setattr__(self, 'rules', tuple(self.rules))
        object.__setattr__(self, 'campaigns', tuple(self.campaigns))
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
        for rule in self.rules:
            MarketplaceParameters(**(plain(self.parameters) | plain(rule.parameters)))

    @classmethod
    def compile(cls, *, defaults=None, overrides=None, rules=(), campaigns=(),
                version='modern-v1', fallback='default'):
        """Strict typed binding; unknown keys and invalid conditional values fail now."""
        return cls(version, MarketplaceParameters(**((defaults or {}) | (overrides or {}))),
                   tuple(ConditionalRule(**r) for r in rules),
                   tuple(Campaign(**c) for c in campaigns), fallback)

    def resolve(self, visible):
        for rule in sorted(self.rules, key=lambda r: -r.priority):
            if all(visible.get(k) == v for k, v in rule.when.items()):
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
        ('now', 'platform_id', 'request', 'order', 'drivers', 'own_orders', 'own_offers', 'visible'),
        (QuoteProposal, OfferProposal, EtaProposal, Wait, Stop, Cancel, Transfer), 1,
        ('quote', 'dispatch', 'revise', 'cancel', 'controller'), (('completed', 'object'), ('last_controller_at', 'number')))

    def __init__(self, config):
        self.config = config

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

    def candidates(self, context, pickup):
        result = []
        for driver in context.drivers:
            params, rule = self.config.resolve(driver.visible)
            if (not driver.accepting or driver.pending_offer_ids
                    or len(driver.own_order_ids) >= params.max_local_commitments):
                continue
            eta = self.pickup_eta(context, driver, pickup, params)
            travel = self.travel(math.dist(driver.position, pickup), params)
            if driver.own_order_ids and eta - travel > params.back_to_back_within_seconds:
                continue
            score = math.dist(driver.position, pickup) if params.matching == 'nearest' else eta
            stable_id = (type(driver.driver_id).__name__, driver.driver_id)
            result.append((score, stable_id, driver, eta, params, rule))
        return sorted(result, key=lambda row: row[:2])

    def quote(self, context, memory, random):
        params, rule = self.config.resolve(context.visible)
        distance = math.dist(context.request.origin, context.request.destination)
        duration = self.travel(distance, params)
        raw = (Decimal(params.base_fare_minor) + Decimal(params.per_km_minor) * Decimal(str(distance))
               + Decimal(params.per_minute_minor) * Decimal(str(duration)) / 60)
        gross = rounded(max(Decimal(params.minimum_fare_minor), Decimal(str(params.multiplier)) * raw))
        eligible = [c for c in self.config.campaigns if c.eligible(context.now, context.visible)]
        chosen = min(eligible, key=lambda c: (-c.discount(gross), c.id), default=None)
        candidates = self.candidates(context, context.request.origin)
        proposal = QuoteProposal(gross, chosen.discount(gross) if chosen else 0, distance, duration,
                                 min((c[3] for c in candidates), default=None), context.now + params.quote_seconds,
                                 self.config.version, rule, chosen.id if chosen and chosen.discount(gross) else None)
        return Decision(proposal, plain(memory), 'Quoted using observed local supply and committed fare')

    def dispatch(self, context, memory, random):
        params, _ = self.config.resolve(context.visible)
        order = context.order
        attempts = [context.own_offers[str(i)] for i in order.offer_ids]
        if any(o.state == 'pending' for o in attempts):
            return Decision(Wait(params.retry_seconds), plain(memory), 'Own offer unresolved')
        if len(attempts) >= params.max_attempts or context.now >= order.created_at + params.order_patience_seconds:
            return Decision(Stop('dispatch exhausted'), plain(memory), 'Bounded dispatch ended')
        tried = {o.driver_id for o in attempts}
        candidates = [row for row in self.candidates(context, order.pickup)
                      if params.retry_drivers or row[2].driver_id not in tried]
        if not candidates:
            return Decision(Wait(min(params.retry_seconds, order.created_at + params.order_patience_seconds - context.now)),
                            plain(memory), 'Waiting for locally eligible supply')
        _, _, driver, eta, driver_params, rule = candidates[0]
        eligible = [c for c in self.config.campaigns if c.eligible(context.now, driver.visible)]
        bonus = min(eligible, key=lambda c: (-c.bonus_minor, c.id), default=None)
        payout = rounded(Decimal(order.fare.gross_minor) * (1 - Decimal(str(driver_params.commission_fraction))))
        proposal = OfferProposal(driver.driver_id, payout, bonus.bonus_minor if bonus else 0, eta,
                                 min(context.now + driver_params.offer_seconds, order.created_at + params.order_patience_seconds),
                                 self.config.version, rule, bonus.id if bonus and bonus.bonus_minor else None)
        return Decision(proposal, plain(memory), 'First ranked locally eligible candidate')

    def revise(self, context, memory, random):
        driver = next((d for d in context.drivers if d.driver_id == context.order.assignment.driver_id), None)
        if driver is None:
            return Decision(Stop('no current position observation'), plain(memory), 'Cannot revise without local observation')
        params, rule = self.config.resolve(driver.visible)
        # Only preceding own orders count. The target's boarding/journey are excluded.
        preceding = list(driver.own_order_ids)
        preceding = preceding[:preceding.index(context.order.id)] if context.order.id in preceding else preceding
        driver = freeze(plain(driver) | {'own_order_ids': preceding})
        eta = self.pickup_eta(context, driver, context.order.pickup, params)
        return Decision(EtaProposal(eta, self.config.version, rule), plain(memory), 'Revised remaining pickup time from own observations')

    def cancel(self, context, memory, random):
        params, _ = self.config.resolve(context.visible)
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
