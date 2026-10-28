#!/usr/bin/env python3
"""
noyes.py - "leave now" pings for the southbound Purple Line at Noyes.

CTA only publishes a live prediction for a southbound train once it departs
Linden; before that, Noyes shows a *scheduled* time. But the train that will
leave Linden is usually visible running northbound into Linden. This script
tracks that northbound train, estimates when it will really reach Noyes
southbound, and pings you when it's time to walk.

    est_noyes = max(scheduled_noyes, nb_linden_arrival + TURN + RUN)

(If the train is early or on time it sits at Linden until its scheduled
departure; if it's late, it turns around as quickly as it can.)

Subcommands
    go       ping me when it's time to leave for the next catchable train
    log      just collect data (leave it running through the afternoon)
    analyze  calibrate TURN/RUN from the logs and score model vs. schedule

Stdlib only (Python 3.8+). Needs a CTA Train Tracker API key in $CTA_API_KEY.
Optional: $NTFY_TOPIC for phone push notifications via ntfy.sh.
"""
import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

API = "http://lapi.transitchicago.com/api/1.0"
NOYES_MAPID = "40400"
LINDEN_MAPID = "41050"
NB, SB = "1", "5"  # Purple Line trDr: 1 = Linden-bound, 5 = Howard/Loop-bound

ARR_FIELDS = ["poll_ts", "rn", "destNm", "trDr", "stpId", "stpDe", "prdt", "arrT",
              "isApp", "isSch", "isDly", "isFlt", "lat", "lon", "heading"]
POS_FIELDS = ["poll_ts", "rn", "destNm", "trDr", "nextStaId", "nextStaNm", "prdt",
              "arrT", "isApp", "isDly", "lat", "lon", "heading"]
EST_FIELDS = ["poll_ts", "rn", "destNm", "isSch", "isFlt", "noyes_arrT", "nb_found",
              "nb_next_sta", "nb_isDly", "nb_linden_eta", "est_noyes", "source",
              "turn_min", "run_min"]
POLL_FIELDS = ["poll_ts", "n_etas", "n_trains"]

# rn -> last known Linden arrival of that run's northbound trip. Survives the
# train dropping out of the feed while it lays over at Linden.
LAST_NB_ETA = {}


# ----------------------------------------------------------------- helpers

def as_list(x):
    """CTA's JSON turns single-element arrays into objects; normalize."""
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def parse_t(s):
    return datetime.fromisoformat(s) if s else None


def fmt(t):
    return t.strftime("%H:%M:%S") if t else "-"


def hm(t):
    return t.strftime("%I:%M").lstrip("0")


def mins(td):
    return td.total_seconds() / 60.0


class ApiError(Exception):
    pass


def fetch(endpoint, **params):
    key = os.environ.get("CTA_API_KEY")
    if not key:
        sys.exit("Set CTA_API_KEY first (see README).")
    params.update(key=key, outputType="JSON")
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.load(r)["ctatt"]
    if str(data.get("errCd", "0")) != "0":
        raise ApiError(f"{data.get('errCd')}: {data.get('errNm')}")
    return data


class CsvLog:
    def __init__(self, path, fields):
        self.path, self.fields = Path(path), fields
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rows):
        if not rows:
            return
        new = not self.path.exists()
        with self.path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.fields, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)


def notify(title, msg, topic, priority="high", tags="train"):
    print(f"\a\n>>> {title}: {msg}\n", flush=True)
    if topic:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"),
                headers={"Title": title, "Priority": priority, "Tags": tags})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:  # never let a failed push kill the loop
            print(f"(ntfy push failed: {e})", file=sys.stderr)
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e",
                        f'display notification {json.dumps(msg)} with title {json.dumps(title)} sound name "Glass"'],
                       check=False)


# ----------------------------------------------------------------- core poll

class Candidate:
    def __init__(self, rn, dest, noyes_t, is_sch, est, source, nb_eta):
        self.rn, self.dest, self.noyes_t, self.is_sch = rn, dest, noyes_t, is_sch
        self.est, self.source, self.nb_eta = est, source, nb_eta


