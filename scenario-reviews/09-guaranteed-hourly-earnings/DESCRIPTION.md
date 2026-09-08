Scenario 9: Cross-App Exploitation of Guaranteed Hourly Driver Earnings (Difficult)
Business or Research Question
Can multihoming drivers strategically exploit one platform’s minimum hourly wage guarantee (conditioned on high acceptance rates) by intentionally accepting remote rides on a rival platform to collect idle subsidies while avoiding actual driving?
Starting Conditions
 * Duration: 16 simulation hours.
 * Population: 150 riders, 30 drivers.
 * Platforms: Platform Steady (offers hourly guarantees) and Platform Flash (spot-market pricing).
 * Participant Groups:
   * Riders: Generate demand across the entire map.
   * Strategic Drivers (15 drivers): Highly optimizing multihomers looking to maximize total payout per hour.
   * Honest Drivers (15 drivers): Single-home exclusively on Platform Steady.
Description of Events and Rules
Platform Steady offers a promotion: "Earn a guaranteed $30.00 per clock hour online, provided you maintain an acceptance rate of \ge 90\% on all dispatches sent to you during that hour." If gross trip earnings fall below $30.00 in an hour, Steady tops up the difference.
Platform Flash operates standard spot dispatch with no guarantees, but experiences periodic high surges (2.5\times to 3.5\times) in fringe zones.
Strategic drivers discover an exploit:
 * They keep Platform Steady online in low-demand coordinates where dispatch requests are rare, ensuring their acceptance rate remains at 100% with zero effort, qualifying for the $30/hr top-up.
 * Simultaneously, they monitor Platform Flash for massive surge rides.
 * If Platform Steady sends a dispatch while they are fulfilling a Flash ride, their acceptance rate on Steady drops, risking the guarantee. Strategic drivers must decide whether to reject Flash rides or physically position themselves where Steady's dispatch algorithm cannot find them.
+-------------------------------------------------------------+
| Low-Demand Fringe Zone                                      |
| Strategic Driver stays online on Steady (0 rides received)  |
| --> Steady Top-up triggered: Pays $30/hr idle subsidy       |
|                                                             |
| Meanwhile, Flash triggers $3.5x Surge in adjacent sector:   |
| Strategic Driver snipes Flash trip while Steady idles       |
+-------------------------------------------------------------+

Observable Checks
 * Verify that Platform Steady calculates acceptance rates strictly within designated 60-minute evaluation windows, correctly topping up drivers whose organic fare earnings are below $30.00.
 * Verify that a driver who accepts a ride on Flash is flagged as "unavailable" by Steady's dispatch engine, preventing ghost double-booking.
 * Confirm that if Steady dispatches a ride to a driver who fails to accept within the timeout (because they were executing a Flash ride), Steady increments the driver's rejected-dispatch counter and disqualifies them from that hour’s top-up.
 * Track whether total driver payout on Steady includes top-up subsidies as a separate ledger entry from direct trip fare earnings.
Main Modeling Challenge or Ambiguity
Test whether the simulator can model complex conditional incentive schemes (contingent on temporal acceptance rates, online durations, and minimum thresholds) across drivers whose physical state is simultaneously being altered by an external competing platform.
