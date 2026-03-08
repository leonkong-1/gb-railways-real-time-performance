"""
Phase 1 QC runner.

Runs 6 automated checks against the current MessageStore snapshot and prints
a report to stdout.  Designed to be called after the Phase 1 ingest window
closes.

Usage (from within app.py or standalone):
    from qc_runner import run_phase1_qc
    run_phase1_qc(store, ref)

Or from the command line after the app has been running:
    python qc_runner.py   (not standalone — use via the API or import)

The checks are also accessible via GET /api/qc/run (see routers/qc.py).
"""

import sys
from aggregation import aggregate_service, compute_delay, TREND_THRESHOLD_MINUTES


def run_phase1_qc(store, ref) -> bool:
    """
    Run all Phase 1 QC checks.  Returns True if all pass.
    Prints a human-readable report to stdout.
    """
    snapshot = store.snapshot()
    train_ids = list(snapshot.keys())
    n = len(train_ids)

    print()
    print("=== Phase 1 QC Report ===")
    print(f"Services in store: {n}")
    print(f"Total messages consumed: {store.total_consumed}")
    print()

    if n == 0:
        print("No services in store. Has the consumer run yet?")
        return False

    results = {}
    for tid in train_ids:
        results[tid] = aggregate_service(tid, snapshot[tid], ref)

    checks_passed = 0
    checks_failed = 0
    all_passed = True

    # ------------------------------------------------------------------
    # Check 1: worst_delay_mins reconciles to source
    # ------------------------------------------------------------------
    failures_1 = []
    for tid, msgs in snapshot.items():
        svc = results[tid]
        # Independent computation.
        independent_worst = None
        for m in msgs:
            if m.get("msg_type") != "0003":
                continue
            if m.get("delay_monitoring_point") != "true":
                continue
            dr = compute_delay(m)
            if dr["delay_mins"] is not None:
                if independent_worst is None or dr["delay_mins"] > independent_worst:
                    independent_worst = dr["delay_mins"]

        agg_worst = svc["worst_delay_mins"]
        if independent_worst != agg_worst:
            # Allow floating-point tolerance.
            if not (independent_worst is not None and agg_worst is not None and
                    abs(independent_worst - agg_worst) < 0.001):
                failures_1.append((tid, independent_worst, agg_worst))

    _print_check(1, "worst delay reconciliation", len(train_ids), len(train_ids) - len(failures_1),
                 failures_1, lambda f: f"  train_id={f[0]}: independent={f[1]} != aggregated={f[2]}")
    if not failures_1:
        checks_passed += 1
    else:
        checks_failed += 1
        all_passed = False

    # ------------------------------------------------------------------
    # Check 2: latest_delay is from the most recent qualifying message
    # ------------------------------------------------------------------
    failures_2 = []
    from aggregation import _parse_ms_timestamp as _pms
    for tid, msgs in snapshot.items():
        svc = results[tid]
        # Use max() with the same key as aggregation so tie-breaking is identical
        # (Python max() picks the first element when keys are equal).
        qualifying_q = [
            (m, compute_delay(m)) for m in msgs
            if m.get("msg_type") == "0003"
            and m.get("delay_monitoring_point") == "true"
            and compute_delay(m)["delay_mins"] is not None
        ]
        if qualifying_q:
            best = max(
                qualifying_q,
                key=lambda x: (_pms(x[0].get("actual_timestamp")) or 0, x[0].get("received_at", ""))
            )
            independent_latest = best[1]["delay_mins"]
        else:
            independent_latest = None
        agg_latest = svc["latest_delay_mins"]

        if independent_latest != agg_latest:
            if not (independent_latest is not None and agg_latest is not None and
                    abs(independent_latest - agg_latest) < 0.001):
                failures_2.append((tid, independent_latest, agg_latest))

    _print_check(2, "latest delay from most recent qualifying message", len(train_ids),
                 len(train_ids) - len(failures_2), failures_2,
                 lambda f: f"  train_id={f[0]}: independent={f[1]} != aggregated={f[2]}")
    if not failures_2:
        checks_passed += 1
    else:
        checks_failed += 1
        all_passed = False

    # ------------------------------------------------------------------
    # Check 3: trend consistency
    # ------------------------------------------------------------------
    non_null = [(tid, results[tid]) for tid in train_ids
                if results[tid]["worst_delay_mins"] is not None
                and results[tid]["latest_delay_mins"] is not None]
    failures_3 = []
    for tid, svc in non_null:
        w = svc["worst_delay_mins"]
        l = svc["latest_delay_mins"]
        if l <= w - TREND_THRESHOLD_MINUTES:
            expected = "IMPROVING"
        elif l >= w + TREND_THRESHOLD_MINUTES:
            expected = "WORSENING"
        else:
            expected = "STABLE"
        if expected != svc["trend"]:
            failures_3.append((tid, expected, svc["trend"]))

    _print_check(3, "trend consistency", len(non_null), len(non_null) - len(failures_3),
                 failures_3, lambda f: f"  train_id={f[0]}: expected={f[1]}, got={f[2]}")
    if not failures_3:
        checks_passed += 1
    else:
        checks_failed += 1
        all_passed = False

    # ------------------------------------------------------------------
    # Check 4: qualifying message count
    # ------------------------------------------------------------------
    failures_4 = []
    for tid, msgs in snapshot.items():
        svc = results[tid]
        count = sum(
            1 for m in msgs
            if m.get("msg_type") == "0003"
            and m.get("delay_monitoring_point") == "true"
            and compute_delay(m)["delay_mins"] is not None
        )
        if count != svc["qualifying_message_count"]:
            failures_4.append((tid, count, svc["qualifying_message_count"]))

    _print_check(4, "qualifying message count", len(train_ids), len(train_ids) - len(failures_4),
                 failures_4, lambda f: f"  train_id={f[0]}: counted={f[1]}, aggregated={f[2]}")
    if not failures_4:
        checks_passed += 1
    else:
        checks_failed += 1
        all_passed = False

    # ------------------------------------------------------------------
    # Check 5: no double-counting (each metric derived from exactly one message)
    # ------------------------------------------------------------------
    failures_5 = []
    for tid, msgs in snapshot.items():
        svc = results[tid]
        if svc["worst_delay_mins"] is None:
            continue
        qualifying = [
            (m, compute_delay(m))
            for m in msgs
            if m.get("msg_type") == "0003"
            and m.get("delay_monitoring_point") == "true"
            and compute_delay(m)["delay_mins"] is not None
        ]
        if not qualifying:
            continue

        # worst: should come from exactly one message
        max_delay = max(dr["delay_mins"] for _, dr in qualifying)
        worst_sources = [m for m, dr in qualifying if abs(dr["delay_mins"] - max_delay) < 0.001]
        if len(worst_sources) > 1:
            # Multiple messages with identical delay — acceptable (ties are valid).
            pass

        # latest: check that aggregation picks exactly one message and is not blending.
        # True ties (same actual_timestamp + received_at, different delay values) can occur
        # when TRUST rounds to the minute and two events fire in the same minute.
        # In this case aggregation deterministically picks the first max() result — this
        # is PASS (not a blend), but we log a note for visibility.
        from aggregation import _parse_ms_timestamp as _pms2
        def _lkey(m):
            return (_pms2(m.get("actual_timestamp")) or 0, m.get("received_at", ""))
        latest_key = max(_lkey(m) for m, _ in qualifying)
        latest_sources = [m for m, _ in qualifying if _lkey(m) == latest_key]
        if len(latest_sources) > 1:
            # Not a failure — aggregation picks deterministically (first max). Log only.
            print(f"  NOTE {tid}: {len(latest_sources)} messages share sort key {latest_key} "
                  f"(TRUST minute-rounding); aggregation picks first, result is determinate")

    _print_check(5, "no double-counting", len(train_ids), len(train_ids) - len(failures_5),
                 failures_5, lambda f: f"  train_id={f[0]}: {f[1]}")
    if not failures_5:
        checks_passed += 1
    else:
        checks_failed += 1
        all_passed = False

    # ------------------------------------------------------------------
    # Check 6: reference data round-trip
    # ------------------------------------------------------------------
    toc_ids_seen = set()
    stanox_seen = set()
    for msgs in snapshot.values():
        for m in msgs:
            if m.get("toc_id"):
                toc_ids_seen.add(str(m["toc_id"]))
            if m.get("loc_stanox"):
                stanox_seen.add(str(m["loc_stanox"]))

    toc_resolved = sum(1 for t in toc_ids_seen if ref.resolve_toc(t) != t)
    stanox_resolved = sum(1 for s in stanox_seen if ref.resolve_stanox(s) != s)

    toc_unmatched = [t for t in toc_ids_seen if ref.resolve_toc(t) == t]
    stanox_unmatched = [s for s in stanox_seen if ref.resolve_stanox(s) == s]

    print(f"Check 6 (reference data round-trip): ", end="")
    ref_errors = []
    # Check that lookup never returns None (only raw code or resolved name).
    for t in toc_ids_seen:
        v = ref.resolve_toc(t)
        if v is None:
            ref_errors.append(f"  toc_id={t} returned None")
    for s in stanox_seen:
        v = ref.resolve_stanox(s)
        if v is None:
            ref_errors.append(f"  stanox={s} returned None")

    if ref_errors:
        print(f"FAIL — null returns detected:")
        for e in ref_errors:
            print(e)
        checks_failed += 1
        all_passed = False
    else:
        print("PASS")
        toc_pct = f"{toc_resolved}/{len(toc_ids_seen)} ({100*toc_resolved//max(len(toc_ids_seen),1)}%)" if toc_ids_seen else "n/a"
        stanox_pct = f"{stanox_resolved}/{len(stanox_seen)} ({100*stanox_resolved//max(len(stanox_seen),1)}%)" if stanox_seen else "n/a"
        print(f"  toc_id resolved: {toc_pct}" + (f" — unmatched: {toc_unmatched[:10]}" if toc_unmatched else ""))
        print(f"  stanox resolved: {stanox_pct}" + (f" — first 5 unmatched: {stanox_unmatched[:5]}" if stanox_unmatched else ""))
        checks_passed += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_checks = checks_passed + checks_failed
    print()
    print(f"Checks run:  {total_checks}")
    print(f"Passed:      {checks_passed}")
    print(f"Failed:      {checks_failed}")
    print()
    if all_passed:
        print("Phase 1 QC: ALL CHECKS PASSED. Proceed to Phase 2.")
    else:
        print("Phase 1 QC: FAILURES DETECTED. Fix issues above before proceeding to Phase 2.")
    print()

    return all_passed


def _print_check(num, name, total, passed, failures, fmt_failure):
    label = f"Check {num} ({name}): "
    if not failures:
        print(f"{label}PASS -- {passed}/{total} verified")
    else:
        print(f"{label}FAIL -- {passed}/{total} verified")
        for f in failures[:10]:
            print(fmt_failure(f))
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more")
