"""
QC endpoints:
  GET /api/qc/run         — run all Phase 1 checks and return report
  GET /api/qc/{train_id}  — full reconciliation trace for a single service
"""

import io
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from aggregation import aggregate_service, build_qc_trace

router = APIRouter()


@router.get("/api/qc/run", response_class=PlainTextResponse)
def run_qc(request: Request):
    """Run Phase 1 QC checks and return the report as plain text."""
    from qc_runner import run_phase1_qc

    store = request.app.state.store
    ref = request.app.state.ref

    # Capture stdout from the QC runner.
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        run_phase1_qc(store, ref)
    finally:
        sys.stdout = old_stdout

    return buf.getvalue()


@router.get("/api/qc/{train_id}")
def get_qc_trace(train_id: str, request: Request):
    store = request.app.state.store
    ref = request.app.state.ref

    messages = store.get_messages(train_id)
    if not messages:
        raise HTTPException(status_code=404, detail=f"No messages found for train_id {train_id!r}")

    svc = aggregate_service(train_id, messages, ref)
    trace = build_qc_trace(train_id, messages, svc)
    return trace
