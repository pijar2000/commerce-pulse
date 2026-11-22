"""Event contract shared by producers, consumers and the bronze writer.

Validation rules live here and nowhere else. Anything that reads events from
a topic goes through ``parse_event``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1

# Producers run on machines with imperfect clocks; a small skew is tolerated.
MAX_FUTURE_SKEW = timedelta(minutes=5)


class EventType(StrEnum):
    PRODUCT_VIEWED = "product_viewed"
    PRODUCT_ADDED_TO_CART = "product_added_to_cart"
    CHECKOUT_STARTED = "checkout_started"
    ORDER_CREATED = "order_created"
    ORDER_COMPLETED = "order_completed"
    ORDER_CANCELLED = "order_cancelled"
    INVENTORY_UPDATED = "inventory_updated"


# Types that must carry a positive quantity and a price.
_PRICED_TYPES = frozenset(
    {
        EventType.PRODUCT_ADDED_TO_CART,
        EventType.ORDER_CREATED,
        EventType.ORDER_COMPLETED,
        EventType.ORDER_CANCELLED,
    }
)
# Types that must reference a customer and a session.
_CUSTOMER_TYPES = frozenset(set(EventType) - {EventType.INVENTORY_UPDATED})


class EventValidationError(Exception):
    """Raised when a payload cannot be turned into an ``Event``.

    ``reason`` is a short stable code that is stored with dead-lettered
    records, so it can be grouped and counted.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class Event(BaseModel):
    # Unknown fields are ignored so producers can add fields without breaking
    # consumers. The untouched payload is kept in bronze either way.
    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = SCHEMA_VERSION
    event_id: str = Field(min_length=8, max_length=64)
    event_type: EventType
    event_timestamp: datetime
    ingestion_timestamp: datetime
    customer_id: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)
    product_id: str | None = Field(default=None, min_length=1, max_length=64)
    # For inventory_updated this is the resulting stock level, otherwise units.
    quantity: int | None = Field(default=None, ge=0)
    price: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_timestamp", "ingestion_timestamp")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a UTC offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _check_type_specific_fields(self) -> Self:
        if self.event_timestamp > datetime.now(UTC) + MAX_FUTURE_SKEW:
            raise ValueError("event_timestamp is in the future")
        if self.event_type in _CUSTOMER_TYPES and not (self.customer_id and self.session_id):
            raise ValueError(f"{self.event_type} requires customer_id and session_id")
        if self.event_type != EventType.CHECKOUT_STARTED and self.product_id is None:
            raise ValueError(f"{self.event_type} requires product_id")
        if self.event_type in _PRICED_TYPES and (not self.quantity or self.price is None):
            raise ValueError(f"{self.event_type} requires quantity >= 1 and price")
        if self.event_type == EventType.INVENTORY_UPDATED and self.quantity is None:
            raise ValueError("inventory_updated requires quantity (new stock level)")
        return self


def parse_event(raw: bytes | str | dict[str, Any]) -> Event:
    """Decode and validate one payload.

    Raises ``EventValidationError`` with ``reason`` set to one of
    ``invalid_json``, ``not_an_object`` or ``schema_violation``.
    """
    payload: Any = raw
    if isinstance(raw, bytes | str):
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EventValidationError("invalid_json", str(exc)) from exc
    if not isinstance(payload, dict):
        raise EventValidationError("not_an_object", type(payload).__name__)
    try:
        return Event.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<event>"
        raise EventValidationError("schema_violation", f"{location}: {first['msg']}") from exc
