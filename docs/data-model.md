# Data model

Design only. DDL will live in `sql/schemas/`.

## Event envelope

Defined in `src/commerce_pulse/common/events.py`. Fields: `schema_version`, `event_id`, `event_type`, `event_timestamp`, `ingestion_timestamp`, `customer_id`, `session_id`, `product_id`, `quantity`, `price`, `metadata`.

Per-type rules:

| event_type | customer + session | product_id | quantity + price |
|---|---|---|---|
| product_viewed | required | required | optional |
| product_added_to_cart | required | required | required, quantity >= 1 |
| checkout_started | required | optional | optional |
| order_created / completed / cancelled | required | required | required, quantity >= 1 |
| inventory_updated | not used | required | quantity is the new stock level |

## ClickHouse

| Table | Engine | Order by | Partition |
|---|---|---|---|
| `events_raw` | ReplacingMergeTree(processed_at) | (event_type, event_id) | toYYYYMM(event_timestamp) |
| `events_dead_letter` | MergeTree | (ingest_date, reason) | toYYYYMM(ingest_date) |
| `mart_sales_hourly` | AggregatingMergeTree | (hour, product_id) | toYYYYMM(hour) |
| `mart_funnel_daily` | AggregatingMergeTree | (day, product_id) | toYYYYMM(day) |
| `mart_demand_hourly` | AggregatingMergeTree | (hour, product_id) | toYYYYMM(hour) |
| `mart_inventory_current` | ReplacingMergeTree(event_timestamp) | (product_id) | none |

Demand spike detection compares a product's current-hour views and orders with its trailing 7-day average for the same hour of day.

## Late and duplicate data

Late events keep their original `event_timestamp`, so marts are recomputed for affected hours. Incremental jobs select on `ingest_date`, not `event_date`, to find them. Duplicates are removed by `event_id`; see `architecture.md`.
