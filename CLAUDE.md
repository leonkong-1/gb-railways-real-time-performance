# CLAUDE.md — GB Train Performance Dashboard

Pick up a new session by reading: CLAUDE.md → CONTEXT.md → DECISIONS.md.

## Project
Real-time train performance dashboard. FastAPI + Vanilla JS. Kafka feed TRAIN_MVT_ALL_TOC.

## Run
```
python app.py          # starts on port 8001 (8000 is taken by Claude Code IDE server)
```
Open http://localhost:8001

## Key constraints
- **Port 8001** — never use 8000 (Claude Code IDE server binds there)
- **TRUST ID (10-char) is the unique service identifier** — headcode (4-char) is NOT unique,
  recycled within same day/TOC. Always show TRUST ID; keep as join key throughout.
- **No database** — pure in-memory store, lost on restart.
- **Level 2 (service-grain) always computed at query time** — never pre-aggregated or cached.

## Architecture quick-ref
```
consumer.py  →  store.py (MessageStore + eviction)
                    ↓ snapshot at query time
aggregation.py  →  routers/performance.py, service.py, qc.py
reference.py    →  resolves toc_id → operator name, stanox → location name
```

## Eviction (UPDATED post Phase 2)
- Rule 1 (latest-N): ONLY for terminated services. Non-terminated keep ALL messages.
  Rationale: truncating active services to 10 msgs gave incomplete drilldown timelines.
- Rule 2 (max-age): drop train_id if most-recent received_at > WINDOW_MAX_AGE_MINUTES (240).
  Applies regardless of termination status.

## Delay calculation
- Primary: actual_timestamp_ms - gbtt_timestamp_ms
- Fallback: actual_timestamp_ms - planned_timestamp_ms (when gbtt absent/zero)
- Exclude: offroute_ind == "true"
- Only for: 0003 messages with delay_monitoring_point == "true"
- QC trace records reference_time_used ("gbtt" or "planned")

## Known data quirks
- TRUST rounds actual_timestamp to whole minutes → ties possible; use max() not >= loop
- BST bug: all timestamps 1hr ahead during BST; delay arithmetic fine (cancels), display wrong
- Batch Kafka payloads: items in a list share the same received_at (second-level resolution)

## Current state
- Phase 2 continuous mode: PHASE1_INGEST_SECONDS=0
- All 6 QC checks pass on live rolling data
- toc_ref.csv generated (213 codes — both Business Code and numeric Sector Code)
- CORPUSExtract.json in "Reference data/" subfolder (auto-detected)
- /api/tocs endpoint returns all operators in store (not just those with delayed services)
