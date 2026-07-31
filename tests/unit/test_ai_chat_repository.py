"""``AiChatRepository.list_messages`` with ``limit`` bounds how much history is
replayed into the LLM prompt on every turn (see AssistantService), instead of
sending an ever-growing transcript. ``list_messages_page`` bounds the separate
paginated chat-detail read view."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.client import Client
from app.models.enums import AiRole, UserRole
from app.models.user import User
from app.repositories.ai_chat_repository import AiChatRepository


def _setup(db: Session) -> tuple[Client, User]:
    client = Client(slug="chat-co", name="Chat Co")
    user = User(
        email="chat-owner@test.com",
        name="Chat Owner",
        password_hash=hash_password("irrelevant1"),
        role=UserRole.user,
    )
    db.add_all([client, user])
    db.commit()
    db.refresh(client)
    db.refresh(user)
    return client, user


def test_list_messages_without_limit_returns_everything_chronologically(
    db_session: Session,
) -> None:
    client, user = _setup(db_session)
    repo = AiChatRepository(db_session)
    chat = repo.create_chat(client.id, user.id)
    db_session.commit()
    for i in range(5):
        repo.add_message(chat.id, AiRole.user, f"message {i}")
        db_session.commit()

    messages = repo.list_messages(chat.id)

    assert [m.content for m in messages] == [f"message {i}" for i in range(5)]


def test_list_messages_with_limit_keeps_only_the_most_recent(db_session: Session) -> None:
    client, user = _setup(db_session)
    repo = AiChatRepository(db_session)
    chat = repo.create_chat(client.id, user.id)
    db_session.commit()
    for i in range(10):
        repo.add_message(chat.id, AiRole.user, f"message {i}")
        db_session.commit()

    capped = repo.list_messages(chat.id, limit=4)

    # The 4 most recent, still in chronological (oldest-first) order — not
    # the first 4 ever sent, and not reversed.
    assert [m.content for m in capped] == ["message 6", "message 7", "message 8", "message 9"]


def test_list_messages_page_paginates_the_full_history(db_session: Session) -> None:
    client, user = _setup(db_session)
    repo = AiChatRepository(db_session)
    chat = repo.create_chat(client.id, user.id)
    db_session.commit()
    for i in range(7):
        repo.add_message(chat.id, AiRole.user, f"message {i}")
        db_session.commit()

    page1, total = repo.list_messages_page(chat.id, offset=0, limit=3)
    page2, _ = repo.list_messages_page(chat.id, offset=3, limit=3)

    assert total == 7
    assert [m.content for m in page1] == ["message 0", "message 1", "message 2"]
    assert [m.content for m in page2] == ["message 3", "message 4", "message 5"]


def test_list_messages_page_ids_are_real(db_session: Session) -> None:
    client, user = _setup(db_session)
    repo = AiChatRepository(db_session)
    chat = repo.create_chat(client.id, user.id)
    db_session.commit()
    msg = repo.add_message(chat.id, AiRole.user, "hello")
    db_session.commit()

    page, total = repo.list_messages_page(chat.id, offset=0, limit=20)
    assert total == 1
    assert page[0].id == msg.id
    assert isinstance(page[0].id, uuid.UUID)