def linden_eta(train, poll_cache):
    """Predicted arrival of a northbound train at Linden."""
    if train.get("nextStaId") == LINDEN_MAPID or train.get("nextStaNm") == "Linden":
        return parse_t(train.get("arrT"))
    rn = train["rn"]
    if rn not in poll_cache:
        try:
            f = fetch("ttfollow.aspx", runnumber=rn)
            etas = as_list(f.get("eta"))
        except (ApiError, urllib.error.URLError):
            etas = []
        poll_cache[rn] = next((parse_t(e["arrT"]) for e in etas
                               if e.get("staId") == LINDEN_MAPID or e.get("staNm") == "Linden"), None)
    return poll_cache[rn]


def poll(args, logs):
    arr = fetch("ttarrivals.aspx", mapid=NOYES_MAPID)
    now = parse_t(arr["tmst"])
    ts = now.isoformat()
    etas = as_list(arr.get("eta"))

    pos = fetch("ttpositions.aspx", rt="P")
    trains = []
    for route in as_list(pos.get("route")):
        trains += as_list(route.get("train"))

    logs["arr"].write([{**e, "poll_ts": ts} for e in etas])
    logs["pos"].write([{**t, "poll_ts": ts} for t in trains])
    logs["poll"].write([{"poll_ts": ts, "n_etas": len(etas), "n_trains": len(trains)}])

    nb_by_rn = {t["rn"]: t for t in trains if t.get("trDr") == NB}
    turn, run = timedelta(minutes=args.turn), timedelta(minutes=args.run)
    follow_cache, cands, est_rows = {}, [], []

    for e in etas:
        if e.get("trDr") != SB:
            continue
        noyes_t = parse_t(e["arrT"])
        nb = nb_by_rn.get(e["rn"])
        nb_eta = None
        if e.get("isSch") == "0":
            est, source = noyes_t, "live"
        else:
            nb_eta = linden_eta(nb, follow_cache) if nb else None
            if nb_eta:
                LAST_NB_ETA[e["rn"]] = nb_eta
            else:
                prev = LAST_NB_ETA.get(e["rn"])
                if prev and timedelta(0) <= now - prev <= timedelta(minutes=30):
                    nb_eta = prev  # train already at Linden, laying over
            if nb_eta:
                est = max(noyes_t, nb_eta + turn + run)
                source = "nb-late" if est > noyes_t + timedelta(seconds=30) else "nb-ontime"
            else:
                est, source = noyes_t, "sched"
        cands.append(Candidate(e["rn"], e.get("destNm", ""), noyes_t, e.get("isSch"), est, source, nb_eta))
        est_rows.append({
            "poll_ts": ts, "rn": e["rn"], "destNm": e.get("destNm"), "isSch": e.get("isSch"),
            "isFlt": e.get("isFlt"), "noyes_arrT": e["arrT"], "nb_found": int(nb is not None),
            "nb_next_sta": nb.get("nextStaNm") if nb else "", "nb_isDly": nb.get("isDly") if nb else "",
            "nb_linden_eta": nb_eta.isoformat() if nb_eta else "", "est_noyes": est.isoformat(),
            "source": source, "turn_min": args.turn, "run_min": args.run})
    logs["est"].write(est_rows)
    cands.sort(key=lambda c: c.est)
    return now, cands


def make_logs(data_dir):
    d = Path(data_dir)
    return {"arr": CsvLog(d / "noyes_arrivals.csv", ARR_FIELDS),
            "pos": CsvLog(d / "purple_positions.csv", POS_FIELDS),
            "est": CsvLog(d / "estimates.csv", EST_FIELDS),
            "poll": CsvLog(d / "polls.csv", POLL_FIELDS)}


def dest_ok(c, dest):
    return dest.lower() == "any" or dest.lower() in c.dest.lower()


def describe(c):
    if c.source == "live":
        return "live"
    if c.source == "sched":
        return "schedule only"
    delta = mins(c.est - c.noyes_t)
    return f"NB train says ~{delta:+.0f} min vs sched" if c.source == "nb-late" else "NB train on pace"


# ----------------------------------------------------------------- commands

