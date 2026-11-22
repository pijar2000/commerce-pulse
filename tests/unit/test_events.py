import json
from datetime import UTC, datetime, timedelta

import pytest

from commerce_pulse.common.events import (
    EventType,
    EventValidationError,
    parse_event,
)


def make_payload(**overrides):
    now = datetime.now(UTC)
    payload = {
        "event_id": "3f6c1d52-9a0e-4a43-8f0c-0e7b1d2a9c11",
        "event_type": "order_completed",
        "event_timestamp": (now - timedelta(seconds=3)).isoformat(),
        "ingestion_timestamp": now.isoformat(),
        "customer_id": "cust_000042",
        "session_id": "sess_9f21",
        "product_id": "sku_0007",
        "quantity": 2,
        "price": 19.99,
        "metadata": {"currency": "EUR"},
    }
    payload.update(overrides)
    return payload


def assert_rejected(raw, reason):
    with pytest.raises(EventValidationError) as info:
        parse_event(raw)
    assert info.value.reason == reason
    return info.value


def test_valid_event_round_trips_from_json_bytes():
    event = parse_event(json.dumps(make_payload()).encode())
    assert event.event_type is EventType.ORDER_COMPLETED
    assert event.quantity == 2
    assert event.event_timestamp.tzinfo is UTC


def test_invalid_json_is_reported():
    assert_rejected(b"{not json", "invalid_json")


def test_non_object_json_is_reported():
    assert_rejected("[1, 2, 3]", "not_an_object")


def test_missing_required_field():
    payload = make_payload()
    del payload["event_id"]
    error = assert_rejected(payload, "schema_violation")
    assert "event_id" in error.detail


def test_unknown_event_type():
    assert_rejected(make_payload(event_type="order_teleported"), "schema_violation")


def test_wrong_type_for_quantity():
    assert_rejected(make_payload(quantity="two"), "schema_violation")


def test_negative_price_rejected():
    assert_rejected(make_payload(price=-1), "schema_violation")


def test_naive_timestamp_rejected():
    naive = datetime.now(UTC).replace(tzinfo=None).isoformat()
    assert_rejected(make_payload(event_timestamp=naive), "schema_violation")


def test_future_timestamp_beyond_skew_rejected():
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert_rejected(make_payload(event_timestamp=future), "schema_violation")


def test_small_clock_skew_is_tolerated():
    near_future = (datetime.now(UTC) + timedelta(minutes=2)).isoformat()
    parse_event(make_payload(event_timestamp=near_future))


def test_delayed_event_is_valid():
    # Lateness is handled downstream; the contract only rejects impossible values.
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    parse_event(make_payload(event_timestamp=old))


def test_order_requires_price_and_quantity():
    assert_rejected(make_payload(price=None), "schema_violation")
    assert_rejected(make_payload(quantity=0), "schema_violation")


def test_product_view_requires_product_and_customer():
    base = make_payload(event_type="product_viewed", quantity=None, price=None)
    parse_event(base)
    assert_rejected({**base, "product_id": None}, "schema_violation")
    assert_rejected({**base, "customer_id": None}, "schema_violation")


def test_inventory_update_needs_no_customer_but_needs_stock_level():
    base = make_payload(
        event_type="inventory_updated",
        customer_id=None,
        session_id=None,
        price=None,
        quantity=0,
    )
    assert parse_event(base).quantity == 0
    assert_rejected({**base, "quantity": None}, "schema_violation")


def test_checkout_started_does_not_need_product():
    payload = make_payload(
        event_type="checkout_started", product_id=None, quantity=None, price=None
    )
    parse_event(payload)


def test_unknown_fields_are_ignored_for_forward_compatibility():
    event = parse_event(make_payload(new_field_from_v2="x"))
    assert not hasattr(event, "new_field_from_v2")
