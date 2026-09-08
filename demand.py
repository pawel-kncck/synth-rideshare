"""Configurable weekly peaks for scheduling rider sessions in scenarios."""

import math
import random
from dataclasses import dataclass

from policy_contracts import finite_number


@dataclass(frozen=True)
class PeakPeriod:
    """Half-open local clock window; weekdays name the day the peak starts.

    Monday is 0, Sunday is 6. A 21:00–03:00 Friday peak includes Saturday
    before 03:00. Overlapping peaks use the greatest multiplier.
    """

    name: str
    weekdays: tuple
    start_hour: int
    end_hour: int
    multiplier: float

    def __post_init__(self):
        if not self.weekdays or any(type(day) is not int or not 0 <= day <= 6 for day in self.weekdays):
            raise ValueError("weekdays must contain day numbers from 0 (Monday) to 6 (Sunday)")
        if (type(self.start_hour) is not int or not 0 <= self.start_hour < 24
                or type(self.end_hour) is not int or not 0 <= self.end_hour <= 24
                or self.start_hour == self.end_hour):
            raise ValueError("peak hours must be distinct whole hours: start 0–23, end 0–24")
        finite_number(self.multiplier, "multiplier", minimum=1)

    def contains(self, weekday, hour):
        if self.start_hour < self.end_hour:
            return weekday in self.weekdays and self.start_hour <= hour < self.end_hour
        return ((weekday in self.weekdays and hour >= self.start_hour)
                or ((weekday - 1) % 7 in self.weekdays and hour < self.end_hour))


@dataclass(frozen=True)
class WeeklyDemandProfile:
    """Relative session arrival intensity, independent of conversion decisions."""

    peaks: tuple = (
        PeakPeriod("Morning commute", (0, 1, 2, 3, 4), 7, 9, 2.5),
        PeakPeriod("Afternoon commute", (0, 1, 2, 3, 4), 16, 19, 2.8),
        PeakPeriod("Friday and Saturday night", (4, 5), 21, 3, 3.0),
    )

    def weight_at(self, weekday, hour):
        return max((peak.multiplier for peak in self.peaks if peak.contains(weekday, hour)), default=1.0)

    def sample_session_times(self, session_count, duration_seconds, *, seed=0,
                             start_weekday=0, start_hour=0):
        """Sample a fixed number of arrivals, weighted by clock time, then sort.

        Off-peak weight is 1. A 3x peak has three times as many expected
        sessions per hour. Partial first/last hours retain their exact weight.
        Returned times are seconds after this scenario's start.
        """
        if type(session_count) is not int or session_count < 0:
            raise ValueError("session_count must be a nonnegative integer")
        finite_number(duration_seconds, "duration_seconds", strictly_positive=True)
        if type(start_weekday) is not int or not 0 <= start_weekday <= 6:
            raise ValueError("start_weekday must be 0 (Monday) through 6 (Sunday)")
        finite_number(start_hour, "start_hour", maximum=24)
        if start_hour == 24:
            raise ValueError("start_hour must be less than 24")
        windows, weights = [], []
        first_hour = math.floor(start_hour)
        hour_count = math.ceil(((start_hour - first_hour) * 3600 + duration_seconds) / 3600)
        # Integer hour steps avoid floating-point drift at fractional starts.
        for offset in range(hour_count):
            clock_hour = first_hour + offset
            left = max(0, (clock_hour - start_hour) * 3600)
            right = min(duration_seconds, (clock_hour + 1 - start_hour) * 3600)
            weekday = (start_weekday + math.floor(clock_hour / 24)) % 7
            windows.append((left, right))
            weights.append((right - left) * self.weight_at(weekday, clock_hour % 24))
        rng = random.Random(seed)
        selected = rng.choices(windows, weights=weights, k=session_count)
        return sorted(left + rng.random() * (right - left) for left, right in selected)
