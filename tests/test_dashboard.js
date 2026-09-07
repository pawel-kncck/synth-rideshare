const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const {TimeSelection, aggregate, summarize} = require('../report_dashboard.js');

const DAY = 86400;
const run = (end, initial = 0, startHour = 0) => ({
  initial_time_seconds: initial, end_time_seconds: end, start_hour: startHour,
  id: 'test-run', runtime_seconds: 1, time_scale: false
});
const observations = [
  {at_seconds: 0, counts: {searches: 2, covered_searches: 1, rider_sessions: 2,
    converted_sessions: 1, declined_sessions: 1, offers: 3, accepted_offers: 2, rejected_offers: 1, completed_orders: 1}},
  {at_seconds: DAY, counts: {searches: 8, covered_searches: 8, rider_sessions: 8,
    converted_sessions: 6, declined_sessions: 2, offers: 10, accepted_offers: 6, rejected_offers: 4, completed_orders: 3}},
  {at_seconds: 2 * DAY, counts: {completed_orders: 1}},
  {start_seconds: 0, end_seconds: 2 * DAY, metric: 'online_driver_seconds'},
  {start_seconds: DAY + 1800, end_seconds: DAY + 3600, metric: 'active_driver_seconds'}
];

test('weeks, days, and ranges navigate within available time and clip partial periods', () => {
  const view = new TimeSelection(run(15 * DAY + 3600), {start_weekday: 'Monday'});
  assert.equal(view.grain, 'week');
  assert.deepEqual(view.bounds(), [0, 7 * DAY]);
  assert.equal(view.canMove(-1), false);
  view.move(1);
  assert.deepEqual(view.bounds(), [7 * DAY, 14 * DAY]);
  view.move(1);
  assert.deepEqual(view.bounds(), [14 * DAY, 15 * DAY + 3600]);
  assert.equal(view.canMove(1), false);
  view.move(1);
  assert.equal(view.startDay, 14);
  view.setGrain('day');
  view.selectPeriod(15);
  assert.deepEqual(view.bounds(), [15 * DAY, 15 * DAY + 3600]);
  view.setRange(3, 5);
  view.move(1);
  assert.deepEqual(view.bounds(), [6 * DAY, 9 * DAY]);
  view.move(-1);
  assert.deepEqual(view.bounds(), [3 * DAY, 6 * DAY]);
  view.setRange(12, 14);
  view.move(1);
  assert.equal(view.startDay, 13);
  assert.equal(view.endDay, 15);
  assert.equal(view.canMove(1), false);
  assert.throws(() => view.setRange(4, 2));
  assert.throws(() => view.setRange(-1, 2));
});

test('calendar weeks respect the starting weekday and continued run offset', () => {
  const view = new TimeSelection(run(8 * DAY, 0, 6), {start_weekday: 'Friday'});
  assert.deepEqual(view.bounds(), [0, 3 * DAY - 6 * 3600]);
  assert.match(view.periods()[0].label, /Friday, day 1 – Sunday, day 3/);
  view.move(1);
  assert.equal(view.startDay, 3);
  assert.match(view.periods()[1].label, /Monday, day 4/);
  const continued = new TimeSelection(run(3 * DAY, DAY + 1234, 6));
  continued.setGrain('day');
  assert.deepEqual(continued.bounds(), [DAY + 1234, 2 * DAY - 6 * 3600]);
  assert.equal(continued.dayLabel(1), 'day 2');
});

test('single-day and zero-duration runs have valid selections with disabled arrows', () => {
  for (const end of [0, 3600]) {
    const view = new TimeSelection(run(end, 0, 6));
    assert.equal(view.grain, 'day');
    assert.deepEqual(view.bounds(), [0, end]);
    assert.equal(view.canMove(1), false);
    assert.equal(view.canMove(-1), false);
    for (const grain of ['week', 'range', 'day']) {
      view.setGrain(grain);
      assert.deepEqual(view.bounds(), [0, end]);
    }
  }
  const rows = aggregate([{at_seconds: 0, counts: {completed_orders: 1}}], 0, 0, 15, 0, 6);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].completed_orders, 1);
});

test('midnight belongs to one day; final boundary and clipped driver time remain exact', () => {
  const first = aggregate(observations, 0, DAY, 60, 2 * DAY, 0);
  const second = aggregate(observations, DAY, 2 * DAY, 60, 2 * DAY, 0);
  assert.equal(summarize(first).completed_orders, 1);
  assert.equal(summarize(second).completed_orders, 4);
  assert.equal(second.at(-1).cumulative_completed_orders, 4);
  assert.equal(summarize(first).coverage_pct, 50);
  assert.equal(summarize(second).coverage_pct, 100);
  assert.equal(summarize([...first, ...second]).coverage_pct, 90);
  assert.equal(summarize(second).online_driver_hours, 24);
  assert.equal(summarize(second).active_driver_hours, .5);
  const partial = aggregate(observations, DAY + 1900, DAY + 2000, 15, 2 * DAY, 0);
  assert.equal(partial[0].active_driver_seconds, 100);
  assert.equal(partial[0].online_driver_seconds, 100);
  assert.equal(partial[0].utilization_pct, 100);
  assert.equal(partial[0].coverage_pct, null);
});

