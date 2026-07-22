from __future__ import annotations

import re
import sqlite3
import unicodedata
import builtins
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Iterable


_WORD = re.compile(r"\w+", re.UNICODE)
_TYPE_WEIGHTS = {"failure": 3.0, "decision": 2.0, "convention": 1.5}
_FORBIDDEN_TYPES = {"transcript", "validator_output", "validator-output"}


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    project_id: str
    type: str
    content: str
    keywords: tuple[str, ...]
    created_at: datetime


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return frozenset(_WORD.findall(normalized))


class MemoryStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        configured_secret_values: Iterable[str] = (),
    ) -> None:
        self._connection = connection
        self._secrets = tuple(secret for secret in configured_secret_values if secret)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                content TEXT NOT NULL,
                keywords TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def add(
        self,
        project_id: str,
        record_type: str,
        content: str,
        keywords: Iterable[str] = (),
    ) -> MemoryRecord:
        keyword_tuple = tuple(dict.fromkeys(keywords))
        combined = "\n".join((project_id, record_type, content, *keyword_tuple))
        if any(secret in combined for secret in self._secrets):
            raise ValueError("sensitive values cannot be stored in project memory")
        if record_type.casefold() in _FORBIDDEN_TYPES:
            raise ValueError("raw transcript or validator output is not allowed")
        created_at = datetime.now(UTC)
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO memory_records (
                    project_id, record_type, content, keywords, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    record_type,
                    content,
                    "\u001f".join(keyword_tuple),
                    created_at.isoformat(),
                ),
            )
        row_id = cursor.lastrowid
        if row_id is None:
            raise RuntimeError("memory insert did not return an id")
        return MemoryRecord(
            id=row_id,
            project_id=project_id,
            type=record_type,
            content=content,
            keywords=keyword_tuple,
            created_at=created_at,
        )

    def list(self, project_id: str) -> builtins.list[MemoryRecord]:
        rows = self._connection.execute(
            """
            SELECT id, project_id, record_type, content, keywords, created_at
            FROM memory_records WHERE project_id = ? ORDER BY id
            """,
            (project_id,),
        ).fetchall()
        return [self._record(row) for row in rows]

    def delete_project(self, project_id: str) -> int:
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM memory_records WHERE project_id = ?", (project_id,)
            )
        return cursor.rowcount

    def search(
        self,
        project_id: str,
        query: str,
        *,
        limit: int,
        char_budget: int,
    ) -> builtins.list[MemoryRecord]:
        if limit < 1 or char_budget < 1:
            return []
        query_tokens = _tokens(query)
        now = datetime.now(UTC)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in self.list(project_id):
            record_tokens = _tokens(record.content) | _tokens(" ".join(record.keywords))
            overlap = len(query_tokens & record_tokens)
            if overlap == 0:
                continue
            age_days = max(0.0, (now - record.created_at).total_seconds() / 86_400)
            recency = 1.0 / (1.0 + min(age_days, 365.0))
            weight = _TYPE_WEIGHTS.get(record.type.casefold(), 1.0)
            scored.append((overlap * weight + recency, record))
        scored.sort(key=lambda item: (-item[0], item[1].id))

        results: builtins.list[MemoryRecord] = []
        remaining = char_budget
        for _, record in scored:
            if len(results) >= limit:
                break
            if len(record.content) > remaining:
                continue
            results.append(replace(record))
            remaining -= len(record.content)
        return results

    @staticmethod
    def _record(row: tuple[object, ...]) -> MemoryRecord:
        return MemoryRecord(
            id=int(str(row[0])),
            project_id=str(row[1]),
            type=str(row[2]),
            content=str(row[3]),
            keywords=tuple(filter(None, str(row[4]).split("\u001f"))),
            created_at=datetime.fromisoformat(str(row[5])).astimezone(UTC),
        )

