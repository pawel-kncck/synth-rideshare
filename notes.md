Values restrictions (raised up front):

scheduling: times must be finite and not in the past; unknown driver/rider ids are rejected
locations: (x, y) pairs of finite kilometres
clock: cannot move backwards; a run cannot end before the current time

Everything else (speed, fares, delays, shift length) is taken as given and
misbehaves in the obvious way if nonsensical, e.g. a zero speed divides by zero.
