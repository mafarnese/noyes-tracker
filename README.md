# noyes-tracker

Logs CTA Purple Line data to figure out when the southbound train will *actually*
reach Noyes, using the northbound train's progress into Linden, before CTA's live
prediction kicks in.

- `noyes.py`: the logger, the "leave now" notifier, and the analyzer
- `.github/workflows/log.yml`: runs the logger on weekday afternoons and commits CSVs to `data/`
- `data/`: Noyes predictions, Purple Line train positions, and model estimates

## Local use

```bash
export CTA_API_KEY=...          # https://www.transitchicago.com/developers/traintrackerapply/
export NTFY_TOPIC=...           # optional: ntfy.sh topic for phone pings
python3 noyes.py go             # ping when it's time to leave (walk = 13.4 min)
python3 noyes.py analyze        # calibrate from data/
```

The GitHub Action needs a repo secret named `CTA_API_KEY`.
Run it on demand from the Actions tab via **Run workflow**.
