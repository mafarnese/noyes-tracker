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
python3 noyes.py go             # ping ~16 min before the train (13.4 walk + 2.5 buffer)
python3 noyes.py analyze        # calibrate from data/
python3 noyes.py trips          # Noyes <-> Wilson ride times vs the 14-min timetable
```


## GitHub Actions

Repo secrets: `CTA_API_KEY` (required) and `NTFY_TOPIC` (for phone pushes).

- **Log Purple Line data** (`log.yml`): logs during weekday Purple Line Express service,
  4:30–10:55am and 1:30–8:10pm Chicago time (DST-proof), and commits CSVs to `data/`.
- **Leave-now alerts (toggle)** (`go.yml`): each start is a button press. Alerts off → on
  (pushes "Noyes alerts ON" with the plan, then "Leave now" at the right time, then turns
  itself off; auto-off after 2 h). Alerts on → off.

## Phone button (iPhone Shortcut)

1. Create a fine-grained token at https://github.com/settings/personal-access-tokens/new
   - Repository access: **Only select repositories** → `noyes-tracker`
   - Permissions → Repository → **Actions: Read and write**
2. In Shortcuts, make a new shortcut with one action, **Get Contents of URL**:
   - URL: `https://api.github.com/repos/mafarnese/noyes-tracker/actions/workflows/go.yml/dispatches`
   - Method: **POST**
   - Headers: `Authorization` = `Bearer <your token>`, `Accept` = `application/vnd.github+json`
   - Request Body: **JSON**, one Text field: `ref` = `main`
3. Name it "Noyes" and add it to your Home Screen, Control Center, Action button, or say "Hey Siri, Noyes."

Each press shows up in ntfy within about 15–30 seconds as "Noyes alerts ON" or "Noyes alerts OFF".