test('changing chart intervals preserves weighted totals for the selected period', () => {
  const expected = summarize(aggregate(observations, 0, 2 * DAY, 60, 2 * DAY, 0));
  for (const interval of [5, 15, 30]) {
    assert.deepEqual(summarize(aggregate(observations, 0, 2 * DAY, interval, 2 * DAY, 0)), expected);
  }
});

function dashboardHarness() {
  const template = fs.readFileSync(path.join(__dirname, '../report_template.html'), 'utf8');
  const payload = {configuration: {run: run(2 * DAY), simulation: {rider_count: 10, driver_count: 1, seed: 0},
    scenario: {start_weekday: 'Monday'}, report: {default_interval_minutes: 60}}, observations};
  const clicks = [], draws = [], downloads = [];
  class Element {
    constructor() { this.value = ''; this.listeners = {}; this.textContent = ''; }
    addEventListener(event, cb) { this.listeners[event] = cb; }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this[key] = value; }
    click() { clicks.push(this); }
    remove() {}
  }
  const elements = new Map([...template.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  elements.get('report-data').textContent = JSON.stringify(payload);
  elements.get('completion-mode').value = 'interval';
  const radios = ['week', 'day', 'range'].map(grain => {
    const input = elements.get(`grain-${grain}`); input.value = grain; return input;
  });
  const context = vm.createContext({
    document: {
      getElementById(id) { assert.ok(elements.has(id), `Missing element ${id}`); return elements.get(id); },
      querySelectorAll() { return radios; }, createElement() { return new Element(); }, body: {appendChild() {}}
    },
    Plotly: {react(...args) { draws.push(args); }}, Date, Blob,
    URL: {createObjectURL(blob) { downloads.push(blob); return 'blob:unit-test'; }, revokeObjectURL() {}},
    setTimeout() {}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../report_dashboard.js'), 'utf8'), context);
  vm.runInContext([...template.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1], context);
  const change = (id, value, event = 'change') => {
    if (value !== undefined) elements.get(id).value = value;
    elements.get(id).listeners[event]();
  };
  return {elements, draws, downloads, clicks, change};
}

test('top time controls update every card, all chart axes, cumulative totals and CSV', async () => {
  const {elements, draws, downloads, clicks, change} = dashboardHarness();
  assert.equal(elements.get('coverage-value').textContent, '90.00%');
  change('grain-day', 'day');
  assert.equal(elements.get('coverage-value').textContent, '50.00%');
  assert.equal(elements.get('sessions-value').textContent, '2');
  assert.equal(elements.get('previous-period').disabled, true);
  change('next-period', undefined, 'click');
  assert.equal(elements.get('coverage-value').textContent, '100.00%');
  assert.equal(elements.get('utilization-value').textContent, '2.08%');
  assert.equal(elements.get('sessions-value').textContent, '8');
  assert.equal(elements.get('conversion-value').textContent, '75.00%');
  assert.equal(elements.get('acceptance-value').textContent, '60.00%');
  assert.equal(elements.get('completed-value').textContent, '4');
  assert.equal(elements.get('next-period').disabled, true);
  change('completion-mode', 'cumulative');
  change('interval', '15');
  const [, traces, layout] = draws.at(-1);
  assert.equal(traces.length, 6);
  assert.equal(traces[0].y.length, 96);
  assert.equal(traces.at(-1).y.at(-1), 4);
  for (const key of ['xaxis', 'xaxis2', 'xaxis3', 'xaxis4', 'xaxis5']) {
    assert.equal(layout[key].range[0], '2000-01-04T00:00:00.000Z');
    assert.equal(layout[key].range[1], '2000-01-05T00:00:00.000Z');
  }
  change('download-csv', undefined, 'click');
  const csv = await downloads[0].text();
  assert.equal(clicks.at(-1).download, 'test-run-days2-2-15min.csv');
  const lines = csv.trim().split('\r\n');
  assert.equal(lines.length, 97);
  const columns = lines[0].split(',');
  assert.equal(lines[1].split(',')[columns.indexOf('start_seconds')], '"86400"');
  assert.equal(lines.at(-1).split(',')[columns.indexOf('cumulative_completed_orders')], '"4"');
  change('grain-range', 'range');
  assert.equal(elements.get('range-picker').hidden, false);
  assert.equal(elements.get('period-picker').hidden, true);
  change('range-end', '0'); // Automatically keep From <= Through.
  assert.equal(elements.get('range-start').value, '0');
  change('range-end', '1');
  assert.equal(elements.get('coverage-value').textContent, '90.00%');
  assert.equal(elements.get('completed-value').textContent, '5');
  assert.equal(elements.get('previous-period').disabled, true);
  assert.equal(elements.get('next-period').disabled, true);
});
