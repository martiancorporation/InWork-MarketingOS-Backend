"""Model registry.

Importing this package imports every model so they are registered on
``Base.metadata`` (required for Alembic autogenerate and relationship
resolution). Keep this list in sync when adding a new model file.
"""

from app.models.ai import AiChat, AiChatMessage, AiChatSource, AiSource
from app.models.ai_usage import AiUsageEvent
from app.models.analytics import AnalyticsDaily, StrategyVisual
from app.models.assignment import ClientAssignment
from app.models.audit import AuditLog
from app.models.client import (
    Client,
    ClientBrandColor,
    ClientBrandFont,
    ClientPlatform,
)
from app.models.compliance import ComplianceDoc, ComplianceEntry
from app.models.contact import ClientContact
from app.models.conversation import (
    Conversation,
    Message,
    MessageAttachment,
    MessageRecipient,
)
from app.models.document import Document
from app.models.event import (
    EventActivity,
    EventAd,
    EventAsset,
    EventPost,
    MarketingEvent,
)
from app.models.integration import Integration
from app.models.plan import PlanTask
from app.models.recommendation import RecommendationAction
from app.models.report import Report
from app.models.upload import Upload
from app.models.user import User, UserSession

__all__ = [
    "AiChat",
    "AiChatMessage",
    "AiChatSource",
    "AiSource",
    "AiUsageEvent",
    "AnalyticsDaily",
    "AuditLog",
    "Client",
    "ClientAssignment",
    "ClientBrandColor",
    "ClientBrandFont",
    "ClientContact",
    "ClientPlatform",
    "ComplianceDoc",
    "ComplianceEntry",
    "Conversation",
    "Document",
    "EventActivity",
    "EventAd",
    "EventAsset",
    "EventPost",
    "Integration",
    "MarketingEvent",
    "Message",
    "MessageAttachment",
    "MessageRecipient",
    "PlanTask",
    "RecommendationAction",
    "Report",
    "StrategyVisual",
    "Upload",
    "User",
    "UserSession",
]
