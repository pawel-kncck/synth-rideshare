"""A single-platform ride-hailing simulation composed on the marketplace engine.

`Simulation` is the scenario-facing entry point. It builds one physical
market with one platform, Rebu, and plays three roles on top of the
engine's commands and notifications:

- the scenario and runner: who shows up when, playback, logs, reports;
- Rebu's marketplace policy: quotes, nearest-driver dispatch, offer terms;
- participants' behavior: whether a rider orders and a driver accepts.

The engine (`marketplace_engine.py`) owns positions, commitments, physical
service, settlements, and legality. Nothing here moves a car or finishes a
ride directly; it asks, and the engine decides what is possible.
"""

import logging
import math
import random
import time
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp

from behavior import decision_probability, finite_number
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

    platform_id = "rebu"
    offer_timeout_seconds = 10
    max_offers_per_order = 5

    def __init__(
        self,
        driver_count=10,
        rider_count=100,
        seed=0,
        speed_kmh=30,
        base_fare=2.0,
        price_per_km=1.5,
        commission_fraction=0.0,
        boarding_delay_seconds=30,
        order_delay_seconds=5,
        accept_delay_seconds=3,
        rider_order_probability=0.55,
        driver_acceptance_probability=0.70,
        rider_price_sensitivity=1.0,
        driver_price_sensitivity=1.0,
        rider_eta_sensitivity=0.5,
        driver_eta_sensitivity=0.5,
        reference_price=10.0,
        reference_eta_seconds=300,
    ):
        for name, value in (
            ("rider_order_probability", rider_order_probability),
            ("driver_acceptance_probability", driver_acceptance_probability),
            ("commission_fraction", commission_fraction),
        ):
            finite_number(value, name, maximum=1)
            setattr(self, name, value)
        for name, value in (
            ("rider_price_sensitivity", rider_price_sensitivity),
            ("driver_price_sensitivity", driver_price_sensitivity),
            ("rider_eta_sensitivity", rider_eta_sensitivity),
            ("driver_eta_sensitivity", driver_eta_sensitivity),
            ("base_fare", base_fare),
            ("price_per_km", price_per_km),
        ):
            finite_number(value, name)
            setattr(self, name, value)
        for name, value in (("reference_price", reference_price),
                            ("reference_eta_seconds", reference_eta_seconds)):
            finite_number(value, name, strictly_positive=True)
            setattr(self, name, value)
        self.seed = seed
        # Behavior never consumes the scenario's or the ID generator's RNG.
        self._decision_rng = random.Random(seed)
        self.speed_kmh = speed_kmh
        self.boarding_delay_seconds = boarding_delay_seconds
        # How long a rider thinks about a quote before ordering or leaving.
        self.order_delay_seconds = order_delay_seconds
        # How long a driver takes to answer an offer. At or past the offer
        # timeout the offer expires first and the next driver is tried.
        self.accept_delay_seconds = accept_delay_seconds

        self.world = World(speed_kmh=speed_kmh, boarding_seconds=boarding_delay_seconds)
        self._seconds_per_km = 3600 / self.world.speed_kmh
        registry = HandlerRegistry()
        self.engine = MarketplaceEngine(self.world, registry)
        for kind, handler in (
            ("driver_session.start", self._on_driver_session_start),
            ("driver_session.end_shift", self._on_shift_end),
            ("rider_session.start", self._on_rider_session_start),
            ("rider_session.decide", self._on_quote_decision),
            ("offer.respond", self._on_offer_response),
        ):
            registry.register(kind, handler)
        self.scheduler = Scheduler(registry)
        self.engine.bind(self.scheduler)
        self.engine.add_listener(self._on_notification)

        self.engine.add_platform(self.platform_id, "Rebu")
        self.drivers = generate_ids(9, driver_count, 1_000_000_000, seed)
        self.riders = generate_ids(8, rider_count, 10_000_000_000, seed)
        for driver_id in self.drivers:
            # One car per driver, registered with the only platform; it shares the driver's id.
            self.engine.add_car(driver_id, {self.platform_id})
            self.engine.add_driver(driver_id, {self.platform_id}, car_id=driver_id)
        for rider_id in self.riders:
            self.engine.add_rider(rider_id, {self.platform_id})

        self.decisions = []  # behavior diagnostics: each draw with its probability
        self.scenario_parameters = {}
        self.session_schedule = []
        self.log_path = None
        self.run_directory = None
        self.report_path = None
        # A private logger keeps lifecycle events out of the console, even if
        # the application using this simulator configures the root logger.
        self._logger = logging.Logger(__name__, level=logging.INFO)
        self._logger.propagate = False

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
            "events": self.scheduler.processed_count,
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
        distance = sum(engine.quotes[order.quote_id].distance_km for order in completed)
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

    def _draw_decision(self, probability):
        return probability == 1 or (probability > 0 and self._decision_rng.random() < probability)

    # ----------------------------------------------------------------------
    # Scenario events: shifts and trips begin
    # ----------------------------------------------------------------------

    def _on_driver_session_start(self, event):
        driver_id, shift_seconds = event.payload["driver_id"], event.payload["shift_seconds"]
        location = tuple(event.payload["location"])
        shift = self.engine.start_shift(driver_id, location)
        self.engine.open_app("driver", driver_id, self.platform_id)
        self._log(f"Driver {driver_id} started a shift at {location}")
        if shift_seconds is not None:
            self._schedule(shift_seconds, "driver_session.end_shift",
                           driver_id=driver_id, shift_id=shift.id)

    def _on_shift_end(self, event):
        driver = self.engine.drivers[event.payload["driver_id"]]
        if driver.shift_id != event.payload["shift_id"]:
            return
        shift = self.engine.end_shift(driver.id)
        if shift.ended_at is None:
            self._log(f"Driver {driver.id} finishes the shift after order(s) {driver.commitments}")

    def _on_rider_session_start(self, event):
        rider_id = event.payload["rider_id"]
        location, destination = tuple(event.payload["location"]), tuple(event.payload["destination"])
        intent = self.engine.begin_intent(rider_id, location, destination)
        self._log(f"Rider {rider_id} wants to travel from {location} to {destination} (trip {intent.id})")
        # Opening the app is what asks Rebu for a quote.
        self.engine.open_app("rider", rider_id, self.platform_id)

    # ----------------------------------------------------------------------
    # Notification routing: the platform and the participants react here
    # ----------------------------------------------------------------------

    def _on_notification(self, notification):
        kind, data = notification.kind, notification.data
        if notification.audience == "platform":
            if kind == "rider_app_opened" and data["request"] is not None:
                self._platform_quote(data["request"])
            elif kind == "order_created":
                self._platform_dispatch(data["order_id"])
            elif kind == "offer_resolved" and data["state"] != "accepted":
                self._platform_dispatch(data["order_id"])
            elif kind == "order_completed":
                order = self.engine.orders[data["order_id"]]
                settlement = self.engine.settlements[data["settlement_id"]]
                self._log(
                    f"Order {order.id} completed: rider {order.rider_id} dropped off at "
                    f"{order.destination} by driver {order.assignment.driver_id}, "
                    f"{self.current_time - order.created_at:.0f}s after ordering, "
                    f"paid {self._money(settlement.rider_payment_minor):.2f}, "
                    f"driver payout {self._money(settlement.driver_payout_minor):.2f}"
                )
        elif notification.audience == "driver":
            if kind == "offer_received":
                self._schedule(self.accept_delay_seconds, "offer.respond", offer_id=data["offer_id"])
            elif kind == "commitment_added":
                self._log(f"Driver {notification.audience_id} accepted order {data['order_id']}"
                          + (" and queued it behind the current ride" if data["position"] > 1 else ""))
            elif kind == "arrived_at_pickup":
                self._log(f"Driver {notification.audience_id} arrived at pickup for order {data['order_id']}")
            elif kind == "rider_boarded":
                self._log(f"Rider boarded with driver {notification.audience_id} for order {data['order_id']}")
            elif kind == "shift_ended":
                self._log(f"Driver {notification.audience_id} went offline at {data['location']}")
        elif notification.audience == "rider":
            if kind == "quote_received":
                self._schedule(self.order_delay_seconds, "rider_session.decide",
                               intent_id=data["intent_id"], quote_id=data["quote_id"])
            elif kind == "order_created":
                order = self.engine.orders[data["order_id"]]
                self._log(f"Order {order.id} placed by rider {order.rider_id}: {order.pickup} to "
                          f"{order.destination}, fare {self._money(order.fare.rider_payment_minor):.2f}")
            elif kind == "order_canceled":
                # The default rider does not retry: they leave once the platform gives up.
                self.engine.end_intent(self.engine.orders[data["order_id"]].intent_id, data["reason"])
            elif kind == "intent_ended":
                self.engine.close_app("rider", notification.audience_id, self.platform_id)

    # ----------------------------------------------------------------------
    # Rebu's marketplace policy: quotes, estimates, and dispatch
    # ----------------------------------------------------------------------

    def calculate_duration(self, distance_km):
        """Rebu's travel-time estimate: straight-line at the world speed."""
        return self.world.travel_seconds(distance_km)

    def calculate_price(self, distance_km):
        """Return the base fare plus the distance charge, in currency units."""
        return self.base_fare + distance_km * self.price_per_km

    def _platform_quote(self, request):
        """Price the trip and estimate the best pickup ETA from Rebu's own knowledge."""
        view = self.engine.platform_view(self.platform_id)
        distance_km = math.dist(request.origin, request.destination)
        eta_seconds = min(
            (self._estimated_pickup_eta(view, driver, request.origin)
             for driver in view.drivers() if self._is_candidate(driver)),
            default=None,
        )
        quote = self.engine.issue_quote(
            self.platform_id, request.intent_id, self.world.to_minor(self.calculate_price(distance_km)),
            distance_km=distance_km, duration_seconds=self.calculate_duration(distance_km),
            eta_seconds=eta_seconds,
        )
        if quote.drivers_available:
            self._log(f"Rider {request.rider_id} quoted {distance_km:.1f} km for "
                      f"{self._money(quote.fare.gross_minor):.2f}, nearest driver {eta_seconds:.0f}s away")
        else:
            self._log(f"Rider {request.rider_id} quoted {distance_km:.1f} km, no drivers available")

    def _is_candidate(self, driver):
        """Rebu's local eligibility: accepting, nothing pending here, and a free own slot."""
        return driver.accepting and not driver.pending_offer_ids and len(driver.own_order_ids) < 2

    def _estimated_pickup_eta(self, view, driver, pickup):
        """Remaining known Rebu service, then travel from where it ends to the pickup.

        Rebu knows only its own orders. If the driver is actually busy for
        another platform, that time is invisible and the estimate is optimistic.
        """
        position, seconds_per_km = driver.position, self._seconds_per_km
        if not driver.own_order_ids:
            return math.dist(position, pickup) * seconds_per_km
        remaining = 0.0
        for order_id in driver.own_order_ids:
            order = view.order(order_id)
            if "boarded" not in order.timeline:  # still to pick up, then board
                remaining += math.dist(position, order.pickup) * seconds_per_km + self.boarding_delay_seconds
                position = order.pickup
            remaining += math.dist(position, order.destination) * seconds_per_km
            position = order.destination
        return remaining + math.dist(position, pickup) * seconds_per_km

    def _platform_dispatch(self, order_id):
        """Offer to the nearest untried eligible driver, up to the attempt limit."""
        view = self.engine.platform_view(self.platform_id)
        order = view.order(order_id)
        if order.state != "open":
            return
        if len(order.offer_ids) >= self.max_offers_per_order:
            self._platform_gives_up(order)
            return
        tried = {view.offer(offer_id).driver_id for offer_id in order.offer_ids}
        best = None
        for driver in view.drivers():
            if driver.driver_id in tried or not self._is_candidate(driver):
                continue
            key = (self._estimated_pickup_eta(view, driver, order.pickup), driver.driver_id)
            if best is None or key < best[0]:
                best = (key, driver)
        if best is None:
            self._platform_gives_up(order)
            return
        (eta_seconds, _), driver = best
        payout_minor = self.world.to_minor(self._money(order.fare.gross_minor) * (1 - self.commission_fraction))
        offer = self.engine.create_offer(
            self.platform_id, order.id, driver.driver_id, payout_minor,
            expires_at=self.current_time + self.offer_timeout_seconds, eta_seconds=eta_seconds,
        )
        self._log(f"Offer {offer.id} for order {order.id} sent to driver {driver.driver_id}, "
                  f"payout {self._money(payout_minor):.2f}, estimated pickup in {eta_seconds:.0f}s")

    def _platform_gives_up(self, order):
        self.engine.cancel_order(order.id, by="platform", reason="no drivers accepted")
        self._log(f"Order {order.id} canceled: no drivers accepted; rider {order.rider_id} left")

    # ----------------------------------------------------------------------
    # Participant behavior: ordering a quote and answering an offer
    # ----------------------------------------------------------------------

    def rider_order_chance(self, quote):
        """Probability of ordering this quote; unavailable supply always gives zero."""
        if not quote.drivers_available:
            return 0.0
        return decision_probability(
            self.rider_order_probability, self._money(quote.fare.rider_payment_minor), quote.eta_seconds,
            self.reference_price, self.reference_eta_seconds,
            -self.rider_price_sensitivity, self.rider_eta_sensitivity,
        )

    def driver_acceptance_chance(self, offer):
        """Use the offered payout and the platform's displayed pickup ETA."""
        return decision_probability(
            self.driver_acceptance_probability, self._money(offer.payout.driver_payout_minor),
            offer.eta_seconds, self.reference_price, self.reference_eta_seconds,
            self.driver_price_sensitivity, self.driver_eta_sensitivity,
        )

    def _on_quote_decision(self, event):
        intent = self.engine.intents[event.payload["intent_id"]]
        if not intent.live or intent.live_order_id is not None:
            return
        quote = self.engine.quotes[event.payload["quote_id"]]
        probability = self.rider_order_chance(quote)
        ordered = self._draw_decision(probability)
        self.decisions.append({
            "type": "rider_decision", "at_seconds": self.current_time, "session_id": intent.id,
            "rider_id": intent.rider_id, "ordered": ordered, "probability": probability,
            "price": self._money(quote.fare.rider_payment_minor), "eta_seconds": quote.eta_seconds,
        })
        if ordered:
            self.engine.place_order(intent.id, quote.id)
        else:
            reason = "quote declined" if quote.drivers_available else "no drivers available"
            self._log(f"Rider {intent.rider_id} left: {reason} (order probability {probability:.3f})")
            self.engine.end_intent(intent.id, reason)

    def _on_offer_response(self, event):
        offer = self.engine.offers[event.payload["offer_id"]]
        if offer.state != "pending":
            return
        probability = self.driver_acceptance_chance(offer)
        accept = self._draw_decision(probability)
        self.decisions.append({
            "type": "driver_decision", "at_seconds": self.current_time, "offer_id": offer.id,
            "driver_id": offer.driver_id, "accept": accept, "probability": probability,
            "payout": self._money(offer.payout.driver_payout_minor), "eta_seconds": offer.eta_seconds,
        })
        disposition = self.engine.respond_to_offer(offer.id, accept)
        if disposition != "accepted":
            self._log(f"Driver {offer.driver_id} answered offer {offer.id} for order {offer.order_id}: "
                      f"{disposition} (acceptance probability {probability:.3f})")


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
