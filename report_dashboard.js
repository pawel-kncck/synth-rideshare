/* Pure selection and aggregation logic, embedded into each offline report. */
const Dashboard = (() => {
  const DAY = 86400;
  const countKeys = [
    'searches', 'covered_searches', 'completed_orders', 'rider_sessions',
    'converted_sessions', 'undecided_sessions', 'declined_sessions', 'unavailable_sessions',
    'offers', 'accepted_offers', 'rejected_offers', 'expired_offers', 'canceled_offers', 'failed_offers', 'pending_offers',
    'online_driver_seconds', 'active_driver_seconds'
  ];
  const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const ratio = (numerator, denominator) => denominator ? 100 * numerator / denominator : null;
  const emptyCounts = () => Object.fromEntries(countKeys.map(key => [key, 0]));
  const clock = seconds => {
    const day = Math.floor(seconds / DAY);
    const hour = Math.floor(seconds / 3600) % 24;
    const minute = Math.floor(seconds / 60) % 60;
    const second = Math.floor(seconds) % 60;
    return `${day ? `D+${day} ` : ''}${[hour, minute, second].map(n => String(n).padStart(2, '0')).join(':')}`;
  };

  function aggregate(observations, start, end, minutes, runEnd, startHour) {
    if (![start, end, minutes, runEnd, startHour].every(Number.isFinite) || end < start || minutes <= 0) {
      throw new Error('Invalid dashboard time window');
    }
    const width = minutes * 60;
    const count = Math.max(1, Math.ceil((end - start) / width));
    const rows = Array.from({length: count}, (_, i) => {
      const left = start + i * width, right = Math.min(end, left + width);
      return {start_seconds: left, end_seconds: right,
        clock_start: clock(startHour * 3600 + left), clock_end: clock(startHour * 3600 + right),
        ...emptyCounts()};
    });
    const indexAt = at => Math.min(count - 1, Math.max(0, Math.floor((at - start) / width)));
    for (const observation of observations) {
      if (observation.counts) {
        const at = observation.at_seconds;
        // Adjacent selections never double-count midnight. The saved run's
        // final instant belongs to its last period, including zero-time runs.
        if (at < start || at > end || (at === end && end !== runEnd)) continue;
        const row = rows[indexAt(at)];
        for (const [key, value] of Object.entries(observation.counts)) row[key] += value;
      } else {
        const left = Math.max(start, observation.start_seconds);
        const right = Math.min(end, observation.end_seconds);
        if (right <= left) continue;
        for (let i = indexAt(left); i <= indexAt(right); i++) {
          rows[i][observation.metric] += Math.max(0,
            Math.min(right, rows[i].end_seconds) - Math.max(left, rows[i].start_seconds));
        }
      }
    }
    let cumulative = 0;
    for (const row of rows) {
      row.coverage_pct = ratio(row.covered_searches, row.searches);
      row.utilization_pct = ratio(row.active_driver_seconds, row.online_driver_seconds);
      row.session_to_order_pct = ratio(row.converted_sessions, row.rider_sessions);
      row.offer_acceptance_pct = ratio(row.accepted_offers, row.offers);
      row.idle_driver_seconds = row.online_driver_seconds - row.active_driver_seconds;
      row.cumulative_completed_orders = cumulative += row.completed_orders;
    }
    return rows;
  }

  function summarize(rows) {
    const totals = emptyCounts();
    for (const row of rows) for (const key of countKeys) totals[key] += row[key];
    return {...totals,
      coverage_pct: ratio(totals.covered_searches, totals.searches),
      utilization_pct: ratio(totals.active_driver_seconds, totals.online_driver_seconds),
      session_to_order_pct: ratio(totals.converted_sessions, totals.rider_sessions),
      offer_acceptance_pct: ratio(totals.accepted_offers, totals.offers),
      online_driver_hours: totals.online_driver_seconds / 3600,
      active_driver_hours: totals.active_driver_seconds / 3600,
      idle_driver_hours: (totals.online_driver_seconds - totals.active_driver_seconds) / 3600};
  }

  class TimeSelection {
    constructor(run, scenario = {}) {
      this.run = run;
      this.clockOffset = run.start_hour * 3600;
      this.weekday = Number.isInteger(scenario.start_weekday) ? scenario.start_weekday : weekdays.indexOf(scenario.start_weekday);
      this.firstDay = Math.floor((this.clockOffset + run.initial_time_seconds) / DAY);
      this.lastDay = Math.max(this.firstDay, Math.ceil((this.clockOffset + run.end_time_seconds) / DAY) - 1);
      this.startDay = this.firstDay;
      this.endDay = this.lastDay;
      this.setGrain(this.firstDay === this.lastDay ? 'day' : 'week');
    }
    weekOf(day) { return Math.floor((day + Math.max(0, this.weekday)) / 7); }
    dayLabel(day) {
      const weekday = this.weekday < 0 ? '' : `${weekdays[(day + this.weekday) % 7]}, `;
      return `${weekday}day ${day + 1}`;
    }
    weekBounds(week) {
      const start = week * 7 - Math.max(0, this.weekday);
      return [Math.max(this.firstDay, start), Math.min(this.lastDay, start + 6)];
    }
    periods() {
      if (this.grain === 'day') {
        return Array.from({length: this.lastDay - this.firstDay + 1}, (_, i) => {
          const day = this.firstDay + i;
          return {value: day, label: this.dayLabel(day)};
        });
      }
      const first = this.weekOf(this.firstDay), last = this.weekOf(this.lastDay);
      return Array.from({length: last - first + 1}, (_, i) => {
        const week = first + i, [start, end] = this.weekBounds(week);
        return {value: week, label: `Week ${i + 1} · ${this.dayLabel(start)} – ${this.dayLabel(end)}`};
      });
    }
    setGrain(grain) {
      if (!['week', 'day', 'range'].includes(grain)) throw new Error('Unknown time grain');
      this.grain = grain;
      if (grain === 'day') this.endDay = this.startDay;
      if (grain === 'week') [this.startDay, this.endDay] = this.weekBounds(this.weekOf(this.startDay));
    }
    selectPeriod(value) {
      if (!this.periods().some(period => period.value === value)) throw new Error('Period outside run');
      if (this.grain === 'day') this.startDay = this.endDay = value;
      else [this.startDay, this.endDay] = this.weekBounds(value);
    }
    setRange(start, end) {
      if (!Number.isInteger(start) || !Number.isInteger(end) || start > end || start < this.firstDay || end > this.lastDay) {
        throw new Error('Range outside run');
      }
      this.grain = 'range';
      this.startDay = start;
      this.endDay = end;
    }
    canMove(direction) { return direction < 0 ? this.startDay > this.firstDay : this.endDay < this.lastDay; }
    move(direction) {
      if (!this.canMove(direction)) return;
      if (this.grain === 'week') {
        this.selectPeriod(this.weekOf(this.startDay) + direction);
      } else if (this.grain === 'day') {
        this.selectPeriod(this.startDay + direction);
      } else {
        const span = this.endDay - this.startDay + 1;
        const next = Math.max(this.firstDay, Math.min(this.lastDay - span + 1, this.startDay + direction * span));
        this.setRange(next, next + span - 1);
      }
    }
    bounds() {
      return [Math.max(this.run.initial_time_seconds, this.startDay * DAY - this.clockOffset),
        Math.min(this.run.end_time_seconds, (this.endDay + 1) * DAY - this.clockOffset)];
    }
    rows(observations, minutes) {
      return aggregate(observations, ...this.bounds(), minutes, this.run.end_time_seconds, this.run.start_hour);
    }
  }
  return {aggregate, summarize, TimeSelection, clock};
})();

if (typeof module !== 'undefined' && module.exports) module.exports = Dashboard;
