from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProtocolInput(BaseModel):
    protocol_id: str
    title: str = "Untitled Protocol"
    source: str = "manual_input"
    raw_text: str


class ParsedStep(BaseModel):
    step_id: str
    raw_text: str
    action: str
    entities: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ParsedProtocol(BaseModel):
    protocol_id: str
    steps: list[ParsedStep]


class ApiCall(BaseModel):
    call_id: str
    api: str
    args: dict[str, Any] = Field(default_factory=dict)
    source_step_id: str | None = None


class Workflow(BaseModel):
    workflow_id: str
    protocol_id: str
    api_calls: list[ApiCall]


class ExecutionEvent(BaseModel):
    timestamp: datetime
    call_id: str
    api: str
    args: dict[str, Any] = Field(default_factory=dict)
    success: bool
    message: str = ""


class ExecutionResult(BaseModel):
    workflow_id: str
    success: bool
    executed_calls: int
    events: list[ExecutionEvent]
    final_state: dict[str, Any] = Field(default_factory=dict)
    state_snapshots: list[dict[str, Any]] = Field(default_factory=list)
