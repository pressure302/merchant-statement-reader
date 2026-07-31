from __future__ import annotations

from abc import ABC, abstractmethod

from merchant_statement_reader.models import StatementAnalysis


class StatementParser(ABC):
    processor_name = "Unknown"

    @abstractmethod
    def matches(self, text: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, text: str) -> StatementAnalysis:
        raise NotImplementedError
