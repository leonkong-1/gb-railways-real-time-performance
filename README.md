# GB Train Performance Dashboard

Real-time web dashboard showing live train performance across the GB network,
consuming TRUST Train Movement messages from the Rail Data Marketplace (Confluent Kafka feed).

Built for control room and performance staff — optimised for fast reading, not aesthetics.

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Kafka credentials
python scripts/fetch_toc_ref.py   # generates toc_ref.csv (TOC operator names)
python app.py                      # starts on http://localhost:8001
```

> **Port**: the app runs on **8001** by default (`PORT` env var). Port 8000 is reserved
> if you are running Claude Code alongside, which binds its IDE server there.

---

## What it shows

- **Leaderboard**: all active services ranked by worst delay, filterable by operator and
  minimum delay threshold. Network-wide view (up to 500 services) or per-operator (unlimited).
- **Drilldown**: per-service event timeline — every TRUST reporting point with actual time,
  planned time (GBTT), computed delay, and timing-point indicator.
- **Live stats**: services tracked, delayed, cancelled, off-route.
- **Auto-refresh**: 15-second interval, scroll-position preserved.

---

## Data sources

### Live train movement feed

**TRUST Train Movements** from Rail Data Marketplace:
https://raildata.org.uk/dataProduct/P-826477b8-3789-45e7-85bd-22c4ae9bcfae/overview

Consumed via Confluent Cloud Kafka (SASL_SSL). Topic: `TRAIN_MVT_ALL_TOC`.
Message types used: 0001 (Activation), 0002 (Cancellation), 0003 (Movement),
0005 (Reinstatement), 0006 (Change of Origin), 0007 (Change of Identity).

**No other real-time data sources are used.** In particular, this project does not
consume the Darwin feed. Darwin is often more up-to-date than TRUST for passenger
information (Darwin aggregates multiple sources; TRUST reflects signaller-reported
movements which can lag slightly). TRUST was chosen here because its message structure
is simpler and more directly suited to a delay-metric MVP. This is an illustrative
proof-of-concept, not a production performance monitoring system.

---

## Reference data

The app starts and functions without reference files — raw codes are shown instead of names.
`/api/health` shows which files are loaded.

### 1. STANOX location names — `CORPUSExtract.json`

**Source**: Rail Data Marketplace (free registration required):
https://raildata.org.uk/dataProduct/P-9d26e657-26be-496b-b669-93b217d45859/overview

**Where to place it**: project root OR `Reference data/` subfolder (auto-detected).

This file is excluded from the repository (large binary, requires registration).

### 2. TOC operator names — `toc_ref.csv`

**Source**: Open Rail Data community wiki:
https://wiki.openraildata.com/index.php/TOC_Codes

Scraped by the provided script:

```bash
python scripts/fetch_toc_ref.py
```

Generates `toc_ref.csv` in the project root. The scraper captures both the 2-letter
Business Code and the numeric Sector Code used in the TRUST feed, so both resolve to
operator names. `toc_ref.csv` is committed to the repo as a convenience — re-run the
script if the operator list changes.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVER` | Yes | — | Confluent Cloud bootstrap server |
| `KAFKA_USERNAME` | Yes | — | Kafka API key |
| `KAFKA_PASSWORD` | Yes | — | Kafka API secret |
| `KAFKA_CONSUMER_GROUP` | Yes | — | Consumer group ID |
| `WINDOW_MAX_MESSAGES_PER_SERVICE` | No | `10` | Max messages retained per train_id for **terminated** services |
| `WINDOW_MAX_AGE_MINUTES` | No | `240` | Max age of last message before eviction (minutes) |
| `PHASE1_INGEST_SECONDS` | No | `0` | Set > 0 for timed snapshot mode; `0` = continuous |
| `PORT` | No | `8001` | HTTP port |
| `REFERENCE_DATA_DIR` | No | `./` | Path to directory containing reference files |

### Eviction rules

Two rules applied on every message append:

1. **Latest-N per service** (`WINDOW_MAX_MESSAGES_PER_SERVICE`): only applied to
   **terminated** services. Non-terminated services keep **all** messages so their full
   station timeline is always visible in the drilldown. Rationale: truncating an active
   service to 10 messages produces confusing incomplete timelines.

2. **Max age** (`WINDOW_MAX_AGE_MINUTES`): if the most-recent message for a `train_id`
   is older than this threshold, the entire entry is evicted. Default is 240 minutes
   (4 hours) rather than a shorter window, to accommodate Highland route services where
   gaps between TRUST reporting points can exceed 90 minutes.

---

## Operating modes

### Continuous (default)

`PHASE1_INGEST_SECONDS=0` — consumer runs indefinitely. Data refreshes live every 15s.

### Snapshot (Phase 1)

Set `PHASE1_INGEST_SECONDS=120` (or any value > 0). The consumer ingests for that many
seconds then stops. The server stays up; the captured dataset is fixed. Useful for
debugging against a reproducible slice of data.

QC checks after a snapshot:
```
GET http://localhost:8001/api/qc/run
GET http://localhost:8001/api/qc/{train_id}
```

---

## API reference

