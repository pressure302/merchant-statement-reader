from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from merchant_statement_reader.models import FeeCategory, FeeLine, StatementAnalysis
from merchant_statement_reader.processors.base import StatementParser

MONEY_RE = re.compile(r"\(?-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})\)?|\(?-?\$?\s*\d+\.\d{2}\)?")

CARD_BRAND_SIGNALS = {
    "amex",
    "american express",
    "apf",
    "assessment",
    "assessments",
    "brand",
    "cross border",
    "discover",
    "dues",
    "fanF".lower(),
    "interchange",
    "kilobyte",
    "mastercard",
    "mc ",
    "nabU".lower(),
    "network",
    "visa",
}

PROCESSOR_SIGNALS = {
    "annual",
    "batch",
    "chargeback",
    "discount",
    "gateway",
    "iso",
    "minimum",
    "monthly",
    "pci",
    "platform",
    "processing fee",
    "processor",
    "retrieval",
    "service fee",
    "statement",
    "trans fee",
    "transaction fee",
}

BRAND_ALIASES = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "mc ": "Mastercard",
    "discover": "Discover",
    "american express": "American Express",
    "amex": "American Express",
}

SUMMARY_PATTERNS = [
    re.compile(r"(?:total\s+)?(?:sales|processing|processed|volume|bankcard)\s+(?:amount|volume|sales)?\s*[:\-]?\s*(?P<amount>\(?\$?[\d,]+\.\d{2}\)?)", re.I),
    re.compile(r"(?:amount|volume)\s+(?:processed|submitted)\s*[:\-]?\s*(?P<amount>\(?\$?[\d,]+\.\d{2}\)?)", re.I),
]

FEE_TOTAL_PATTERNS = [
    re.compile(r"(?:total\s+)?(?:fees|charges|discount\s+due|amount\s+due)\s*[:\-]?\s*(?P<amount>\(?\$?[\d,]+\.\d{2}\)?)", re.I),
]

PERIOD_PATTERN = re.compile(r"(?:statement\s+period|period|month)\s*[:\-]?\s*(?P<period>[A-Za-z0-9,/ .\-]+)", re.I)
MERCHANT_PATTERN = re.compile(r"(?:merchant|dba)\s*(?:name)?\s*[:\-]?\s*(?P<merchant>[A-Za-z0-9&',. \-]+)", re.I)


class GenericParser(StatementParser):
    processor_name = "Generic / unknown processor"

    def matches(self, text: str) -> bool:
        return True

    def parse(self, text: str) -> StatementAnalysis:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        fee_lines = [line for line in (self._parse_fee_line(line) for line in lines) if line]
        total_processing = self._find_first_amount(text, SUMMARY_PATTERNS)
        total_fees = self._find_first_amount(text, FEE_TOTAL_PATTERNS)

        if total_processing == Decimal("0"):
            total_processing = sum((line.volume or Decimal("0") for line in fee_lines), Decimal("0"))
        if total_fees == Decimal("0"):
            total_fees = sum((line.amount for line in fee_lines), Decimal("0"))

        notes = [
            "Generic parser used. Review unknown fees and totals before relying on the result.",
        ]
        if any(line.category == FeeCategory.UNKNOWN for line in fee_lines):
            notes.append("Some fees could not be confidently categorized.")

        return StatementAnalysis(
            processor_name=self.processor_name,
            merchant_name=self._find_text(text, MERCHANT_PATTERN, "merchant"),
            statement_period=self._find_text(text, PERIOD_PATTERN, "period"),
            total_processing=total_processing,
            total_fees=total_fees,
            fee_lines=fee_lines,
            notes=notes,
        )

    def _parse_fee_line(self, line: str) -> FeeLine | None:
        money_matches = list(MONEY_RE.finditer(line))
        if not money_matches:
            return None

        amount_match = money_matches[-1]
        amount = parse_money(amount_match.group())
        if amount == 0:
            return None

        name = line[: amount_match.start()].strip(" .:-\t")
        if len(name) < 3:
            return None
        if re.search(r"^(?:total\s+)?(?:fees|charges|amount due|processing volume|sales volume)\b", name, re.I):
            return None

        lower = f" {name.lower()} "
        if not self._looks_like_fee(lower):
            return None

        category, confidence = categorize_fee(lower)
        return FeeLine(
            raw_name=name,
            normalized_name=normalize_fee_name(name),
            amount=abs(amount),
            category=category,
            brand=detect_brand(lower),
            rate_text=detect_rate_text(line),
            item_count=detect_item_count(line),
            volume=detect_volume(line, amount_match.start()),
            confidence=confidence,
        )

    def _looks_like_fee(self, lower_name: str) -> bool:
        fee_words = ("fee", "fees", "dues", "assessment", "interchange", "discount", "rate", "charge", "auth")
        return any(word in lower_name for word in fee_words)

    def _find_first_amount(self, text: str, patterns: list[re.Pattern[str]]) -> Decimal:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return abs(parse_money(match.group("amount")))
        return Decimal("0")

    def _find_text(self, text: str, pattern: re.Pattern[str], group: str) -> str | None:
        match = pattern.search(text)
        if not match:
            return None
        value = match.group(group).strip()
        return value[:80] if value else None


def parse_money(value: str) -> Decimal:
    cleaned = value.strip().replace("$", "").replace(",", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")
    return -amount if negative else amount


def categorize_fee(lower_name: str) -> tuple[FeeCategory, float]:
    if any(signal in lower_name for signal in CARD_BRAND_SIGNALS):
        return FeeCategory.CARD_BRAND, 0.8
    if any(signal in lower_name for signal in PROCESSOR_SIGNALS):
        return FeeCategory.PROCESSOR, 0.75
    return FeeCategory.UNKNOWN, 0.35


def detect_brand(lower_name: str) -> str | None:
    for signal, brand in BRAND_ALIASES.items():
        if signal in lower_name:
            return brand
    return None


def normalize_fee_name(name: str) -> str:
    normalized = name
    normalized = re.sub(r"\d+(?:\.\d+)?\s*(?:%|bps|bp|basis points)", "", normalized, flags=re.I)
    normalized = re.sub(r"\b(Visa|Master\s*Card|Mastercard|MC|Discover|Amex|American Express)\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\b(Credit|Debit|DB|CR)\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s+[-:/]\s+", " ", normalized)
    normalized = normalized.strip(" -:/.\t")
    return normalized.title() if normalized else name.title()


def detect_rate_text(line: str) -> str | None:
    matches = re.findall(r"\d+(?:\.\d+)?\s*(?:%|bps|bp|basis points)", line, flags=re.I)
    return ", ".join(matches) if matches else None


def detect_item_count(line: str) -> int | None:
    match = re.search(r"\b(\d{1,7})\s+(?:items?|auths?|transactions?|txns?)\b", line, flags=re.I)
    return int(match.group(1)) if match else None


def detect_volume(line: str, amount_start: int) -> Decimal | None:
    prefix = line[:amount_start]
    money = list(MONEY_RE.finditer(prefix))
    if not money:
        return None
    volume = abs(parse_money(money[-1].group()))
    return volume if volume > Decimal("10") else None
