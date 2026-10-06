"""Prometheus metrics.

Business funnel, technical latency and AI cost all share one registry so a
single scrape endpoint covers platform health and product health.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

registry = CollectorRegistry()

http_requests_total = Counter(
    "loka_http_requests_total",
    "HTTP requests by method, route and status.",
    ["method", "route", "status"],
    registry=registry,
)
http_latency_seconds = Histogram(
    "loka_http_latency_seconds",
    "HTTP latency in seconds.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=registry,
)
db_query_seconds = Histogram(
    "loka_db_query_seconds",
    "SQLAlchemy statement latency.",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 3),
    registry=registry,
)
cache_operations_total = Counter(
    "loka_cache_operations_total", "Redis cache hits and misses.", ["result"], registry=registry
)
broker_publish_total = Counter(
    "loka_broker_publish_total",
    "Messages published to the broker.",
    ["topic", "outcome"],
    registry=registry,
)
consumer_processing_total = Counter(
    "loka_consumer_processing_total",
    "Messages processed by consumers.",
    ["queue", "outcome"],
    registry=registry,
)
whatsapp_failures_total = Counter(
    "loka_whatsapp_failures_total",
    "WhatsApp gateway failures by operation.",
    ["operation", "reason"],
    registry=registry,
)
llm_calls_total = Counter(
    "loka_llm_calls_total",
    "LLM invocations by use case, model and outcome.",
    ["use_case", "model", "outcome"],
    registry=registry,
)
llm_tokens_total = Counter(
    "loka_llm_tokens_total",
    "LLM tokens consumed.",
    ["model", "direction"],
    registry=registry,
)
llm_cost_usd_total = Counter(
    "loka_llm_cost_usd_total", "Estimated LLM spend.", ["use_case"], registry=registry
)
llm_latency_seconds = Histogram(
    "loka_llm_latency_seconds",
    "LLM end-to-end latency.",
    ["use_case", "model"],
    buckets=(0.2, 0.5, 1, 2, 4, 8, 16, 30),
    registry=registry,
)
ai_bypass_total = Counter(
    "loka_ai_bypass_total",
    "Conversations resolved deterministically without calling an LLM.",
    ["stage"],
    registry=registry,
)
fraud_evaluations_total = Counter(
    "loka_fraud_evaluations_total",
    "Risk evaluations by outcome.",
    ["subject", "band", "action"],
    registry=registry,
)
business_landlords_total = Gauge(
    "loka_business_landlords", "Registered landlords.", ["status"], registry=registry
)
business_properties_total = Gauge(
    "loka_business_properties", "Properties by status.", ["status"], registry=registry
)
business_funnel_total = Counter(
    "loka_business_funnel_total",
    "Conversion funnel events.",
    ["stage"],
    registry=registry,
)
outbox_backlog = Gauge(
    "loka_outbox_backlog", "Unpublished outbox rows.", registry=registry
)


def render() -> bytes:
    return generate_latest(registry)