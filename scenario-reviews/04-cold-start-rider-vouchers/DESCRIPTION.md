Scenario 4: Cold-Start Market Entry via Subsidized Rider Vouchers (Intermediate)
Business or Research Question
Can a new platform break an incumbent's market lock-in using targeted burn-rate subsidies, and does user engagement persist when promotional discounts end?
Starting Conditions
 * Duration: 7 simulation days (168 hours).
 * Population: 250 riders, 50 drivers.
 * Platforms: Platform Dominant (incumbent) and Platform Challenger (launches at t = 24 hours).
 * Participant Groups:
   * Riders: 100% start with Platform Dominant installed. None have Platform Challenger installed at t = 0.
   * Drivers: 100% are active on Platform Dominant at t = 0.
Description of Events and Rules
Platform Dominant operates with standard pricing ($3.00 base + $1.50 per unit, 20% commission).
At t = 24, Platform Challenger enters the market:
 * Challenger Driver Terms: 0% commission for the first 72 hours of operation (ending t = 96), rising to 15% thereafter. Drivers install the Challenger app as soon as they learn of the 0% commission (modeled as a 10% adoption probability per hour while idle).
 * Challenger Rider Promotion: Between t = 24 and t = 96, Challenger offers a "First 3 Rides Free" voucher (up to $10 off per ride, platform absorbs the cost). Riders install Challenger if their quoted ETA on Dominant exceeds 8 minutes or if peer adoption in their geographic vicinity exceeds 20%.
 * At t = 96, Challenger ends all vouchers and switches to a 15% commission.
Observable Checks
 * Verify that between t = 0 and t = 24, zero dispatches, app installations, or ride completions occur on Platform Challenger.
 * Confirm that Challenger records negative net platform revenue between t = 24 and t = 96, reflecting driver payouts on subsidized rides with zero rider fare collection.
 * Verify that each rider who installs Challenger receives the $10 voucher on exactly their first three completed rides on Challenger, after which standard pricing applies.
 * Track whether app installation counts are decoupled from app usage counts (e.g., riders who have Challenger installed but choose Dominant due to ETA).
Main Modeling Challenge or Ambiguity
Investigate whether the platform can represent individual rider-level state progression (tracking "voucher ride 1, 2, 3") and dynamic mid-run app adoption cascades, as opposed to static population-wide parameter assignments.
