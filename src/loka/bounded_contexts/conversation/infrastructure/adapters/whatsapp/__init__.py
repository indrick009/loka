"""WhatsApp, behind one port: Baileys today, the official Cloud API tomorrow.

The conversation talks to :class:`WhatsAppChannel` (``application/ports``) and
never to a provider. Two adapters implement it, and ``WHATSAPP__PROVIDER``
picks one — nothing else changes:

- ``baileys``: the unofficial gateway (``deploy/baileys``), for the tests;
- ``cloud``: Meta's WhatsApp Cloud API, the target.

On the way in, the same holds: ``interfaces/http/routers/whatsapp`` (Baileys)
and ``routers/whatsapp_cloud`` (Meta's webhook) turn a delivery into the same
``GatewayMessage``. Each adapter shows a :class:`RichReply` as richly as it can:
buttons and lists on the Cloud API, the sentence alone on Baileys.
"""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.ports import WhatsAppChannel
from loka.bounded_contexts.conversation.infrastructure.adapters.whatsapp.baileys import (
    BaileysChannel,
)
from loka.bounded_contexts.conversation.infrastructure.adapters.whatsapp.cloud_api import (
    CloudApiChannel,
)
from loka.bounded_contexts.conversation.infrastructure.adapters.whatsapp.delivery import (
    GatewayDown,
)
from loka.shared.infrastructure.config.settings import Settings

__all__ = ["BaileysChannel", "CloudApiChannel", "GatewayDown", "channel_for"]


def channel_for(settings: Settings) -> WhatsAppChannel:
    """The adapter ``WHATSAPP__PROVIDER`` names."""
    if settings.whatsapp.provider == "cloud":
        return CloudApiChannel(settings)
    return BaileysChannel(settings)
