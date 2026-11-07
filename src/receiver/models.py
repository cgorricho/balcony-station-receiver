"""Request and response schemas for the API contract (spec section 5)."""

import math
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, model_validator

MAX_BATCH = 500
MAX_TEXT = 64
SQLITE_INT = 2**63 - 1

Seq = Annotated[int, Field(strict=True, ge=0, le=SQLITE_INT)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)]


def _check_metric_value(value: Any) -> Any:
    """Allow finite numbers and short strings that SQLite can store."""
    if isinstance(value, bool):
        raise ValueError("metric value must be a number or short string")
    if isinstance(value, int):
        if abs(value) > SQLITE_INT:
            raise ValueError("metric value is out of range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metric value must be finite")
    elif isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise ValueError(f"metric text is longer than {MAX_TEXT} characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("metric text is not valid unicode") from None
    else:
        raise ValueError("metric value must be a number or short string")
    return value


MetricValue = Annotated[int | float | str, BeforeValidator(_check_metric_value)]


class Reading(BaseModel):
    seq: Seq
    ts: Seq
    ts_synced: bool = Field(strict=True)
    metrics: dict[ShortText, MetricValue]


class ReadingBatch(BaseModel):
    node_id: ShortText
    fw_version: ShortText | None = None
    oldest_buffered_seq: Seq
    readings: list[Reading] = Field(min_length=1, max_length=MAX_BATCH)

    @model_validator(mode="after")
    def oldest_is_not_above_batch(self) -> "ReadingBatch":
        if self.oldest_buffered_seq > min(r.seq for r in self.readings):
            raise ValueError("oldest_buffered_seq is above the lowest seq in the batch")
        return self


class IngestResponse(BaseModel):
    stored: int
    duplicates: int
    ack_seq: int | None
    server_ts: int


class TimeResponse(BaseModel):
    server_ts: int


class HealthResponse(BaseModel):
    status: str
    db: str
    last_reading_at: int | None
    nodes: list[str]


class LatestResponse(BaseModel):
    node_id: str
    seq: int
    ts: int
    ts_synced: bool
    received_at: int
    fw_version: str | None
    metrics: dict[str, float | str | None]
