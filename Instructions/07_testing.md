# Testing and QC

## Purpose

The aggregation layer transforms raw messages into service-grain metrics. Bugs here are silent -- an incorrect worst_delay calculation will look plausible and will not throw an error. The QC process exists to make the computation auditable: every derived metric must be traceable back to specific source messages.

This layer is built and verified as part of Phase 1 (small-data ingest) before Phase 2 (continuous operation). Do not proceed to Phase 2 until all QC checks pass.

---

## QC approach: reconciliation traces

Implement a `GET /api/qc/{train_id}` endpoint that returns a full reconciliation trace for a single service. This endpoint is for development and debugging only -- it does not need to appear in the frontend.

### Response structure

```json
{
  "train_id": "882S65MZ07",
  "headcode": "2S65",
  "summary": {
    "worst_delay_mins": 7.0,
    "latest_delay_mins": 5.0,
    "trend": "IMPROVING",
    "qualifying_message_count": 3
  },
  "reconciliation": {
    "all_messages_in_store": 4,
    "messages_used_for_delay": [
      {
        "received_at": "2025-03-08T14:28:05Z",
        "actual_timestamp_raw": "1741441285000",
        "gbtt_timestamp_raw": "1741441165000",
        "actual_timestamp_parsed": "2025-03-08T14:28:05Z",
        "gbtt_timestamp_parsed": "2025-03-08T14:26:05Z",
        "computed_delay_seconds": 120,
        "computed_delay_mins": 2.0,
        "delay_monitoring_point": true,
        "msg_type": "0003",
        "included_in_worst_delay": false,
        "included_in_latest_delay": false
      },
      {
        "received_at": "2025-03-08T14:31:00Z",
        "actual_timestamp_raw": "1741441660000",
        "gbtt_timestamp_raw": "1741441240000",
        "actual_timestamp_parsed": "2025-03-08T14:31:00Z",
        "gbtt_timestamp_parsed": "2025-03-08T14:24:00Z",
        "computed_delay_seconds": 420,
        "computed_delay_mins": 7.0,
        "delay_monitoring_point": true,
        "msg_type": "0003",
        "included_in_worst_delay": true,
        "included_in_latest_delay": false
      },
      {
        "received_at": "2025-03-08T14:33:00Z",
        "actual_timestamp_raw": "1741441780000",
        "gbtt_timestamp_raw": "1741441480000",
        "actual_timestamp_parsed": "2025-03-08T14:33:00Z",
        "gbtt_timestamp_parsed": "2025-03-08T14:28:00Z",
        "computed_delay_seconds": 300,
        "computed_delay_mins": 5.0,
        "delay_monitoring_point": true,
        "msg_type": "0003",
        "included_in_worst_delay": false,
        "included_in_latest_delay": true
      }
    ],
    "messages_excluded": [
      {
        "received_at": "2025-03-08T14:25:00Z",
        "msg_type": "0001",
        "exclusion_reason": "not a 0003 message"
      }
    ],
    "worst_delay_derivation": "max of computed_delay_mins across qualifying messages = 7.0 (from message at 14:31:00)",
    "latest_delay_derivation": "computed_delay_mins from most recent qualifying message = 5.0 (from message at 14:33:00)",
    "trend_derivation": "latest (5.0) < worst (7.0) - threshold (2.0) → IMPROVING"
  }
}
```

The `reconciliation` block must show:
- Every message in the store for this `train_id`, with its raw and parsed timestamps
- The first-principles delay computed for each message, with the arithmetic shown
- Which messages were included vs excluded, and why
- A plain-English derivation of each summary metric, citing the specific message(s) it came from

---

## Automated QC checks (Phase 1)

After the Phase 1 ingest window closes, run the following checks automatically and print a report. These must all pass before proceeding to Phase 2.

### Check 1: Worst delay reconciles to source

For every `train_id` in the store:
- Call the aggregation layer to get `worst_delay_mins`
- Independently recompute it by iterating over raw messages and applying the delay formula directly
- Assert they are equal (within floating point tolerance)
- Report any discrepancies

### Check 2: Latest delay is from the most recent qualifying message

For every `train_id`:
- Identify the most recent `0003` message with `delay_monitoring_point == true` and a valid `gbtt_timestamp`, by `received_at`
- Compute its delay independently
- Assert it equals `latest_delay_mins` from the aggregation layer

### Check 3: Trend derivation is consistent

For every `train_id` where both `worst_delay_mins` and `latest_delay_mins` are non-null:
- Apply the trend logic manually (latest < worst - 2 → IMPROVING, etc.)
- Assert it matches `trend` from the aggregation layer

### Check 4: Qualifying message count is accurate

For every `train_id`:
- Count `0003` messages with `delay_monitoring_point == true` and a valid `gbtt_timestamp` directly from raw messages
- Assert it equals `qualifying_message_count` from the aggregation layer

### Check 5: No message is double-counted

For every `train_id`:
- Confirm that `worst_delay_mins` is derived from exactly one message (the one with the maximum delay)
- Confirm that `latest_delay_mins` is derived from exactly one message (the most recent)
- These may be the same message -- that is valid. They may not be a blend of multiple messages.

### Check 6: Reference data round-trip

For every `toc_id` and `loc_stanox` value seen in the ingested messages:
- Confirm the lookup either returns a resolved name or the raw code (never an error or null)
- Log the proportion of codes that resolved successfully vs fell back to raw -- this is useful signal for whether the reference files are complete

---

## Output format for Phase 1 QC report

Print a summary to stdout:

```
=== Phase 1 QC Report ===
Services in store: 87
Checks run: 6
Passed: 6
Failed: 0

Check 1 (worst delay reconciliation): PASS -- 87/87 services reconciled
Check 2 (latest delay from most recent): PASS -- 87/87 services verified
Check 3 (trend consistency): PASS -- 84/84 non-null trend services verified
Check 4 (qualifying count): PASS -- 87/87 services verified
Check 5 (no double-counting): PASS -- 87/87 services verified
Check 6 (reference data): PASS
  toc_id resolved: 85/87 (98%) -- 2 unmatched codes: ['99', '00']
  stanox resolved: 412/501 (82%) -- 89 unmatched codes (first 5: ['12345', ...])

Phase 1 QC: ALL CHECKS PASSED. Proceed to Phase 2.
```

If any check fails, print the specific `train_id` and message that caused the failure, and do not proceed to Phase 2 until it is resolved.

---

## Ongoing: sanity check endpoint

In Phase 2, the `/api/qc/{train_id}` endpoint remains available. It is the first tool to reach for when a displayed metric looks wrong during a live session.
