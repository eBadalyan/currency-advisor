from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SourceHealthResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    base_currency: str
    quote_currency: str
    last_observed_at: datetime | None
    is_stale: bool
