Scenario 2: Flat-Fee Bonus vs. Multiplicative Surge Under Distance Heterogeneity (Straightforward)
Business or Research Question
How do distinct surge pricing structures (a flat monetary bonus versus a percentage multiplier) sort short-haul and long-haul trips between competing platforms during localized demand spikes?
Starting Conditions
 * Duration: 6 simulation hours (covering a 2-hour morning surge spike from t = 2 to t = 4).
 * Population: 150 riders, 30 drivers.
 * Platforms: Platform Metro (uses a multiplicative surge engine) and Platform Urban (uses a flat monetary surge engine).
 * Participant Groups:
   * Short-Haul Riders (75 riders): Request trips with travel distances between 1 and 3 coordinate units.
   * Long-Haul Riders (75 riders): Request trips with travel distances between 8 and 12 coordinate units.
   * Drivers (30 drivers): Uniformly distributed at t = 0, multihoming across both platforms.
Description of Events and Rules
Baseline fares for both platforms are $3.00 base + $1.00 per unit. Between t = 2 and t = 4, demand doubles in the central coordinate zone (4,4) to (6,6).
 * Platform Metro triggers a 1.8\times multiplier on the total trip fare for all rides originating in the zone.
 * Platform Urban applies a flat +$5.00 surge fee to all rides originating in the zone, passing 100% of this fee to the driver.
Riders compare final quoted prices across both apps and choose the cheaper option; if prices are within $0.50 of each other, they select based on the lower pickup ETA. Drivers prioritize dispatches that maximize immediate expected net payout per minute.
Observable Checks
 * Verify that quoted prices on Platform Metro scale proportionally with distance during surge, while Platform Urban quotes reflect base fare plus exactly $5.00 regardless of trip length.
 * Confirm that between t = 2 and t = 4, Metro is consistently cheaper than Urban for 1-unit trips (1.8 \times \$4.00 = \$7.20 vs. \$4.00 + \$5.00 = \$9.00), while Urban is cheaper for 10-unit trips (1.8 \times \$13.00 = \$23.40 vs. \$13.00 + \$5.00 = \$18.00).
 * Verify that drivers who reject an incoming short trip on Urban to take a long trip on Metro do not incur phantom state locks on Urban.
 * Confirm that at t = 4.01, all surge pricing rules deactivate and base fare calculations resume instantaneously.
Main Modeling Challenge or Ambiguity
Determine whether rider decision logic can parse non-linear structural price differences across platforms simultaneously with pickup ETAs, rather than evaluating pricing through a single uniform sensitivity parameter.
