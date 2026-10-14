"""Typed application settings, loaded once per process."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str = "loka"
    password: SecretStr = SecretStr("loka")
    database: str = "loka"
    pool_size: int = 20
    max_overflow: int = 10
    pool_timeout: int = 5
    pool_recycle: int = 1800
    statement_timeout_ms: int = 15_000
    application_name: str = "loka"

    @property
    def async_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def sync_dsn(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class BrokerSettings(BaseModel):
    url: SecretStr = SecretStr("amqp://loka:loka@localhost:5672/")
    prefetch: int = 64
    retry_delay_ms: int = 1000
    max_retries: int = 5
    dlq_suffix: str = ".dlq"
    publish_timeout_seconds: float = 5.0


class ObjectStorageSettings(BaseModel):
    endpoint: str = "http://localhost:9000"
    access_key: SecretStr = SecretStr("minioadmin")
    secret_key: SecretStr = SecretStr("minioadmin")
    bucket: str = "loka-media"
    region: str = "us-east-1"
    url_ttl_seconds: int = 300
    presigned_url_ttl_seconds: int = 900


class EncryptionSettings(BaseModel):
    key: SecretStr


class ModelPricing(BaseModel):
    """Per-million-token rates, in USD, for one model.

    Prices live in configuration rather than in the adapter because they change
    without a deploy, and a wrong price in code produces a wrong budget in
    silence.
    """

    input: float = Field(gt=0)
    output: float = Field(gt=0)


class AISettings(BaseModel):
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: SecretStr = SecretStr("")
    default_model: str = "google/gemini-2.5-flash"
    fallback_model: str = "google/gemini-2.5-pro"
    request_timeout_seconds: float = 12.0
    max_output_tokens: int = 1024
    temperature: float = 0.2
    daily_budget_usd: float = 25.0
    per_user_daily_budget_usd: float = 0.25
    pipeline_enabled: bool = False
    model_pricing_usd_per_million: dict[str, ModelPricing] = Field(default_factory=dict)
    # The decision-model pilot: asked on every turn, in shadow, never shown to
    # the user. Off by default; its cost comes back with each answer.
    decision_shadow_enabled: bool = False
    decision_model: str = "~typesafe/jev-latest"
    decision_timeout_seconds: float = 5.0

    def pricing_for(self, model: str) -> ModelPricing | None:
        return self.model_pricing_usd_per_million.get(model)

    def unpriced_models(self) -> set[str]:
        """Configured models the platform cannot cost.

        Enabling the pipeline without these is refused at boot: an uncosted
        call is an unbounded bill wearing a budget's clothes.
        """
        return {
            model
            for model in (self.default_model, self.fallback_model)
            if model not in self.model_pricing_usd_per_million
        }


class WhatsAppSettings(BaseModel):
    gateway_url: str = "http://localhost:3000"
    api_key: SecretStr = SecretStr("")
    request_timeout_seconds: float = 10.0
    session_name: str = "loka-main"
    max_outbound_per_minute: int = 120


class FapshiSettings(BaseModel):
    """Credentials and endpoints for Fapshi, the mobile-money provider.

    Sandbox and live are two hosts with two separate credential pairs, so the
    base URL is configuration rather than a flag derived from the key: a live
    key aimed at the sandbox host is refused with a 403, which is a loud
    failure instead of a test payment quietly taken for a real one.

    ``webhook_secret`` is not a signing key. Fapshi echoes this exact value in
    the ``x-wh-secret`` header, so it identifies the caller and proves nothing
    about the body — which is why the webhook is confirmed against the status
    endpoint before any payment settles.
    """

    base_url: str = "https://sandbox.fapshi.com"
    api_user: SecretStr = SecretStr("")
    api_key: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    request_timeout_seconds: float = 15.0
    # Where the payer lands after the checkout page. Empty on purpose: on
    # WhatsApp the conversation resumes in the thread, not on a web page, and
    # the confirmation the payer should trust is the one Kora sends.
    redirect_url: str = ""
    # Fapshi payment links live 24 hours. Kept here rather than in the
    # aggregate because it is the provider's rule, not the platform's.
    link_ttl_seconds: int = 86_400

    def is_configured(self) -> bool:
        return bool(
            self.api_user.get_secret_value()
            and self.api_key.get_secret_value()
            and self.webhook_secret.get_secret_value()
        )


class PaymentSettings(BaseModel):
    service_fee_xaf: int = 1000
    webhook_secret: SecretStr = SecretStr("change-me")
    gateway: str = "mock"
    callback_tolerance_seconds: int = 300
    fapshi: FapshiSettings = Field(default_factory=FapshiSettings)
    # A payment that stayed pending longer than this is reconciled against the
    # provider. Fapshi sends one webhook per event and never retries, so a
    # single dropped request is money taken and access never granted.
    reconcile_after_minutes: int = 10


class FraudSettings(BaseModel):
    medium_risk_threshold: int = 40
    high_risk_threshold: int = 70
    critical_risk_threshold: int = 90
    price_anomaly_ratio: float = 0.4
    max_active_properties_per_landlord: int = 25


class ReviewsSettings(BaseModel):
    """The two numbers the Avis et plaintes spec leaves to be set, not designed.

    ``complaint_hide_threshold`` is the N of §6: past this many complaints still
    owed an answer, Kora stops putting the provider forward until a human
    decides. It hides, it never bans. Set it too low and three bad-faith
    complaints take an honest landlord out of search; too high and a scammer
    keeps trading while the queue fills.

    ``complaint_response_time_hours`` is the delay the team promises itself. It
    is reported with the queue as a count of overdue complaints, because a human
    queue with no announced delay is a queue that grows in silence.

    Both defaults below are placeholders chosen to be safe rather than right:
    three complaints is more than one angry person can file alone, and 48 hours
    is two working days. The product owner still owes the real values.
    """

    complaint_hide_threshold: int = Field(default=3, ge=1)
    complaint_response_time_hours: int = Field(default=48, ge=1)


class ObservabilitySettings(BaseModel):
    log_level: str = "INFO"
    log_json: bool = True
    metrics_enabled: bool = True
    metrics_port: int = 9100
    tracing_enabled: bool = False
    otlp_endpoint: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: str = "development"
    service_name: str = "loka"

    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 100

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    object_storage: ObjectStorageSettings = Field(default_factory=ObjectStorageSettings)
    ai: AISettings = Field(default_factory=AISettings)
    whatsapp: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
    payment: PaymentSettings = Field(default_factory=PaymentSettings)
    fraud: FraudSettings = Field(default_factory=FraudSettings)
    reviews: ReviewsSettings = Field(default_factory=ReviewsSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)

    encryption_key: SecretStr = Field(
        default=SecretStr("Zm9yLWRldi1vbmx5LTMyLWJ5dGVzLWtleS0zMmJ5dGVzLWtleSE=")
    )
    outbound_webhook_signing_secret: SecretStr = SecretStr("change-me")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()