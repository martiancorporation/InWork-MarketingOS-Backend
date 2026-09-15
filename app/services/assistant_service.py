"""Project AI assistant use-cases ("Ask AI about this project").

Client-access scoping is enforced at the router via ``ClientService.get_client``
(inaccessible client → 404). Repos flush; this service owns the commit. The
question runs through ``ProjectAssistantAgent``, grounded in the client's
intelligence context + RAG store, with a deterministic fallback when the AI
provider is unconfigured.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from anyio import to_thread
from sqlalchemy.orm import Session

from app.ai.assistant import AssistantStreamPrep, ProjectAssistantAgent
from app.ai.attachments import MAX_ATTACHMENTS, AttachmentBundle, build_bundle
from app.ai.features import AiFeature
from app.ai.plan_chat_intent import PlanChatIntent, PlanChatIntentAgent
from app.ai.usage import AiUsageContext
from app.core.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError
from app.core.pagination import PaginationParams
from app.integrations.embeddings import get_embedder
from app.integrations.llm import get_llm_client
from app.integrations.storage import Storage
from app.models.ai import AiChat, AiChatMessage
from app.models.client import Client
from app.models.enums import AiRole
from app.models.user import User
from app.repositories.ai_chat_repository import AiChatRepository
from app.schemas.assistant import (
    AssistantAskResponse,
    AssistantAttachmentRead,
    AssistantChatCreate,
    AssistantChatDetail,
    AssistantChatListResponse,
    AssistantChatRead,
    AssistantMessageRead,
    PlanDraftAction,
    PlanDraftItemSummary,
)
from app.services.plan_generation_service import PlanGenerationService
from app.services.upload_service import UploadService
from app.utils.download_link import upload_permalink
from app.utils.timezones import client_local_today

logger = logging.getLogger("app.services.assistant")

# Cap how much prior conversation is replayed into the LLM prompt on every
# turn. Without a bound, a long-running chat sends an ever-growing transcript
# on every single message — token cost (and latency) grows without limit as
# the conversation gets longer. 40 messages is ~20 user/assistant turns, well
# past what's useful for the model to stay grounded in the recent thread.
_MAX_LLM_HISTORY_MESSAGES = 40

# How many drafted items to preview inline on the chat card — the full list
# is always on the real Content Calendar; the card just needs enough for the
# manager to recognize what was drafted before approving.
_MAX_PREVIEW_ITEMS = 8


@dataclass
class StreamContext:
    """Prepared state for a streamed answer (built before the SSE body starts).

    ``agent``/``prep`` are set for the normal conversational path; a chat
    message that turned out to be a content-plan request (a clarifying
    question, or an already-generated draft) instead sets
    ``immediate_reply``/``immediate_action`` — delivered as a single SSE frame
    rather than animated token-by-token, since there's nothing to stream (a
    short question, or a result that already exists in full).
    """

    client_id: uuid.UUID
    chat_id: uuid.UUID
    agent: ProjectAssistantAgent | None = None
    prep: AssistantStreamPrep | None = None
    immediate_reply: str | None = None
    immediate_action: dict | None = None


def _sse(payload: dict) -> str:
    """One Server-Sent Events frame (``data: {...}\\n\\n``)."""
    return f"data: {json.dumps(payload)}\n\n"


class AssistantService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.chats = AiChatRepository(db)

    def list_chats(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        context_type: str | None = None,
    ) -> AssistantChatListResponse:
        rows, total = self.chats.list_for_client(
            client_id,
            context_type=context_type,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return AssistantChatListResponse(
            items=[AssistantChatRead.model_validate(c) for c in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def create_chat(
        self, client_id: uuid.UUID, user_id: uuid.UUID, data: AssistantChatCreate
    ) -> AiChat:
        chat = self.chats.create_chat(
            client_id,
            user_id,
            title=data.title,
            context_type=data.context_type or "project",
            context_key=data.context_key,
        )
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def get_chat_detail(
        self, client_id: uuid.UUID, chat_id: uuid.UUID, *, pagination: PaginationParams
    ) -> AssistantChatDetail:
        chat = self._require_chat(client_id, chat_id)
        messages, total = self.chats.list_messages_page(
            chat_id, offset=pagination.offset, limit=pagination.limit
        )
        return AssistantChatDetail(
            id=chat.id,
            title=chat.title,
            context_type=chat.context_type,
            context_key=chat.context_key,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            messages=[self._message_read(m) for m in messages],
            messages_total=total,
            messages_page=pagination.page,
            messages_page_size=pagination.page_size,
        )

    @staticmethod
    def _message_read(message: AiChatMessage) -> AssistantMessageRead:
        """Serialise a message, building a permanent download link per attachment.

        The link is pure string construction (no S3 call) — it never expires
        itself, it always redirects to a freshly presigned URL when followed. See
        ``app/utils/download_link.py``.
        """
        read = AssistantMessageRead.model_validate(message)
        meta = message.meta or {}
        records = meta.get("attachments") or []
        for record in records:
            content_type = record.get("content_type") or ""
            read.attachments.append(
                AssistantAttachmentRead(
                    upload_id=record["upload_id"],
                    filename=record.get("filename") or "attachment",
                    content_type=content_type or None,
                    size_bytes=record.get("size_bytes"),
                    kind="image" if content_type.startswith("image/") else "file",
                    download_url=upload_permalink(record["upload_id"]),
                )
            )
        action_data = meta.get("action")
        if action_data:
            try:
                read.action = PlanDraftAction.model_validate(action_data)
            except Exception:
                logger.warning("Malformed plan-draft action on message %s", message.id)
        return read

    def delete_chat(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> None:
        chat = self._require_chat(client_id, chat_id)
        self.chats.delete_chat(chat)
        self.db.commit()

    async def ask(
        self,
        client_id: uuid.UUID,
        chat_id: uuid.UUID,
        user: User,
        content: str,
        *,
        attachment_upload_ids: list[uuid.UUID] | None = None,
        storage: Storage | None = None,
    ) -> AssistantAskResponse:
        chat = self._require_chat(client_id, chat_id)
        history = [
            (m.role.value, m.content)
            for m in self.chats.list_messages(chat_id, limit=_MAX_LLM_HISTORY_MESSAGES)
        ]

        bundle, meta = await self._resolve_attachments(user, attachment_upload_ids, storage)
        self.chats.add_message(chat_id, AiRole.user, content, meta=meta)

        plan_reply = await self._maybe_handle_plan_request(client_id, content, history, actor=user)
        if plan_reply is not None:
            reply_text, action_payload = plan_reply
            assistant_msg = self.chats.add_message(
                chat_id,
                AiRole.assistant,
                reply_text,
                meta={"action": action_payload} if action_payload else None,
            )
            chat.updated_at = datetime.now(UTC)
            self.db.commit()
            self.db.refresh(assistant_msg)
            return AssistantAskResponse(message=self._message_read(assistant_msg), sources=[])

        agent = ProjectAssistantAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=get_llm_client(
                AiUsageContext(feature=AiFeature.PROJECT_AI, client_id=client_id, user_id=user.id)
            ),
        )
        answer, sources = await agent.answer(content, history=history, attachments=bundle)

        assistant_msg = self.chats.add_message(chat_id, AiRole.assistant, answer)
        chat.updated_at = datetime.now(UTC)  # bump so recent chats sort first
        self.db.commit()
        self.db.refresh(assistant_msg)
        return AssistantAskResponse(message=self._message_read(assistant_msg), sources=sources)

    async def _maybe_handle_plan_request(
        self,
        client_id: uuid.UUID,
        content: str,
        history: list[tuple[str, str]],
        *,
        actor: User,
    ) -> tuple[str, dict | None] | None:
        """Runs the cheap intent classifier ahead of the normal chat reply.

        Returns ``None`` when the message isn't a content-plan request at all
        (the caller falls through to the normal conversational agent — this is
        the overwhelmingly common case, and behaves identically to before this
        feature existed). Otherwise returns ``(reply_text, action_payload)``:
        a clarifying question (``action_payload=None``) when the date range
        isn't clear yet, or a short confirmation + the drafted batch's action
        payload once generation has actually happened.
        """
        client = self.db.get(Client, client_id)
        today = client_local_today(client.timezone if client else None)
        intent_agent = PlanChatIntentAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=get_llm_client(
                AiUsageContext(
                    feature=AiFeature.PLAN_CHAT_INTENT, client_id=client_id, user_id=actor.id
                )
            ),
        )
        intent = await intent_agent.classify(content, history=history, today=today)
        if not intent.wants_content_plan:
            return None
        if not intent.ready:
            return intent.clarifying_question, None
        return await self._generate_plan_from_chat(client_id, intent, actor=actor)

    async def _generate_plan_from_chat(
        self, client_id: uuid.UUID, intent: PlanChatIntent, *, actor: User
    ) -> tuple[str, dict]:
        assert intent.start_date is not None and intent.end_date is not None
        items = await PlanGenerationService(self.db).propose_range(
            client_id, "", start_date=intent.start_date, end_date=intent.end_date, user=actor
        )
        if not items:
            return (
                "I wasn't able to draft anything for that range — try describing "
                "what you'd like differently, or a different date range.",
                {},
            )

        preview = [
            PlanDraftItemSummary(task_id=item.id, title=item.title, event_date=item.due_date)
            for item in items[:_MAX_PREVIEW_ITEMS]
            if item.due_date is not None
        ]
        action = PlanDraftAction(
            status="pending",
            start_date=intent.start_date,
            end_date=intent.end_date,
            task_ids=[item.id for item in items],
            items=preview,
        )
        reply = (
            f"I've drafted {len(items)} post(s) for "
            f"{intent.start_date.isoformat()} to {intent.end_date.isoformat()} — "
            "take a look below and approve it, or let me know what to change."
        )
        return reply, action.model_dump(mode="json")

    async def _resolve_attachments(
        self,
        user: User,
        upload_ids: list[uuid.UUID] | None,
        storage: Storage | None,
    ) -> tuple[AttachmentBundle | None, dict | None]:
        """Fetch the attached uploads and resolve them into model input + stored meta.

        ``UploadService.read_bytes`` is owner-scoped — someone else's upload id (or a
        made-up one) raises ``NotFoundError`` → 404, so an id can't be used to probe
        another user's files. It is also *blocking* S3 I/O inside an async handler, so
        each read is offloaded to a thread (same as the brand-extraction route).

        Only the storage key is recorded, never the presigned URL: those last 15
        minutes, and persisting one would leave the chat full of dead links.
        """
        if not upload_ids:
            return None, None
        if storage is None:  # pragma: no cover - router always supplies it
            raise ServiceUnavailableError("File storage is not available.")

        service = UploadService(self.db, storage)
        items: list[tuple[bytes, str | None, str]] = []
        records: list[dict] = []
        for upload_id in upload_ids[:MAX_ATTACHMENTS]:
            data, content_type, filename, storage_key = await to_thread.run_sync(
                service.read_with_key, user, upload_id
            )
            items.append((data, content_type, filename))
            records.append(
                {
                    "upload_id": str(upload_id),
                    "filename": filename,
                    "content_type": content_type,
                    "size_bytes": len(data),
                    "storage_key": storage_key,
                }
            )
        return build_bundle(items), {"attachments": records}

    async def begin_stream(
        self,
        client_id: uuid.UUID,
        chat_id: uuid.UUID,
        user: User,
        content: str,
        *,
        attachment_upload_ids: list[uuid.UUID] | None = None,
    ) -> StreamContext:
        """Validate + persist the user turn and pre-compute the answer prompt while
        the request session is open. Raises ``NotFoundError`` (404) before any
        streaming starts. Call this, then feed the result to ``stream_events``.

        Also runs the same content-plan intent check as ``ask()``: a request
        that turns out to want a content plan short-circuits to
        ``immediate_reply``/``immediate_action`` on the returned context
        instead of preparing a conversational stream — see ``stream_events``.
        """
        if attachment_upload_ids:
            # `LLMClient.stream` is text-only, so an attachment here would be
            # accepted and then silently dropped. Fail loudly and point at the route
            # that does support files.
            raise BadRequestError(
                "Attachments are not supported on the streaming endpoint — "
                "POST to /messages instead."
            )
        self._require_chat(client_id, chat_id)
        history = [
            (m.role.value, m.content)
            for m in self.chats.list_messages(chat_id, limit=_MAX_LLM_HISTORY_MESSAGES)
        ]
        self.chats.add_message(chat_id, AiRole.user, content)
        self.db.commit()

        plan_reply = await self._maybe_handle_plan_request(client_id, content, history, actor=user)
        if plan_reply is not None:
            reply_text, action_payload = plan_reply
            return StreamContext(
                client_id=client_id,
                chat_id=chat_id,
                immediate_reply=reply_text,
                immediate_action=action_payload or None,
            )

        agent = ProjectAssistantAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=get_llm_client(
                AiUsageContext(feature=AiFeature.PROJECT_AI, client_id=client_id, user_id=user.id)
            ),
        )
        prep = agent.prepare_stream(content, history=history)
        return StreamContext(client_id=client_id, chat_id=chat_id, agent=agent, prep=prep)

    async def stream_events(self, ctx: StreamContext) -> AsyncIterator[str]:
        """Server-Sent Events for one streamed answer: a ``sources`` frame, then a
        ``delta`` frame per token chunk, then a ``done`` frame with the persisted
        message id + full text. Degrades to the deterministic fallback when the AI
        provider is unconfigured or the stream fails.

        A content-plan turn (see ``begin_stream``) has nothing to animate — a
        short clarifying question, or a result that already fully exists — so
        it's delivered as one immediate ``delta`` + ``done`` pair instead.
        """
        if ctx.immediate_reply is not None:
            yield _sse({"type": "sources", "sources": []})
            yield _sse({"type": "delta", "text": ctx.immediate_reply})
            message_id = self._finalize(
                ctx.client_id,
                ctx.chat_id,
                ctx.immediate_reply,
                meta={"action": ctx.immediate_action} if ctx.immediate_action else None,
            )
            yield _sse(
                {
                    "type": "done",
                    "message_id": str(message_id),
                    "content": ctx.immediate_reply,
                    "action": ctx.immediate_action,
                }
            )
            return

        assert ctx.agent is not None and ctx.prep is not None
        prep = ctx.prep
        yield _sse({"type": "sources", "sources": prep.snippets})

        parts: list[str] = []
        if prep.system is None:  # AI unconfigured → stream the deterministic fallback
            parts.append(prep.fallback)
            yield _sse({"type": "delta", "text": prep.fallback})
        else:
            try:
                async for delta in ctx.agent.ai.stream(
                    system=prep.system, prompt=prep.prompt, model=prep.model
                ):
                    if delta:
                        parts.append(delta)
                        yield _sse({"type": "delta", "text": delta})
            except Exception:  # transient provider error — degrade, never 500 mid-stream
                logger.warning(
                    "Project assistant stream failed for client %s", ctx.client_id, exc_info=True
                )
                if not parts:
                    parts.append(prep.error_fallback)
                    yield _sse({"type": "delta", "text": prep.error_fallback})

        answer = "".join(parts).strip() or prep.error_fallback
        message_id = self._finalize(ctx.client_id, ctx.chat_id, answer)
        yield _sse(
            {"type": "done", "message_id": str(message_id), "content": answer, "action": None}
        )

    def _finalize(
        self, client_id: uuid.UUID, chat_id: uuid.UUID, content: str, *, meta: dict | None = None
    ) -> uuid.UUID:
        """Persist the assembled assistant turn on the request session (same session
        the non-streaming ``ask`` uses)."""
        message = self.chats.add_message(chat_id, AiRole.assistant, content, meta=meta)
        chat = self.chats.get_for_client(client_id, chat_id)
        if chat is not None:
            chat.updated_at = datetime.now(UTC)  # bump so recent chats sort first
        self.db.commit()
        self.db.refresh(message)
        return message.id

    def _require_chat(self, client_id: uuid.UUID, chat_id: uuid.UUID) -> AiChat:
        chat = self.chats.get_for_client(client_id, chat_id)
        if chat is None:
            raise NotFoundError("Chat not found.")
        return chat

    # ---- chat-drafted plan approve/reject -------------------------------- #

    def approve_plan_draft(
        self, client_id: uuid.UUID, chat_id: uuid.UUID, message_id: uuid.UUID, *, actor: User
    ) -> AssistantMessageRead:
        """The chat card's "Approve" button — reuses the exact same batch
        approval as a manually-drafted plan (see
        ``PlanGenerationService.approve_batch``), then marks this specific
        message's card as resolved so reopening the chat later shows the
        outcome instead of a card that looks pending forever."""
        message, task_ids = self._require_pending_action_message(client_id, chat_id, message_id)
        PlanGenerationService(self.db).approve_batch(client_id, task_ids, actor=actor)
        self._set_action_status(message, "approved")
        return self._message_read(message)

    def reject_plan_draft(
        self,
        client_id: uuid.UUID,
        chat_id: uuid.UUID,
        message_id: uuid.UUID,
        reason: str,
        *,
        actor: User,
    ) -> AssistantMessageRead:
        """The chat card's "Discard" button — mirrors
        ``PlanGenerationService.reject_batch`` exactly (never hard-deletes;
        a manager can still revise it later from the normal Plan page)."""
        message, task_ids = self._require_pending_action_message(client_id, chat_id, message_id)
        PlanGenerationService(self.db).reject_batch(client_id, task_ids, reason, actor=actor)
        self._set_action_status(message, "rejected")
        return self._message_read(message)

    def _require_pending_action_message(
        self, client_id: uuid.UUID, chat_id: uuid.UUID, message_id: uuid.UUID
    ) -> tuple[AiChatMessage, list[uuid.UUID]]:
        self._require_chat(client_id, chat_id)  # 404 for an inaccessible/missing chat
        message = self.chats.get_message(chat_id, message_id)
        action = (message.meta or {}).get("action") if message is not None else None
        if message is None or not action:
            raise NotFoundError("Plan draft message not found.")
        if action.get("status") != "pending":
            raise BadRequestError("This plan draft has already been resolved.")
        task_ids = [uuid.UUID(str(t)) for t in action.get("task_ids") or []]
        return message, task_ids

    def _set_action_status(self, message: AiChatMessage, status: str) -> None:
        # Reassign the whole dict (rather than mutating message.meta in place)
        # so SQLAlchemy's change-tracking on the plain JSON column actually
        # notices — this column isn't wrapped in MutableDict.
        meta = dict(message.meta or {})
        action = dict(meta.get("action") or {})
        action["status"] = status
        meta["action"] = action
        message.meta = meta
        self.db.commit()
        self.db.refresh(message)
