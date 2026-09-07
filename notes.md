Values restrictions (raised up front):

scheduling: times must be finite and not in the past; unknown driver/rider ids are rejected
locations: (x, y) pairs of finite kilometres
clock: cannot move backwards; a run cannot end before the current time
playback: time_scale must be a finite positive number or False (no sleep); zero and True are rejected

Everything else (speed, fares, delays, shift length) is taken as given and
misbehaves in the obvious way if nonsensical, e.g. a zero speed divides by zero.

Realism:
Not every session turns into an order --> in very healthy markets, session to order ratio is 50-60%
Not every offer gets accepted by the driver --> it varies hugely between markets, but it's generally within the 60-80% range
Both ratios reflect driver and rider decision. Those decision depend on price and ETA. For ETA both rider and driver prefer short time (time to pickup), for price - drivers prefer high prices, riders prefer low prices. 
There are peak hours. morning peak and afternoon peak on working days + friday and saturday night