def cmd_run(args, ping):
    logs = make_logs(args.data)
    topic = os.environ.get("NTFY_TOPIC")
    walk, buf = timedelta(minutes=args.walk), timedelta(minutes=args.buffer)
    slack = timedelta(minutes=args.slack)
    pinged = set()
    stop_at = time.monotonic() + args.minutes * 60 if args.minutes else None
    announce = ping and getattr(args, "announce", False)
    announced = False
    print(f"{'Watching' if ping else 'Logging'} Noyes southbound "
          f"(dest={args.dest}, walk={args.walk}m, buffer={args.buffer}m, poll={args.interval}s). Ctrl-C to stop.")
    while True:
        if stop_at and time.monotonic() >= stop_at:
            print(f"Reached --minutes {args.minutes:g}; stopping.")
            if announce and not pinged:
                notify("Noyes alerts off", f"Timed out after {args.minutes:g} min with no leave-now ping "
                       f"(no catchable {args.dest} train?).", topic, priority="default", tags="zzz")
            return
        try:
            now, cands = poll(args, logs)
        except (ApiError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            print(f"[{datetime.now():%H:%M:%S}] poll failed: {e}", file=sys.stderr)
            time.sleep(args.interval)
            continue

        usable = [c for c in cands if dest_ok(c, args.dest) and c.est - now >= walk - slack]
        if announce and not announced:
            if usable:
                c0 = usable[0]
                lv = c0.est - walk - buf
                when = "now" if lv <= now else f"at {hm(lv)}"
                note = " Express or shuttle isn't known until it leaves Linden." if "(or" in c0.dest else ""
                msg = (f"Next {c0.dest} train: Noyes ~{hm(c0.est)} ({describe(c0)}). "
                       f"Leave ping {when}.{note} Press again to turn off.")
            else:
                msg = f"No catchable {args.dest} train listed yet; watching. Press again to turn off."
            notify("Noyes alerts ON", msg, topic, priority="default", tags="bell")
            announced = True
        if not usable:
            print(f"[{fmt(now)}] no catchable {args.dest} train listed yet", flush=True)
        else:
            c = usable[0]
            leave_at = c.est - walk - buf
            print(f"[{fmt(now)}] run {c.rn} -> {c.dest}: Noyes ~{fmt(c.est)} ({describe(c)}; "
                  f"sched {fmt(c.noyes_t)})  leave {fmt(leave_at)} ({mins(leave_at - now):+.1f}m)", flush=True)
            key = (c.rn, c.noyes_t.date())
            if ping and now >= leave_at and key not in pinged:
                after = next((u for u in usable[1:]), None)
                msg = (f"{c.dest}-bound run {c.rn} at Noyes ~{hm(c.est)} "
                       f"({mins(c.est - now):.0f} min, {describe(c)}).")
                if after:
                    msg += f" Next: ~{hm(after.est)}."
                notify("Leave now", msg, topic)
                pinged.add(key)
                if not args.stay:
                    return
        time.sleep(args.interval)


def read_csv(path):
    p = Path(path)
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def segment(rows, gap):
    """Split one run's rows (sorted by poll_ts) into trips separated by >gap."""
    trips, cur, last = [], [], None
    for r in rows:
        t = parse_t(r["poll_ts"])
        if last and t - last > gap:
            trips.append(cur)
            cur = []
        cur.append(r)
        last = t
    if cur:
        trips.append(cur)
    return trips


def summarize(name, xs):
    if not xs:
        print(f"  {name:<34} n=0")
        return
    q = statistics.quantiles(xs, n=4) if len(xs) >= 4 else [min(xs), statistics.median(xs), max(xs)]
    print(f"  {name:<34} n={len(xs):<3} median {statistics.median(xs):5.2f}  "
          f"IQR [{q[0]:.2f}, {q[-1]:.2f}]  range [{min(xs):.2f}, {max(xs):.2f}]")


def cmd_analyze(args):
    d = Path(args.data)
    arr = [r for r in read_csv(d / "noyes_arrivals.csv") if r["trDr"] == SB]
    pos = read_csv(d / "purple_positions.csv")
    est = read_csv(d / "estimates.csv")
    if not arr:
        sys.exit("No data yet - run `log` or `go` first.")
    gap = timedelta(minutes=25)
    poll_times = sorted({parse_t(r["poll_ts"]) for r in read_csv(d / "polls.csv")})

    def next_poll_after(t):
        for p in poll_times:
            if p > t:
                return p
        return None

    # Southbound trips at Noyes
    by_rn = defaultdict(list)
    for r in sorted(arr, key=lambda r: r["poll_ts"]):
        by_rn[r["rn"]].append(r)
    trips = []
    for rn, rows in by_rn.items():
        for seg in segment(rows, gap):
            live = [r for r in seg if r["isSch"] == "0"]
            sch = [r for r in seg if r["isSch"] == "1"]
            if not live:
                continue
            first_live = parse_t(live[0]["poll_ts"])       # ~ departure from Linden
            last = live[-1]
            gone = next_poll_after(parse_t(last["poll_ts"]))  # first poll where it had arrived
            actual = parse_t(last["arrT"])
            if gone:
                actual = min(actual, gone)
            trips.append({"rn": rn, "start": parse_t(seg[0]["poll_ts"]), "end": parse_t(seg[-1]["poll_ts"]),
                          "first_live": first_live, "actual": actual, "dest": last["destNm"],
                          "sched": parse_t(sch[-1]["arrT"]) if sch else None})

    # Northbound arrivals at Linden (last predicted arrT while next stop was Linden)
    nb_by_rn = defaultdict(list)
    for r in sorted((r for r in pos if r["trDr"] == NB and r["nextStaNm"] == "Linden"),
                    key=lambda r: r["poll_ts"]):
        nb_by_rn[r["rn"]].append(r)
    nb_events = defaultdict(list)
    for rn, rows in nb_by_rn.items():
        for seg in segment(rows, gap):
            nb_events[rn].append(parse_t(seg[-1]["arrT"]))

    turn, run, late = [], [], []
    for t in trips:
        cands = [x for x in nb_events.get(t["rn"], []) if x <= t["first_live"] <= x + timedelta(minutes=45)]
        t["nb_linden"] = max(cands) if cands else None
        if t["nb_linden"]:
            turn.append(mins(t["first_live"] - t["nb_linden"]))
        run.append(mins(t["actual"] - t["first_live"]))
        if t["sched"]:
            late.append(mins(t["actual"] - t["sched"]))

    print(f"\nSouthbound trips observed at Noyes: {len(trips)}  "
          f"(with matching NB run into Linden: {sum(1 for t in trips if t['nb_linden'])})")
    print("Minutes (poll resolution ~ +/- interval):")
    summarize("NB Linden arrival -> SB departure", turn)
    summarize("SB departure -> Noyes (RUN)", run)
    summarize("Actual - scheduled at Noyes", late)

    # Score the estimates made while Noyes only had a scheduled time
    buckets = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 60)]
    model_err, sched_err = defaultdict(list), defaultdict(list)
    for e in est:
        if e["isSch"] != "1":
            continue
        pt = parse_t(e["poll_ts"])
        trip = next((t for t in trips if t["rn"] == e["rn"]
                     and t["start"] - timedelta(minutes=1) <= pt <= t["end"] + timedelta(minutes=1)), None)
        if not trip:
            continue
        lead = mins(trip["actual"] - pt)
        b = next((b for b in buckets if b[0] <= lead < b[1]), None)
        if b:
            model_err[b].append(mins(parse_t(e["est_noyes"]) - trip["actual"]))
            sched_err[b].append(mins(parse_t(e["noyes_arrT"]) - trip["actual"]))

    print("\nWhile Noyes still showed a scheduled time - error vs. actual (min; + = predicted too late):")
    print(f"  {'lead time':<12}{'n':>5}{'model MAE':>12}{'model bias':>12}{'sched MAE':>12}{'sched bias':>12}")
    for b in buckets:
        m, s = model_err[b], sched_err[b]
        if not m:
            continue
        mae = lambda xs: statistics.mean(abs(x) for x in xs)
        print(f"  {f'{b[0]}-{b[1]} min':<12}{len(m):>5}{mae(m):>12.2f}{statistics.mean(m):>12.2f}"
              f"{mae(s):>12.2f}{statistics.mean(s):>12.2f}")
    if turn and run:
        print(f"\nSuggested settings: --turn {max(0.5, statistics.median(sorted(turn)[:max(1, len(turn)//4)])):.1f} "
              f"(fast-turn quartile) --run {statistics.median(run):.1f}")


