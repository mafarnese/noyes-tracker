#!/usr/bin/env python3
"""
Label every scheduled southbound Purple Line slot at Noyes with what actually happened.

Rules (derived from the Oct 27 - Nov 10 2026 data; see analysis/README.md):
  * A train's identity is the run number it carries when it LEAVES LINDEN.
    Relabels after departure are CTA bookkeeping and are ignored.
  * Pull-up: a train that passes Noyes > 2.5 min before its own run's slot and
    within 3 min of the previous slot is serving that previous slot (dispatch filled
    a gap with the next crew).
  * Otherwise a train serves its own run's slot, early or late.
  * Extra trains (1000-series runs, or runs with no slot) serve the nearest unserved
    slot within 5 min; otherwise they are counted as extra service.
  * Trains left over after that (own slot taken by a closer train) fill the nearest
    unserved slot within 6 min.
  * A slot nobody serves is a no-show.
Usage: python3 analysis/trip_outcomes.py [data_dir] [out_csv]
"""
import csv, collections, sys, bisect
from datetime import datetime as D, timedelta as T
P = D.fromisoformat
data = sys.argv[1] if len(sys.argv) > 1 else "data"
out = sys.argv[2] if len(sys.argv) > 2 else "analysis/trip_outcomes.csv"

pos = sorted(csv.DictReader(open(f"{data}/purple_positions.csv")), key=lambda p: p["poll_ts"])
arr = [a for a in csv.DictReader(open(f"{data}/noyes_arrivals.csv")) if a["trDr"] == "5"]
polls = sorted(P(r["poll_ts"]) for r in csv.DictReader(open(f"{data}/polls.csv")))
cov = {p.replace(second=0, microsecond=0) for p in polls}
def covered(t, a=6, b=8):
    m = t.replace(second=0, microsecond=0)
    return all(m + T(minutes=k) in cov for k in range(-a, b + 1))

# --- scheduled slots at Noyes: first-listed scheduled time per run trip
sch = collections.defaultdict(list)
for a in sorted(arr, key=lambda a: a["poll_ts"]):
    if a["isSch"] == "1": sch[a["rn"]].append(a)
slots = []
for rn, rs in sch.items():
    cur, last = [], None
    for r in rs + [None]:
        t = P(r["poll_ts"]) if r else None
        if r is None or (last and t - last > T(minutes=25)):
            if cur: slots.append(dict(slot=P(cur[0]["arrT"]), rn=rn, fault=any(x["isFlt"] == "1" for x in cur)))
            cur = []
        if r: cur.append(r); last = t
slots = sorted((s for s in slots if covered(s["slot"])), key=lambda s: s["slot"])

# --- departures from Linden with the label carried at departure, and the Noyes pass time
by = collections.defaultdict(list)
for p in pos: by[p["rn"]].append(p)
nb_arr = collections.defaultdict(list); deps = []
for rn, rows in by.items():
    for i, p in enumerate(rows):
        t = P(p["poll_ts"])
        if p["trDr"] == "1" and p["nextStaNm"] == "Linden":
            nx = rows[i + 1] if i + 1 < len(rows) else None
            if not nx or nx["nextStaNm"] != "Linden" or nx["trDr"] != "1":
                nb_arr[rn].append(min(P(p["arrT"]), t + T(seconds=20)))
        if p["trDr"] == "5" and p["nextStaNm"] in ("Central", "Noyes"):
            pv = rows[i - 1] if i else None
            if pv is None or pv["trDr"] != "5" or t - P(pv["poll_ts"]) > T(minutes=10) or pv["nextStaNm"] not in ("Central", "Noyes", "Linden"):
                deps.append(dict(rn=rn, dep=t))
live = collections.defaultdict(dict)
for a in arr:
    if a["isSch"] == "0": live[a["poll_ts"]][a["rn"]] = a["arrT"]
ap = [p.isoformat() for p in polls]; passes = collections.defaultdict(list)
for i, p in enumerate(ap[:-1]):
    for rn, at in live.get(p, {}).items():
        if rn not in live.get(ap[i + 1], {}): passes[rn].append(min(P(at), P(ap[i + 1])))
