"""Repository layer — abstracts the database so the query API is backend-agnostic."""

from app.repository.base import DuplicateError, FileRepository
from app.repository.sqlite_repo import SQLiteRepository

__all__ = ["FileRepository", "SQLiteRepository", "DuplicateError"]