def station_events(rows):
    """One run's position rows (sorted) -> [(station, trDr, arrival_time)].

    Each block of polls where the train's next stop is S ends when it reaches S.
    Arrival = the last predicted arrT in that block, capped at the first poll
    showing a different next stop (accurate to about one poll interval)."""
    ev, i = [], 0
    while i < len(rows):
        j = i
        while (j + 1 < len(rows) and rows[j + 1]["nextStaNm"] == rows[i]["nextStaNm"]
               and rows[j + 1]["trDr"] == rows[i]["trDr"]):
            j += 1
        if j + 1 < len(rows):
            arr = min(parse_t(rows[j]["arrT"]), parse_t(rows[j + 1]["poll_ts"]))
            ev.append((rows[i]["nextStaNm"], rows[i]["trDr"], arr))
        i = j + 1
    return ev


def cmd_trips(args):
    """Door-to-door ride times between two stations, from the position logs."""
    pos = read_csv(Path(args.data) / "purple_positions.csv")
    if not pos:
        sys.exit("No position data yet - run `log` first.")
    by_rn = defaultdict(list)
    for r in sorted(pos, key=lambda r: r["poll_ts"]):
        by_rn[r["rn"]].append(r)
    a, b = args.a, args.b
    legs = {"5": (a, b), "1": (b, a)}  # SB: a -> b, NB: b -> a (a is the northern station)
    found = defaultdict(list)
    for rn, rows in by_rn.items():
        for rows_seg in segment(rows, timedelta(minutes=25)):
            ev = station_events(rows_seg)
            for k, (sta, d, t) in enumerate(ev):
                if d not in legs or sta != legs[d][0]:
                    continue
                end = next((t2 for s2, d2, t2 in ev[k + 1:] if s2 == legs[d][1] and d2 == d), None)
                if end and timedelta(0) < end - t < timedelta(minutes=60):
                    found[d].append((t, rn, mins(end - t)))
    for d, label in (("5", f"{a} -> {b} (southbound)"), ("1", f"{b} -> {a} (northbound)")):
        trips = sorted(set(found[d]))
        print(f"\n{label}: {len(trips)} trips")
        for t, rn, m in trips:
            print(f"  {t:%a %m-%d %H:%M}  run {rn:<4} {m:5.1f} min")
        if trips:
            xs = [m for _, _, m in trips]
            summarize("ride time", xs)
            if args.timetable:
                print(f"  vs timetable {args.timetable:g} min: median {statistics.median(xs) - args.timetable:+.1f} min")


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--data", default="data", help="folder for CSV logs (default ./data)")
        p.add_argument("--interval", type=int, default=15, help="seconds between polls (default 15)")
        p.add_argument("--turn", type=float, default=2.0, help="min. turnaround at Linden, minutes (default 2)")
        p.add_argument("--run", type=float, default=3.5, help="Linden departure -> Noyes, minutes (default 3.5)")
        p.add_argument("--dest", default="Loop", help="destination to catch: Loop (express), Howard, or any")
        p.add_argument("--walk", type=float, default=13.4, help="office chair to Noyes platform, minutes (default 13.4 = 13:21 timed)")
        p.add_argument("--buffer", type=float, default=2.5, help="extra minutes of margin (default 2.5: covers trains up to ~2.2 min early, seen 10/27)")
        p.add_argument("--slack", type=float, default=1.0,
                       help="still count a train as catchable if it's this many min tighter than the walk")
        p.add_argument("--minutes", type=float, default=0, help="stop after this many minutes (0 = run until Ctrl-C)")

    go = sub.add_parser("go", help="ping me when it's time to leave (also logs)")
    common(go)
    go.add_argument("--stay", action="store_true", help="keep running/logging after the ping")
    go.add_argument("--announce", action="store_true",
                    help="push an 'alerts ON' message after the first poll and an 'off' message on timeout")
    lg = sub.add_parser("log", help="collect data only, no pings")
    common(lg)
    an = sub.add_parser("analyze", help="calibrate from logs")
    an.add_argument("--data", default="data")
    tr = sub.add_parser("trips", help="ride times between two stations (default Noyes <-> Wilson)")
    tr.add_argument("--data", default="data")
    tr.add_argument("--a", default="Noyes", help="northern station (default Noyes)")
    tr.add_argument("--b", default="Wilson", help="southern station (default Wilson)")
    tr.add_argument("--timetable", type=float, default=14.0, help="scheduled ride time to compare against")

    args = ap.parse_args()
    try:
        if args.cmd == "analyze":
            cmd_analyze(args)
        elif args.cmd == "trips":
            cmd_trips(args)
        else:
            cmd_run(args, ping=(args.cmd == "go"))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
