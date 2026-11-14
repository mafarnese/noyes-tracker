#!/usr/bin/env python3
"""Scheduled times for a CTA route at stops matching a name, on a given date, from CTA's GTFS feed.
usage: gtfs_lookup.py ROUTE "WORD1,WORD2" YYYY-MM-DD HH:MM HH:MM OUT.csv"""
import csv, io, sys, zipfile, urllib.request, datetime as dt
route, words, day, t0, t1, out = sys.argv[1:7]
words = [w.strip().lower() for w in words.split(",")]
URL = "https://www.transitchicago.com/downloads/sch_data/google_transit.zip"
req = urllib.request.Request(URL, headers={"User-Agent": "noyes-tracker-gtfs-lookup"})
z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(req, timeout=300).read()))
def rows(name): return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))
d = dt.date.fromisoformat(day); ymd = d.strftime("%Y%m%d"); wd = d.strftime("%A").lower()
active = set()
for r in rows("calendar.txt"):
    if r["start_date"] <= ymd <= r["end_date"] and r[wd] == "1": active.add(r["service_id"])
if "calendar_dates.txt" in z.namelist():
    for r in rows("calendar_dates.txt"):
        if r["date"] == ymd: (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
trips = {r["trip_id"]: r for r in rows("trips.txt") if r["route_id"] == route and r["service_id"] in active}
stops = {r["stop_id"]: r["stop_name"] for r in rows("stops.txt") if all(w in r["stop_name"].lower() for w in words)}
res = []
for r in rows("stop_times.txt"):
    if r["trip_id"] in trips and r["stop_id"] in stops:
        t = r["departure_time"][:5]
        if t0 <= t.zfill(5) <= t1:
            tr = trips[r["trip_id"]]
            res.append(dict(time=r["departure_time"], stop_id=r["stop_id"], stop_name=stops[r["stop_id"]],
                            direction=tr.get("direction", tr.get("direction_id", "")), headsign=tr.get("trip_headsign", ""), trip_id=r["trip_id"]))
res.sort(key=lambda x: (x["stop_name"], x["time"]))
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["time", "stop_id", "stop_name", "direction", "headsign", "trip_id"]); w.writeheader(); w.writerows(res)
info = f"# feed fetched {dt.datetime.utcnow():%Y-%m-%d %H:%MZ}; date {day} ({wd}); active services {sorted(active)}; matching stops {stops}; route {route} trips {len(trips)}\n"
open(out + ".info.txt", "w").write(info); print(info); print(open(out).read())
