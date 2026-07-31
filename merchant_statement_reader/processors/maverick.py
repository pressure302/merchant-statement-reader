from __future__ import annotations

import re
from decimal import Decimal

from merchant_statement_reader.models import FeeCategory, FeeLine, StatementAnalysis
from merchant_statement_reader.processors.base import StatementParser
from merchant_statement_reader.processors.generic import parse_money


AMOUNT_DEDUCTED_RE = re.compile(r"Amount Deducted:?\s*(?:\n|\s)*\$?\s*(?P<amount>[\d,]+\.\d{2})", re.I)
AMOUNT_DEDUCTED_SUMMARY_RE = re.compile(r"^Amount Deducted\s+(?P<amount>[\d,]+\.\d{2})$", re.I | re.M)
PROCESSING_MONTH_RE = re.compile(r"Processing Month:\s*(?P<period>\d{2}-\d{2})", re.I)
MERCHANT_NUMBER_RE = re.compile(r"Merchant Number:\s*(?P<number>\S+)", re.I)
PLAN_LINE_RE = re.compile(
    r"^(?P<code>[A-Z]{2})\s+\d+\s+(?P<sales>\.?\d[\d,]*\.\d{2}|\.\d{2})\s+\d+\s+"
    r"(?P<credits>\.?\d[\d,]*\.\d{2}|\.\d{2})\s+(?P<net>\.?\d[\d,]*\.\d{2}|\.\d{2})\s+"
    r"(?P<avg>\.?\d[\d,]*\.\d{2}|\.\d{2})\s+(?P<per_item>\d+\.\d{5})\s+"
    r"(?P<rate>\d+\.\d{4})\s+(?P<discount>\.?\d[\d,]*\.\d{2}|\.\d{2})$"
)
OTHER_FEE_RE = re.compile(r"^(?P<rate>\d+\.\d{5})\s+(?P<description>.+?)\s+\.00\s+(?P<amount>\d+\.\d{2})$")
MINIMUM_DISCOUNT_RE = re.compile(r"Minimum Discount Due\s+(?P<amount>[\d,]+\.\d{2})", re.I)
TOTAL_OTHER_FEES_RE = re.compile(r"Total Other Fees:\s+(?P<amount>[\d,]+\.\d{2})", re.I)


class MaverickParser(StatementParser):
    processor_name = "Maverick"

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return "merchant number:" in lowered and "minimum discount fee" in lowered and "plan summary" in lowered

    def parse(self, text: str) -> StatementAnalysis:
        plan_rate, per_item, total_processing = parse_plan_summary(text)
        fee_lines = parse_other_fees(text)

        minimum_discount = find_amount(text, MINIMUM_DISCOUNT_RE)
        if minimum_discount:
            fee_lines.append(
                FeeLine(
                    raw_name="Minimum Discount Due",
                    normalized_name="Minimum Discount Due",
                    amount=minimum_discount,
                    category=FeeCategory.PROCESSOR,
                    confidence=0.95,
                )
            )

        # Zero-amount lines carry the pricing program into the shared pricing detector.
        if plan_rate is not None:
            fee_lines.append(
                FeeLine(
                    raw_name="Flat Rate Discount",
                    normalized_name="Flat Rate Discount",
                    amount=Decimal("0"),
                    category=FeeCategory.PROCESSOR,
                    rate_text=str(plan_rate),
                    confidence=0.95,
                )
            )
        if per_item is not None:
            fee_lines.append(
                FeeLine(
                    raw_name="Flat Rate Transaction Fee",
                    normalized_name="Flat Rate Transaction Fee",
                    amount=Decimal("0"),
                    category=FeeCategory.PROCESSOR,
                    rate_text=str(per_item),
                    confidence=0.95,
                )
            )

        return StatementAnalysis(
            processor_name=self.processor_name,
            merchant_name=detect_merchant_name(text),
            statement_period=find_text(text, PROCESSING_MONTH_RE, "period"),
            total_processing=total_processing,
            total_fees=find_amount(text, AMOUNT_DEDUCTED_RE) or find_amount(text, AMOUNT_DEDUCTED_SUMMARY_RE),
            fee_lines=fee_lines,
            notes=[],
        )


def parse_plan_summary(text: str) -> tuple[Decimal | None, Decimal | None, Decimal]:
    rates: set[Decimal] = set()
    per_items: set[Decimal] = set()
    total_processing = Decimal("0")

    for line in text.splitlines():
        match = PLAN_LINE_RE.match(line.strip())
        if not match:
            continue
        rates.add(Decimal(match.group("rate")))
        per_items.add(Decimal(match.group("per_item")))
        total_processing += parse_statement_decimal(match.group("net"))

    rate = sorted(rates)[0] if rates else None
    per_item = sorted(per_items)[0] if per_items else None
    return rate, per_item, total_processing


def parse_other_fees(text: str) -> list[FeeLine]:
    lines: list[FeeLine] = []
    in_other_fees = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "OTHER FEES:":
            in_other_fees = True
            continue
        if line.startswith("Total Other Fees:"):
            break
        if not in_other_fees:
            continue

        match = OTHER_FEE_RE.match(line)
        if not match:
            continue
        description = match.group("description").strip()
        lines.append(
            FeeLine(
                raw_name=description,
                normalized_name=description.title(),
                amount=Decimal(match.group("amount")),
                category=FeeCategory.PROCESSOR,
                rate_text=match.group("rate"),
                confidence=0.95,
            )
        )
    return lines


def find_amount(text: str, pattern: re.Pattern[str]) -> Decimal:
    match = pattern.search(text)
    if not match:
        return Decimal("0")
    return abs(parse_money(match.group("amount")))


def find_text(text: str, pattern: re.Pattern[str], group: str) -> str | None:
    match = pattern.search(text)
    return match.group(group).strip() if match else None


def parse_statement_decimal(value: str) -> Decimal:
    normalized = value.replace(",", "")
    if normalized.startswith("."):
        normalized = "0" + normalized
    return Decimal(normalized)


def detect_merchant_name(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.startswith("Amount Deducted:"):
            for candidate in lines[index + 1 : index + 5]:
                if not candidate.startswith("$"):
                    return candidate
    merchant_number = MERCHANT_NUMBER_RE.search(text)
    return merchant_number.group("number") if merchant_number else None
