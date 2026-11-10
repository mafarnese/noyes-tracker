# Which trip is a train? (findings, Oct 27 – Nov 10, 2026)

`trip_outcomes.py` labels every scheduled southbound slot at Noyes with what happened.
`trip_outcomes.csv` is its output: one row per slot (slot time, its run, CTA fault flag,
outcome, the run of the train that served it, when it passed Noyes, minutes vs. slot,
when that train arrived at Linden).

## What run numbers are
- A run number is an operator's work assignment, not a train or a trip, and is never reused
  within a day (chicago-l.org, "Run Numbers").
- `isFlt` means the scheduled departure hasn't happened yet, not that it's cancelled
  (CTA Train Tracker API docs).
- The API exposes no train or car ID, so identity has to be inferred.

## What the data shows
1. **Labels mostly survive the turnaround.** 72% of departures carry the label the
   train arrived at Linden with (the rest are mostly yard pull-outs).
2. **CTA relabels trains after departure about 4 times a day**, sometimes several trains at
   once (Mon 11/9, 5:33:27pm: three trains between Noyes and Armitage shifted labels by one).
   These are bookkeeping; no train moves. Ignore them.
3. **Missing runs get covered by pulling the next crews up a slot.** When a run never
   appears (e.g. run 511 on 11/3 and 11/9 mornings), each following crew leaves one slot
   early. By run number those trains look ~10 min early; by time they're on schedule.
   64 trains "early" by >4 min: 66% pass within 1.5 min of the previous slot (chance ≈ 24%).
   The 54 identified pull-ups pass a median of 0.0 min from the slot they cover.
4. **Late trains are genuinely late, not reassigned to the next trip.** 65 trains "late" by
   >4 min: 25% land within 1.5 min of the next slot, the same as chance (24%). Holds across
   thresholds of 3–5 min and windows of ±1–2 min. In 8 of the 16 that did land near the
   next slot, that slot's own run also came separately.

## Labelling rules (used by trip_outcomes.py)
- Identity = the run number a train carries when it leaves Linden; later relabels are ignored.
- Passes > 2.5 min before its own slot and within 3 min of the previous slot → pull-up.
- Otherwise it serves its own run's slot (late or early).
- 1000-series / unscheduled trains, and leftovers, fill the nearest unserved slot (≤5–6 min).
- Nobody serves the slot → no-show.

## Results with these rules (661 slots)
- 533 served by their own run, 54 by pull-ups, 30 by extra/leftover trains, **44 no-shows (1 in 15)**.
- 3:30–5pm: 1 in 8 slots is a no-show; rest of the day: 1 in 19.
- CTA fault flag on the slot: 16% no-show rate; no flag: 2.3%.

## Uncertainty
The late-vs-reassigned test can't rule out that a small share of late trains (up to roughly
1 in 5, the upper end of the 95% interval) were really reassignments. For a single late train
that happens to land on the next slot, the best estimate is ~95% "late", with ~50% as a
worst case. Physically the two readings are identical (same trains at the same times);
only which slot gets called the no-show differs.