trains = []
for d in deps:
    ps = [x for x in passes[d["rn"]] if T(0) <= x - d["dep"] < T(minutes=12)]
    if not ps: continue
    inb = [x for x in nb_arr[d["rn"]] if T(0) < d["dep"] - x < T(minutes=90)]
    trains.append(dict(rn=d["rn"], noyes=ps[0], dep=d["dep"], inbound=max(inb) if inb else None))
trains.sort(key=lambda t: t["noyes"])
# the same physical train can appear twice when a label jumps onto it; keep the label it left Linden with
dedup = []
for tr in trains:
    if dedup and tr["noyes"] - dedup[-1]["noyes"] < T(seconds=75):
        if tr["dep"] < dedup[-1]["dep"]: dedup[-1] = tr
        continue
    dedup.append(tr)
trains = dedup

# --- assignment
by_day = collections.defaultdict(list)
for s in slots: by_day[s["slot"].date()].append(s)
claims = collections.defaultdict(list)   # slot index -> (train, kind, delta)
extras = []
for tr in trains:
    day = by_day.get(tr["noyes"].date(), []); times = [s["slot"] for s in day]
    own = [k for k, s in enumerate(day) if s["rn"] == tr["rn"] and T(minutes=-20) <= tr["noyes"] - s["slot"] <= T(minutes=30)]
    if own and not tr["rn"].startswith("1"):
        k = own[0]; delta = (tr["noyes"] - day[k]["slot"]).total_seconds() / 60
        if delta < -2.5 and k > 0 and abs((tr["noyes"] - day[k - 1]["slot"]).total_seconds() / 60) <= 3:
            claims[(tr["noyes"].date(), k - 1)].append((tr, "pull-up", (tr["noyes"] - day[k - 1]["slot"]).total_seconds() / 60))
        else:
            claims[(tr["noyes"].date(), k)].append((tr, "own", delta))
    else:
        if not times: continue
        k = min(range(len(times)), key=lambda j: abs((tr["noyes"] - times[j]).total_seconds()))
        dd = (tr["noyes"] - times[k]).total_seconds() / 60
        if abs(dd) <= 5: claims[(tr["noyes"].date(), k)].append((tr, "extra-fill", dd))
        else: extras.append(tr)
rows = []
for day, ss in by_day.items():
    for k, s in enumerate(ss):
        c = sorted(claims.get((day, k), []), key=lambda x: abs(x[2]))
        if c:
            tr, kind, dd = c[0]
            for other in c[1:]: extras.append(other[0])
            rows.append(dict(slot=s["slot"].isoformat(), slot_run=s["rn"], fault_flag=int(s["fault"]), outcome=kind,
                             train_run=tr["rn"], passed_noyes=tr["noyes"].isoformat(timespec="seconds"),
                             minutes_vs_slot=round(dd, 2), inbound_arrived_linden=tr["inbound"].isoformat(timespec="seconds") if tr["inbound"] else ""))
        else:
            rows.append(dict(slot=s["slot"].isoformat(), slot_run=s["rn"], fault_flag=int(s["fault"]), outcome="no-show",
                             train_run="", passed_noyes="", minutes_vs_slot="", inbound_arrived_linden=""))
# second pass: trains left over (their own slot was taken by a closer train) fill the nearest
# unserved slot within 6 minutes, so every physical train is counted once
for tr in sorted(extras, key=lambda t: t["noyes"]):
    free = [r for r in rows if r["outcome"] == "no-show" and abs((tr["noyes"] - P(r["slot"])).total_seconds()) <= 360]
    if not free: continue
    r = min(free, key=lambda r: abs((tr["noyes"] - P(r["slot"])).total_seconds()))
    r.update(outcome="fill", train_run=tr["rn"], passed_noyes=tr["noyes"].isoformat(timespec="seconds"),
             minutes_vs_slot=round((tr["noyes"] - P(r["slot"])).total_seconds() / 60, 2),
             inbound_arrived_linden=tr["inbound"].isoformat(timespec="seconds") if tr["inbound"] else "")
    tr["used"] = True
extras = [t for t in extras if not t.get("used")]
rows.sort(key=lambda r: r["slot"])
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
c = collections.Counter(r["outcome"] for r in rows)
inwin = [t for t in extras if covered(t["noyes"])]
print(f"{len(rows)} slots -> {dict(c)}; extra trains inside logged windows not tied to a slot: {len(inwin)}")
