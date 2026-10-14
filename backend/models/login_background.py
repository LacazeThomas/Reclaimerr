from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginBackgroundUpdate(BaseModel):
    mode: Literal["default", "upload", "urls"] = "default"
    urls: list[str] = Field(default_factory=list, max_length=50)
    interval_seconds: int = Field(default=5, ge=5, le=300)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = value.strip()
            if not value:
                continue
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and parsed.port != 0
                    and not parsed.username
                    and not parsed.password
                )
            except ValueError:
                valid = False
            if (
                len(value) > 4096
                or any(c.isspace() or ord(c) < 32 for c in value)
                or not valid
            ):
                raise ValueError(
                    "Use direct HTTP or HTTPS image URLs without credentials (maximum 4096 characters each)"
                )
            if value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def require_urls(self) -> "LoginBackgroundUpdate":
        if self.mode == "urls" and not self.urls:
            raise ValueError("Add at least one image URL")
        return self


class LoginBackgroundResponse(BaseModel):
    mode: Literal["default", "upload", "urls"] = "default"
    urls: list[str] = Field(default_factory=list)
    interval_seconds: int = 5
    image_url: str | None = None
