from __future__ import annotations

import re

from merchant_statement_reader.models import StatementAnalysis
from merchant_statement_reader.processors.generic import GenericParser


class FiservParser(GenericParser):
    processor_name = "Fiserv"

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ("fiserv", "first data", "merchant processing statement"))

    def parse(self, text: str) -> StatementAnalysis:
        analysis = super().parse(text)
        analysis.processor_name = self.processor_name
        analysis.notes.insert(
            0,
            "Fiserv parser selected. Like card-brand fee names are normalized across Visa, Mastercard, Discover, and Amex.",
        )
        return analysis
