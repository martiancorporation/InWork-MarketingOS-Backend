"""All database enums in one place.

Each is a ``str`` enum so the stored value equals the member value (e.g.
``"admin"``). Used everywhere via ``pg_enum(EnumClass, "pg_type_name")``.
"""

from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    admin = "admin"      # full access: manage users, onboard clients, assign clients, see all
    manager = "manager"  # non-admin; sees only assigned clients (gets all client-capabilities on them)
    user = "user"        # non-admin; sees only assigned clients (capabilities scoped per assignment)


class ClientCapability(str, enum.Enum):
    """Per-project responsibility a user holds on a client they are assigned to.

    Granular RBAC layered on top of the bare client assignment: an assignment can
    grant a subset of these so, e.g., one operator only reviews results while
    another only sets up connectors. Stored as a JSON list on
    ``client_assignments.capabilities`` (an app-defined, growable set — not a
    native DB enum). Admins implicitly hold all capabilities on every client;
    managers hold all on their assigned clients; the ``admin`` capability below is
    a per-client super-grant that implies every other capability.
    """

    manage_integrations = "manage_integrations"  # connect/disconnect connectors
    review_results = "review_results"            # act on recommendations / KPI alerts
    review_creatives = "review_creatives"        # approve calendar content
    manage_calendar = "manage_calendar"          # create/edit calendar items
    manage_compliance = "manage_compliance"      # edit the compliance register
    admin = "admin"                              # per-client super-grant (implies all)


# ---- Client intelligence (async build pipeline + RAG) ----
# Stored as plain indexed String columns (not native PG enums) so these open-ish
# sets can grow without a migration, matching the ai_usage_events.status pattern.


class KnowledgeSourceType(str, enum.Enum):
    """Provenance of a knowledge source feeding the client profile."""

    document = "document"          # an uploaded file
    onboarding_field = "onboarding_field"  # a structured field group from the wizard
    note = "note"                  # free-text note / instruction
    website = "website"            # scraped site content


class SourceStatus(str, enum.Enum):
    pending = "pending"
    extracted = "extracted"
    embedded = "embedded"
    failed = "failed"
    unsupported = "unsupported"


class DirectiveType(str, enum.Enum):
    must = "must"            # required action/inclusion
    must_not = "must_not"    # hard prohibition
    prefer = "prefer"        # soft preference
    avoid = "avoid"          # soft avoidance
    constraint = "constraint"  # factual/technical constraint


class DirectiveTier(str, enum.Enum):
    """Priority tier. Higher tiers win conflicts and are never dropped."""

    mandatory = "mandatory"    # P0 — must_not / legal / explicit "do not"
    required = "required"      # P1 — explicit must
    preference = "preference"  # P2 — style choices
    inferred = "inferred"      # P3 — model-derived, lower confidence


class DirectiveStatus(str, enum.Enum):
    active = "active"
    superseded = "superseded"
    conflicted = "conflicted"  # opposing rules; needs human resolution


class ProfileStatus(str, enum.Enum):
    building = "building"
    ready = "ready"
    failed = "failed"
    superseded = "superseded"


class IntelJobType(str, enum.Enum):
    full_build = "full_build"    # re-extract & re-embed everything
    incremental = "incremental"  # only changed sources; reuse unchanged chunks


class IntelJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"  # exhausted retries


class ClientStatus(str, enum.Enum):
    draft = "draft"  # being set up (onboarding wizard not finished)
    active = "active"  # onboarding complete / live
    inactive = "inactive"  # paused or switched off
    paused = "paused"  # legacy alias of inactive (kept for existing rows)
    onboarding = "onboarding"  # legacy; superseded by ``draft``
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


# ---- Campaigns + KPI alerts ----
# Stored as plain indexed String columns (the ai_usage/knowledge precedent) so
# these sets stay portable across PG/SQLite and can grow without a migration.
# The enums here drive request-schema validation, not native PG enum types.


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    paused = "paused"
    ended = "ended"


class AlertKind(str, enum.Enum):
    """A watchdog signal: a problem to fix, or an opportunity to seize.

    Mirrors the web ``WatchdogItem.kind`` contract."""

    alert = "alert"
    opportunity = "opportunity"


class AlertSeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class AlertStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"


class NotificationLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class ConsistencyLevel(str, enum.Enum):
    """Severity of an onboarding cross-field consistency finding.

    Matches the web ``runConsistencyCheck`` contract (``ok`` / ``warn`` /
    ``error``)."""

    ok = "ok"
    warn = "warn"
    error = "error"

# Audit actions are deliberately NOT an enum: the app logs free-form, dotted
# action identifiers per feature (e.g. "report.pdf.exported",
# "recommendation.accepted", "integration.connect") and that set grows with
# every new feature. See ``AuditLog.action`` for the string column instead.