| Endpoint | Description |
|---|---|
| `GET /` | Frontend dashboard |
| `GET /api/performance` | Service leaderboard (`toc_id`, `min_delay`, `limit`, `include_terminated`, `include_cancelled`) |
| `GET /api/tocs` | All distinct operators currently in the store (used to populate the filter dropdown) |
| `GET /api/service/{train_id}` | Full event timeline for one service |
| `GET /api/health` | Consumer thread and reference data status |
| `GET /api/qc/run` | Run 6 QC checks — plain text report |
| `GET /api/qc/{train_id}` | Full reconciliation trace for one service |

---

## Key domain notes

### TRUST ID vs headcode

The **10-character TRUST ID** (e.g. `882S65MZ07`) is the unique service identifier.
The **4-character headcode** (e.g. `2S65`) is for human readability only — it is **not
unique** and is recycled within the same day and TOC. TRUST ID is the join key throughout
the data model; both are shown in the UI. The TRUST ID column is click-to-copy.

### Delay calculation

- Primary: `actual_timestamp_ms − gbtt_timestamp_ms` (GBTT = public passenger timetable)
- Fallback: `actual_timestamp_ms − planned_timestamp_ms` (WTT) when GBTT is absent or zero.
  GBTT is absent for freight services, non-public calls, and locations without a passenger
  timetable entry. The drilldown marks WTT-based delays with `(WTT)`.
- Excluded: messages where `offroute_ind == "true"` (no valid timetable reference off-route).
- Qualifying: only `0003` (Movement) messages where `delay_monitoring_point == "true"`.

### TRUST minute-rounding

TRUST rounds `actual_timestamp` to whole minutes. Two qualifying messages for the same
service can therefore share the same `actual_timestamp_ms`. Sort key is
`(actual_timestamp_ms, received_at)` using `max()` — not a `>=` loop — for deterministic
tie-breaking consistent between aggregation and QC checks.

---

## Known limitations (v1)

- **BST timestamp bug**: all TRUST timestamps are 1 hour ahead during British Summer Time.
  Delay arithmetic is unaffected (the offset cancels in the difference), but absolute times
  on the frontend will be 1 hour ahead during BST.

- **Change of Identity (0007)**: a 0007 message assigns a new `train_id` mid-journey.
  Subsequent messages appear as a new store entry; the two entries are not linked.

- **Off-route services**: excluded from the delay leaderboard (timetable reference is
  meaningless off-route); shown as a separate count in the summary bar.

---

## Hosting

GitHub Pages is not viable (no backend). The app requires a persistent Python process
for the Kafka consumer thread. A `Dockerfile` and `fly.toml` are included for
**Fly.io** deployment (recommended).

### Fly.io deployment

Prerequisites: [Fly.io account](https://fly.io) + [flyctl CLI](https://fly.io/docs/hands-on/install-flyctl/) installed.

```bash
# 1. Authenticate
fly auth login

# 2. Deploy (fly.toml is already configured — skip the interactive setup)
fly deploy

# 3. Set secrets (Kafka credentials — never committed to the repo)
fly secrets set \
  KAFKA_BOOTSTRAP_SERVER="pkc-xxxxx.europe-west2.gcp.confluent.cloud:9092" \
  KAFKA_USERNAME="your-api-key" \
  KAFKA_PASSWORD="your-api-secret" \
  KAFKA_CONSUMER_GROUP="your-consumer-group-id"

# 4. Open the live app
fly open
```

The app runs on Fly's `lhr` (London) region — closest to the Rail Data Marketplace
Kafka cluster. `auto_stop_machines = false` and `min_machines_running = 1` in
`fly.toml` ensure the VM stays alive for continuous Kafka consumption.

> **Note on dual consumers**: if you also run the app locally while Fly.io is live,
> both instances share the same Kafka consumer group and will split the message
> partition between them. Use a different `KAFKA_CONSUMER_GROUP` locally (e.g. append
> `-dev`) to avoid this.

> **CORPUSExtract.json**: this file is not in the repository (requires NR registration).
> Without it the app starts normally and shows raw STANOX codes instead of location names.
> To add it: download from Rail Data Marketplace, then use `fly sftp` or bake it into
> the image via a `Reference data/` folder added to your local build before `fly deploy`.

### Alternative platforms

| Platform | Notes |
|---|---|
| **Railway** | GitHub push deploy, very simple; set env vars in dashboard |
| **Render** | Free tier spins down on inactivity — breaks the Kafka consumer; use paid plan |

---

## Development context — agentic build

This project was built end-to-end using **Claude Code** (Anthropic's agentic CLI),
operating in an interactive loop with a human product owner. The `Instructions/` folder
contains the original specification documents that were handed to the agent at the start
of the engagement:

```
Instructions/
  00_overview.md       Project brief and success criteria
  01_architecture.md   Data model, layering, threading model
  02_ingestion.md      Kafka consumer spec
  03_reference_data.md STANOX and TOC reference data
  04_aggregation.md    Delay, trend, cancelled, off-route logic
  05_serving.md        API endpoint contracts
  06_visualisation.md  Frontend requirements
  07_testing.md        QC checks and acceptance criteria
```

The agent (Claude Sonnet 4.6) read all spec files, surfaced domain questions before
writing any code, built the full stack in sequence, ran all 6 QC checks against live
feed data, then iterated on feedback to fix bugs and add features. Key decisions made
during or after the build are documented in `DECISIONS.md`.

`CLAUDE.md` and `CONTEXT.md` in the project root are agent-facing context files — they
allow a new Claude Code session to resume work on this project without re-reading the
full codebase. This pattern (persistent context files alongside source code) is a
recommended practice for long-running agentic projects.
