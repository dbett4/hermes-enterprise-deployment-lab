from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RunbookDocument:
    document_id: str
    text: str
    actions: tuple[dict[str, Any], ...] = ()


class KeywordRetriever:
    """Small local retriever behind the interface Stage 2 will replace with pgvector."""

    def __init__(self, documents: Iterable[RunbookDocument]) -> None:
        self._documents = tuple(documents)

    def search(self, query: str, *, limit: int = 3) -> list[RunbookDocument]:
        query_terms = _terms(query)
        ranked = sorted(
            (
                (len(query_terms & _terms(document.text)), document.document_id, document)
                for document in self._documents
            ),
            key=lambda item: (-item[0], item[1]),
        )
        return [document for score, _, document in ranked if score > 0][:limit]


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.lower()))
