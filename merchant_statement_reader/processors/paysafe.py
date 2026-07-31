from __future__ import annotations

from merchant_statement_reader.models import StatementAnalysis
from merchant_statement_reader.processors.card_processing_statement import CardProcessingStatementParser


class PaysafeParser(CardProcessingStatementParser):
    processor_name = "Paysafe"

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return (
            "your card processing statement" in lowered
            and "merchant services" in lowered
            and "additional fees" in lowered
        )

    def parse(self, text: str) -> StatementAnalysis:
        analysis = super().parse(text)
        analysis.processor_name = self.processor_name
        return analysis
