"""Pydantic models shared by the API, engine and tests."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["PLAYING", "WON", "LOST"]


class CheckpointOut(BaseModel):
    name: str
    km: int


class TurnRecord(BaseModel):
    turn: int
    action: str
    narrative: str
    time_delta: int
    cash_delta: int
    battery_delta: int
    checkpoint_before: int
    checkpoint_after: int
    clock_after: str
    guardrail_notes: list[str] = Field(default_factory=list)


class GameState(BaseModel):
    session_id: str
    turn: int = 0
    elapsed_mins: int = 0
    battery: int = 4
    cash: int = 500
    has_samosa: bool = True
    friend_called: bool = False
    weather: str = "Humid haze"
    is_raining: bool = False
    checkpoint_idx: int = 0
    status: Status = "PLAYING"
    end_reason: str = ""
    active_situation: str = ""
    npc_memory: list[str] = Field(default_factory=list)
    history: list[TurnRecord] = Field(default_factory=list)


class TurnRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=128)
    action: str = Field(min_length=1, max_length=600)
    client_state: GameState
    state_signature: str = Field(min_length=16, max_length=128)


class StartResponse(BaseModel):
    state: GameState
    state_signature: str
    checkpoints: list[CheckpointOut]
    clock: str


class TurnResponse(BaseModel):
    state: GameState
    state_signature: str
    clock: str
    narrator: str = "local"
