"""AI assistant API (plan Section 16). Every role may use it; WHAT it will do for them is decided in the service, with the
person's own role and school lock (see services/assistant.py).

  GET  /api/assistant/status    is the AI available right now, example commands for this role
  POST /api/assistant/chat      one message (+ the last few turns of the chat) -> an answer, a Confirm card or a question
  POST /api/assistant/choose    the person tapped one of our "which one do you mean?" options (no AI call)
  POST /api/assistant/confirm   the person pressed Confirm on a card -> the action runs (audited via='ai')
  POST /api/assistant/cancel    the person pressed Cancel on a card
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..security.deps import CurrentUser, require_roles
from ..services import assistant as svc

router = APIRouter(prefix="/api/assistant", tags=["assistant"])
ALL = ("admin", "hod", "teacher")


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    text: str = Field(default="", max_length=800)


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=svc.MESSAGE_LIMIT)
    history: list[Turn] = Field(default_factory=list, max_length=20)


class CardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=8, max_length=100)


class ChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice_id: str = Field(min_length=8, max_length=100)


@router.get("/status")
async def status(user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    return await svc.status(user)


@router.post("/chat")
async def chat(body: ChatIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    return await svc.chat(user, body.message, [t.model_dump() for t in body.history])


@router.post("/choose")
async def choose(body: ChoiceIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    return await svc.choose(user, body.choice_id)


@router.post("/confirm")
async def confirm(body: CardIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    return await svc.confirm(user, body.action_id)


@router.post("/cancel")
async def cancel(body: CardIn, user: CurrentUser = Depends(require_roles(*ALL))) -> dict:
    return await svc.cancel(user, body.action_id)
