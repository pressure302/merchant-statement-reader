from __future__ import annotations

import re
from decimal import Decimal

from merchant_statement_reader.models import FeeCategory, FeeLine, StatementAnalysis
from merchant_statement_reader.processors.base import StatementParser
from merchant_statement_reader.processors.generic import detect_item_count, detect_rate_text, normalize_fee_name, parse_money

CARD_SECTION_RE = re.compile(r"^(MASTERCARD|MASTER\s*CARD|VISA|VS OFLN DB|AMEXCT\d+|AMEX ACQ|AMERICAN EXPRESS|DISCOVER|DCVR ACQ|DEBIT CARD|STAR|INTERLINK|PULSE)$", re.I)
FISERV_FEE_RE = re.compile(
    r"^\d{2}/\d{2}/\d{2}\s+(?P<type>CF|MISC)\s+(?P<description>.+?)\s+(?:(?P<volume>-?\d[\d,]*\.\d{2})\s+)?(?P<rate>-?\d+\.\d{4,5})\s+(?P<amount>-?\$?[\d,]+\.\d{2}|-\$?[\d,]+\.\d{2})$",
    re.I,
)
CARDPOINTE_FEE_RE = re.compile(
    r"^(?P<description>.+?)\s+(?P<type>Interchange charges|Program Fees|Service charges|Fees|Non-Receipt of PCI Validation Fees)\s+(?P<amount>-?\$?[\d,]+\.\d{2})$",
    re.I,
)

TOTAL_SUBMITTED_PATTERNS = [
    re.compile(r"(?:Page\s+\d+\s+)?(?:Total\s+)?Amount(?:s)? Submitted\s+(?P<amount>-?\$?[\d,]+\.\d{2})", re.I),
    re.compile(r"Total\s+\d[\d,]*\s+(?P<amount>\$?[\d,]+\.\d{2})\s+0\s+0\.00\s+\$?[\d,]+\.\d{2}", re.I),
]
TOTAL_FEES_PATTERNS = [
    re.compile(r"(?:Page\s+\d+\s+)?Fees(?: Charged)?\s+(?P<amount>-\$?[\d,]+\.\d{2})", re.I),
    re.compile(r"Total \(.*?\)\s+(?P<amount>-\$?[\d,]+\.\d{2})", re.I),
]
CUSTOMER_PAID_FEE_PATTERNS = [
    re.compile(r"Less\s+Discount\s+Paid\s+(?P<amount>-?\$?[\d,]+\.\d{2})", re.I),
    re.compile(r"Less\s+(?:Cash\s+Discount|Surcharge|Service\s+Fee)\s+Paid\s+(?P<amount>-?\$?[\d,]+\.\d{2})", re.I),
    re.compile(r"(?:Cash\s+Discount|Surcharge|Service\s+Fee)\s+Paid\s+(?P<amount>-?\$?[\d,]+\.\d{2})", re.I),
]
PERIOD_PATTERN = re.compile(r"Statement\s*Period\s+(?P<period>\d{2}/\d{2}/\d{2}\s+-\s+\d{2}/\d{2}/\d{2})", re.I)

CARD_BRAND_TERMS = {
    "acq proc",
    "acquirer",
    "assessment",
    "assessments",
    "base ii",
    "clearing connectivity",
    "commercial solutions",
    "cross border",
    "data usage",
    "digital enablement",
    "digital investment",
    "dues",
    "fixed network",
    "global acquirer",
    "integrity",
    "interchange",
    "kilobyte",
    "license volume",
    "nabu",
    "network",
    "ntwk",
    "online debit",
    "program fees",
}

PROCESSOR_TERMS = {
    "accessone",
    "annual pci",
    "auth fee",
    "avs",
    "batch",
    "cardpointe platform",
    "clover security",
    "cpu gtwy",
    "disc 1",
    "discount",
    "gateway",
    "location fee",
    "monthly",
    "other item",
    "other volume",
    "pci",
    "platform",
    "sales discount",
    "sales items",
    "sales trans",
    "statement fee",
    "trans fee",
    "transarmor",
}

BRAND_MAP = {
    "MASTERCARD": "Mastercard",
    "MASTER CARD": "Mastercard",
    "VISA": "Visa",
    "VS OFLN DB": "Visa Debit",
    "AMEX": "American Express",
    "AMERICAN EXPRESS": "American Express",
    "DISCOVER": "Discover",
    "DCVR ACQ": "Discover",
    "DEBIT CARD": "Debit",
    "STAR": "STAR",
    "INTERLINK": "Interlink",
    "PULSE": "Pulse",
}


