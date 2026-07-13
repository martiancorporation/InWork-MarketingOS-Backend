"""Data access for the global ``uploads`` registry.

Reads/writes single rows via the generic base (get/add). The service layer owns
access-scoping and the transaction boundary.
"""

from __future__ import annotations

from app.models.upload import Upload
from app.repositories.base import BaseRepository


class UploadRepository(BaseRepository[Upload]):
    model = Upload
