"""Admin-only API for aggregated agent telemetry (all JSONL runs under CODEMIND_TELEMETRY_DIR)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from codemind.api.auth import require_admin
from codemind.utils.agent_telemetry import list_telemetry_sessions, poll_telemetry_feed

router = APIRouter(prefix="/api/v1/admin/telemetry", tags=["admin-telemetry"])


class TelemetryPollBody(BaseModel):
    cursors: dict[str, int] = Field(default_factory=dict)
    session_limit: int = Field(50, ge=1, le=200)
    limit_per_run: int = Field(200, ge=1, le=2000)


@router.get("/sessions")
async def admin_telemetry_sessions(
    limit: int = Query(50, ge=1, le=200),
    _admin: dict = Depends(require_admin),
):
    """List recent telemetry JSONL sessions (newest first)."""
    return {"sessions": list_telemetry_sessions(limit=limit)}


@router.post("/poll")
async def admin_telemetry_poll(
    body: TelemetryPollBody,
    _admin: dict = Depends(require_admin),
):
    """Return new events across all tracked runs since per-run ``cursors`` line counts."""
    return poll_telemetry_feed(
        body.cursors,
        session_limit=body.session_limit,
        limit_per_run=body.limit_per_run,
    )
