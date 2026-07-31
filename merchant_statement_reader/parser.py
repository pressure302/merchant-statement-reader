from __future__ import annotations

from merchant_statement_reader.models import StatementAnalysis
from merchant_statement_reader.processors.card_processing_statement import CardProcessingStatementParser
from merchant_statement_reader.processors.fiserv import FiservParser
from merchant_statement_reader.processors.generic import GenericParser
from merchant_statement_reader.processors.maverick import MaverickParser
from merchant_statement_reader.processors.paysafe import PaysafeParser


PARSERS = [MaverickParser(), PaysafeParser(), CardProcessingStatementParser(), FiservParser(), GenericParser()]


def analyze_statement(text: str) -> StatementAnalysis:
    for parser in PARSERS:
        if parser.matches(text):
            return parser.parse(text)
    return GenericParser().parse(text)
