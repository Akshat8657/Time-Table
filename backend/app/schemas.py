"""Request / response shapes of the API."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)  # never trimmed, never logged
    remember_me: bool = False

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("Please enter a valid email address.")
        return v


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: Literal["admin", "hod", "teacher"]
    school_id: str | None
    school_name: str | None
    specialization: str | None
    phone: str | None
    photo_url: str | None
    is_visiting: bool
    is_active: bool
    must_change_password: bool


class SessionOut(BaseModel):
    remember_me: bool
    idle_timeout_seconds: int
    idle_expires_in_seconds: int
    absolute_expires_in_seconds: int


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut
    session: SessionOut


class MeResponse(BaseModel):
    user: UserOut
    session: SessionOut
