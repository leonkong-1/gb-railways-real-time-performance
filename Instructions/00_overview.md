# Train Performance Dashboard -- Project Overview

## What we are building

A real-time web dashboard that gives control room and operational performance staff a live view of train service health across the GB network, based on a rolling window of Train Movement messages from the Rail Data Marketplace.

The tool fills a specific gap: day-after performance reports exist, and individual services can be looked up on Realtime Trains, but there is currently no way to answer "what is the health of the network right now, at a glance, across all services?" This is the v1 of that tool.

## Who will use it

Control room staff and performance managers during live shifts. The UX bar is: fast to read, filterable by operator, useful without training. Not beautiful.

## Scope of these documents

This PRD is decomposed into separate files, each corresponding to a build layer:

- `00_overview.md` -- this file: context, personas, working conventions
- `01_architecture.md` -- overall stack, data flow, the two-level data model
- `02_ingestion.md` -- Kafka connection, message parsing, cache design, eviction logic
- `03_reference_data.md` -- dimensional lookups (TOC, STANOX), file-driven loading pattern
- `04_aggregation.md` -- delay calculation logic, service-grain derived model, trend logic
- `05_serving.md` -- API endpoint specifications
- `06_visualisation.md` -- dashboard UI, leaderboard view, service drilldown view

Build and verify each layer in order before proceeding to the next.

---

## Working conventions for this project

### Your role

Approach this project as a **highly competent analytics engineer** who is building against a domain they are technically fluent in but operationally unfamiliar with.

This means:

- **Make your own technical decisions**: language, library choices, code structure, error handling patterns, test scaffolding. Do not ask for permission on these.
- **Ask before assuming on domain questions**: anything touching "how does this data represent operational reality" must be surfaced as a question before you implement. Examples of domain questions:
  - "When `gbtt_timestamp` is null or zero on a `0003` message, what does that mean operationally, and how should delay be calculated?"
  - "Should off-route services be included in the delay leaderboard, or treated as a separate category?"
  - "Is a service with `train_terminated = true` always finished, or can it be reinstated?"
  - "How should I handle duplicate `train_id` + `actual_timestamp` messages, which may occur due to corrections?"
- **Surface ambiguities early**: if a spec requirement is unclear or has edge cases that could be resolved multiple ways, stop and ask. Do not implement a silent assumption.
- **Prefer composability over cleverness**: write code that can be extended or corrected without a rewrite. Hardcode nothing that belongs in a config or reference file.

### Build phases

Build in two phases. Do not proceed to Phase 2 until Phase 1 is verified end-to-end.

**Phase 1: Timed ingest, end-to-end build on small data**

1. Connect to the Kafka feed and ingest for a fixed window of 2-5 minutes. Stop consuming after that window closes.
2. Build all layers (ingestion, reference loading, aggregation, serving, frontend) against this fixed dataset.
3. Run QC checks (see `07_testing.md`) against the captured data. Confirm aggregation outputs reconcile to source messages.
4. Fix all bugs and edge cases identified by QC before proceeding.
5. Ask for sign-off before moving to Phase 2.

**Phase 2: Harden to continuous operation**

Switch the consumer from a timed ingest to a continuous loop. Verify that eviction, thread safety, and the frontend auto-refresh all behave correctly under ongoing message flow. The Phase 1 QC fixtures should still pass.

### What "done" looks like for each layer

Each layer is complete when:
1. The code runs without errors against the Kafka feed (or a mocked equivalent for layers that do not yet depend on it)
2. You can demonstrate the output of that layer explicitly (e.g. print a sample of the cache contents, return a sample API response)
3. You have flagged any domain questions that arose during implementation

Do not proceed to the next layer until the current one is verified.

---

## Credentials and environment

All credentials are supplied via a `.env` file. Never hardcode credentials. The app must start cleanly with:

```
python app.py
```

or

```
uvicorn app:app --reload
```

Required environment variables are documented in `01_architecture.md`.
