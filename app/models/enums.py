"""All database enums in one place.

Each is a ``str`` enum so the stored value equals the member value (e.g.
``"admin"``). Used everywhere via ``pg_enum(EnumClass, "pg_type_name")``.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    strategist = "strategist"
    analyst = "analyst"
    client_viewer = "client_viewer"


class ClientStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    onboarding = "onboarding"
    archived = "archived"


class ClientPipelineStage(str, enum.Enum):
    """Lifecycle stage shown by the pipeline stepper in the client shell.

    Distinct from ``ClientStatus``: status is account state (paused, archived);
    pipeline stage is where the account is in its onboarding→retention journey.
    """

    onboarding = "onboarding"
    discovery = "discovery"
    active = "active"
    optimize = "optimize"
    retention = "retention"


class ContactSide(str, enum.Enum):
    client = "client"
    inwork = "inwork"


class IntegrationKey(str, enum.Enum):
    ga4 = "ga4"
    search_console = "search_console"
    google_ads = "google_ads"
    google_lsa = "google_lsa"
    meta = "meta"
    linkedin = "linkedin"  # added — present in the frontend integrations catalog


class IntegrationStatus(str, enum.Enum):
    connected = "connected"
    disconnected = "disconnected"
    error = "error"
    pending = "pending"


class ComplianceKind(str, enum.Enum):
    brand_voice = "brand_voice"
    banned = "banned"
    required = "required"
    rule = "rule"
    note = "note"


class EventType(str, enum.Enum):
    campaign = "campaign"
    email = "email"
    ad = "ad"
    review = "review"
    content = "content"
    meeting = "meeting"


class EventStage(str, enum.Enum):
    """Production lifecycle of a calendar item — independent of client
    ``approval_status``. Backs the calendar's "Drafts & Ideas" panel vs. the
    scheduled grid."""

    draft = "draft"
    scheduled = "scheduled"
    published = "published"
    archived = "archived"


class SocialPlatform(str, enum.Enum):
    instagram = "instagram"
    facebook = "facebook"
    youtube = "youtube"
    tiktok = "tiktok"
    x = "x"
    linkedin = "linkedin"
    pinterest = "pinterest"
    google = "google"
    email = "email"
    other = "other"


class ApprovalStatus(str, enum.Enum):
    approved = "approved"
    pending = "pending"
    changes_requested = "changes_requested"
    rejected = "rejected"


class AdObjective(str, enum.Enum):
    awareness = "awareness"
    traffic = "traffic"
    engagement = "engagement"
    leads = "leads"
    conversions = "conversions"


class TaskStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    blocked = "blocked"
    done = "done"


class TaskCategory(str, enum.Enum):
    strategy = "strategy"
    creative = "creative"
    ads = "ads"
    content = "content"
    analytics = "analytics"
    compliance = "compliance"
    admin = "admin"


class MessageFolder(str, enum.Enum):
    inbox = "inbox"
    sent = "sent"
    drafts = "drafts"
    archive = "archive"
    spam = "spam"
    trash = "trash"


class RecipientKind(str, enum.Enum):
    to = "to"
    cc = "cc"
    bcc = "bcc"


class ConversationSource(str, enum.Enum):
    email = "email"
    sms = "sms"
    whatsapp = "whatsapp"
    instagram = "instagram"
    facebook = "facebook"
    webform = "webform"
    internal = "internal"


class AiRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class AiSourceType(str, enum.Enum):
    document = "document"
    url = "url"
    ga4 = "ga4"
    search_console = "search_console"
    google_ads = "google_ads"
    meta = "meta"
    analytics_snapshot = "analytics_snapshot"


class ReportKind(str, enum.Enum):
    performance = "performance"
    compliance = "compliance"
    strategy = "strategy"
    executive = "executive"
    custom = "custom"


class ReportFormat(str, enum.Enum):
    """The three real output formats. "Save to Outlook draft" is a delivery
    option layered on top of any format, not a format itself — see
    ``Report.save_to_outlook_draft``."""

    pdf = "pdf"
    excel = "excel"
    visual = "visual"


class DocumentKind(str, enum.Enum):
    brand = "brand"
    compliance = "compliance"
    goals = "goals"
    contract = "contract"
    brief = "brief"
    creative = "creative"
    other = "other"


class RecommendationDecision(str, enum.Enum):
    accepted = "accepted"
    modified = "modified"
    rejected = "rejected"

# Audit actions are deliberately NOT an enum: the app logs free-form, dotted
# action identifiers per feature (e.g. "report.pdf.exported",
# "recommendation.accepted", "integration.connect") and that set grows with
# every new feature. See ``AuditLog.action`` for the string column instead.
