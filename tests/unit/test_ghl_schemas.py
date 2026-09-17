"""Unit tests: GhlContactRead field normalization.

GHL's own examples have been inconsistent — the illustrative `/contacts/search`
sample showed a single `contactName`, but the account team's later
field-level breakdown lists separate `firstName`/`lastName` instead. Both
must be accepted without knowing in advance which shape a given response
actually uses."""

from __future__ import annotations

from app.schemas.integration import GhlContactRead


def test_uses_contact_name_when_provided():
    contact = GhlContactRead.model_validate({"id": "c1", "contactName": "Jane Sample"})
    assert contact.contact_name == "Jane Sample"


def test_falls_back_to_first_and_last_name_when_contact_name_absent():
    contact = GhlContactRead.model_validate({"id": "c1", "firstName": "Jane", "lastName": "Sample"})
    assert contact.contact_name == "Jane Sample"
    assert contact.first_name == "Jane"
    assert contact.last_name == "Sample"


def test_prefers_contact_name_over_first_last_when_both_present():
    contact = GhlContactRead.model_validate(
        {"id": "c1", "contactName": "Preferred Name", "firstName": "Jane", "lastName": "Sample"}
    )
    assert contact.contact_name == "Preferred Name"


def test_handles_only_first_name():
    contact = GhlContactRead.model_validate({"id": "c1", "firstName": "Jane"})
    assert contact.contact_name == "Jane"


def test_contact_name_is_none_when_nothing_is_provided():
    contact = GhlContactRead.model_validate({"id": "c1"})
    assert contact.contact_name is None


def test_assigned_to_is_normalized():
    contact = GhlContactRead.model_validate({"id": "c1", "assignedTo": "user-123"})
    assert contact.assigned_to == "user-123"
