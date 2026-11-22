# Engineering decisions

Short records of choices that are not obvious, with what was rejected.

## At-least-once delivery with idempotent sinks

Kafka transactions give exactly-once only between Kafka topics. The sinks here
(object storage and ClickHouse) are outside that boundary, so claiming
exactly-once would be inaccurate. Instead the consumer commits offsets after
writes and sinks tolerate replays. Rejected: a two-phase commit between sinks,
which adds failure modes for little benefit at this scale.

## Dedup key is `event_id`, enforced in two places

An in-memory window drops the common case cheaply. `ReplacingMergeTree` ordered
by `(event_type, event_id)` is the backstop. Rejected: an external key-value
store (Redis) for dedup, because it adds a service whose loss would silently
reduce dedup quality.

## Validation is a single module

`common/events.py` is the only place that decides whether a payload is valid.
Producers use it to self-check, consumers to route to the DLQ, tests to build
fixtures. Unknown fields are ignored and `schema_version` is carried on every
event, so additive changes do not need a coordinated deploy. Breaking changes
require a new topic version (`.v2`).

## Pydantic instead of JSON Schema or Avro

JSON Schema alone cannot express rules like "orders need price and quantity"
without verbose conditionals. Avro with a schema registry is the better choice
when many teams share a topic; here there is one producer codebase and the
registry would be an extra service. Revisit if a second producer appears.

## Redpanda over Apache Kafka

Single binary, no ZooKeeper or KRaft setup, Kafka-compatible API, low memory use
in Docker Compose. The client code uses the Kafka protocol only, so it runs
against Kafka unchanged.

## ClickHouse for serving, Parquet for the system of record

Bronze Parquet in MinIO is the replayable source of truth. ClickHouse tables are
derived and can be rebuilt. Rejected: querying Parquet directly for the
dashboard, since latency on small recent windows is much better in ClickHouse.

## Polars for batch work, no dbt

Compaction and backfill read Parquet and are naturally dataframe work. Mart
logic is plain SQL files under `sql/transformations/` executed by Python, with
tests that run them against fixture data. dbt would add a dependency without
much benefit for roughly ten models.

## SQL assistant is read-only by construction

Validation of LLM-written SQL (single `SELECT`, allow-listed tables, forced
`LIMIT`) is a second line of defence. The first is a ClickHouse user with
`readonly=1` and a restricted grant, so a validator bug cannot cause writes.
The assistant must fail clearly when no LLM is configured instead of returning
canned answers.