class CardProcessingStatementParser(StatementParser):
    processor_name = "Card processing statement"

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return "your card processing statement" in lowered and (
            "fees charged" in lowered or "transaction fees type amount" in lowered
        )

    def parse(self, text: str) -> StatementAnalysis:
        fee_lines: list[FeeLine] = []
        current_brand: str | None = None
        in_fees = False

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.upper().startswith(("FEES CHARGED", "TRANSACTION FEES", "DEBIT NETWORK FEES", "ACCOUNT FEES")):
                in_fees = True
                continue
            if line.upper().startswith(("TOTAL CARD FEES", "TOTAL MISCELLANEOUS FEES", "TOTAL TRANSACTION FEES", "TOTAL DEBIT NETWORK FEES", "TOTAL ACCOUNT FEES")):
                continue
            if line.startswith("Total Interchange Charges/Program Fees"):
                break

            section = detect_section_brand(line)
            if section:
                current_brand = section
                continue
            if not in_fees:
                continue

            fiserv_line = self._parse_fiserv_fee(line, current_brand)
            if fiserv_line:
                fee_lines.append(fiserv_line)
                continue

            cardpointe_line = self._parse_cardpointe_fee(line, current_brand)
            if cardpointe_line:
                fee_lines.append(cardpointe_line)

        return StatementAnalysis(
            processor_name=self.processor_name,
            merchant_name=detect_merchant_name(text),
            statement_period=find_text(text, PERIOD_PATTERN, "period"),
            total_processing=find_amount(text, TOTAL_SUBMITTED_PATTERNS),
            total_fees=find_amount(text, TOTAL_FEES_PATTERNS),
            fee_lines=fee_lines,
            customer_paid_fees=find_amount(text, CUSTOMER_PAID_FEE_PATTERNS),
            notes=[],
        )

    def _parse_fiserv_fee(self, line: str, brand: str | None) -> FeeLine | None:
        match = FISERV_FEE_RE.match(line)
        if not match:
            return None
        description = match.group("description").strip()
        source_type = match.group("type").upper()
        amount = abs(parse_money(match.group("amount")))
        if amount == 0:
            return None
        category = FeeCategory.PROCESSOR if source_type == "MISC" else categorize_by_description(description)
        rate = match.group("rate")
        volume = Decimal(match.group("volume").replace(",", "")) if match.group("volume") else None
        return FeeLine(
            raw_name=description,
            normalized_name=normalize_statement_fee_name(description),
            amount=amount,
            category=category,
            brand=brand,
            rate_text=rate,
            volume=volume,
            confidence=0.95,
        )

    def _parse_cardpointe_fee(self, line: str, brand: str | None) -> FeeLine | None:
        match = CARDPOINTE_FEE_RE.match(line)
        if not match:
            return None
        description = match.group("description").strip()
        source_type = match.group("type").lower()
        if source_type == "non-receipt of pci validation fees":
            description = f"{description} {match.group('type')}"
        amount = abs(parse_money(match.group("amount")))
        if amount == 0:
            return None

        if source_type in {"interchange charges", "program fees"}:
            category = FeeCategory.CARD_BRAND
        elif source_type == "service charges":
            category = FeeCategory.PROCESSOR
        else:
            category = categorize_by_description(description)

        return FeeLine(
            raw_name=description,
            normalized_name=normalize_statement_fee_name(description),
            amount=amount,
            category=category,
            brand=brand,
            rate_text=detect_rate_text(line) or detect_embedded_rate(line),
            item_count=detect_item_count(line),
            confidence=0.9,
        )


def categorize_by_description(description: str) -> FeeCategory:
    lowered = description.lower()
    if any(term in lowered for term in PROCESSOR_TERMS):
        return FeeCategory.PROCESSOR
    if any(term in lowered for term in CARD_BRAND_TERMS):
        return FeeCategory.CARD_BRAND
    return FeeCategory.PROCESSOR


def normalize_statement_fee_name(description: str) -> str:
    normalized = re.sub(r"\b\d{1,7}\s+(?:TRANSACTIONS|KILOBYTES|TRANS)\s+AT\s+\$?\.?\d+\b", "", description, flags=re.I)
    normalized = re.sub(r"\b\$?[\d,]+\.\d{2}\s+AT\s*\.?\d+\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\b\.?\d+(?:\.\d+)?\s+(?:DISC RATE )?TIMES\s+\$?[\d,]+\.\d{2}\b", "", normalized, flags=re.I)
    normalized = re.sub(r"\b(MC|VI|VISA|MASTERCARD|DISCOVER|DSCVR|AMEX|AXP|DCVR)\b[- ]?", "", normalized, flags=re.I)
    normalized = normalize_fee_name(normalized)
    normalized = normalized.replace("Dscv", "Discover").replace("Ntwk", "Network").replace("Acq Proc", "Acquirer Processor")
    return normalized


def detect_section_brand(line: str) -> str | None:
    if not CARD_SECTION_RE.match(line):
        return None
    upper = re.sub(r"\s+", " ", line.upper())
    for key, value in BRAND_MAP.items():
        if upper.startswith(key):
            return value
    return line.title()


def detect_embedded_rate(line: str) -> str | None:
    discount_match = re.search(r"\b(?P<rate>\.?\d+(?:\.\d+)?)\s+DISC RATE TIMES\s+\$?[\d,]+\.\d{2}", line, re.I)
    if discount_match:
        return discount_match.group("rate")
    match = re.search(r"(?:AT|RATE TIMES|TIMES)\s+\$?(\.?\d+(?:\.\d+)?)", line, re.I)
    if not match:
        return None
    return match.group(1)


def find_amount(text: str, patterns: list[re.Pattern[str]]) -> Decimal:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return abs(parse_money(match.group("amount")))
    return Decimal("0")


def find_text(text: str, pattern: re.Pattern[str], group: str) -> str | None:
    match = pattern.search(text)
    return match.group(group).strip() if match else None


def detect_merchant_name(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines[:20]):
        if "YOUR CARD PROCESSING STATEMENT" in line.upper() and index + 1 < len(lines):
            candidate = re.sub(r"\s+Page\s+\d+.*$", "", lines[index + 1], flags=re.I).strip()
            return candidate or None
    return None
