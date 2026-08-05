from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class FeeCategory(str, Enum):
    CARD_BRAND = "Card brand / network"
    PROCESSOR = "ISO / processor"
    UNKNOWN = "Needs review"


class ComparisonRole(str, Enum):
    PASS_THROUGH = "Pass-through"
    CARD_PROCESSING = "Processor card-processing charge"
    MONTHLY_OPTIONAL = "Monthly / optional processor charge"
    HIDDEN = "Hidden from comparison"


@dataclass(frozen=True)
class PricingSummary:
    program_type: str
    rate: Decimal | None = None
    per_transaction_fee: Decimal | None = None

    @property
    def display_text(self) -> str:
        rate = "--" if self.rate is None else f"{self.rate:.2f}%"
        transaction_fee = "--" if self.per_transaction_fee is None else f"${self.per_transaction_fee:.2f}/swipe"
        return f"{self.program_type}\n{rate} + {transaction_fee}"


@dataclass(frozen=True)
class FeeLine:
    raw_name: str
    normalized_name: str
    amount: Decimal
    category: FeeCategory
    brand: str | None = None
    rate_text: str | None = None
    item_count: int | None = None
    volume: Decimal | None = None
    confidence: float = 0.5


@dataclass
class FeeGroup:
    normalized_name: str
    category: FeeCategory
    amount: Decimal = Decimal("0")
    brands: set[str] = field(default_factory=set)
    raw_names: set[str] = field(default_factory=set)
    rates: set[str] = field(default_factory=set)
    item_count: int = 0
    volume: Decimal = Decimal("0")
    confidence: float = 0

    def add(self, line: FeeLine) -> None:
        self.amount += line.amount
        self.raw_names.add(line.raw_name)
        if line.brand:
            self.brands.add(line.brand)
        if line.rate_text:
            self.rates.add(line.rate_text)
        if line.item_count:
            self.item_count += line.item_count
        if line.volume:
            self.volume += line.volume
        self.confidence = max(self.confidence, line.confidence)

    @property
    def comparison_role(self) -> ComparisonRole:
        text = " ".join([self.normalized_name, *self.raw_names]).lower()
        if is_dues_and_assessments_text(text):
            return ComparisonRole.CARD_PROCESSING
        if self.category == FeeCategory.CARD_BRAND:
            return ComparisonRole.PASS_THROUGH
        if self.category != FeeCategory.PROCESSOR:
            return ComparisonRole.HIDDEN

        if is_pass_through_processor_text(text):
            return ComparisonRole.PASS_THROUGH
        if is_monthly_optional_processor_text(text):
            return ComparisonRole.MONTHLY_OPTIONAL
        if is_card_processing_processor_text(text):
            return ComparisonRole.CARD_PROCESSING
        return ComparisonRole.HIDDEN

    @property
    def is_likely_daily_paid(self) -> bool:
        text = " ".join([self.normalized_name, *self.raw_names]).lower()
        return any(
            term in text
            for term in (
                "disc 1",
                "discount",
                "mqual disc",
                "nqual disc",
                "qual disc",
                "sales disc",
                "sales discount",
            )
        )

    @property
    def source_label(self) -> str:
        text = " ".join([self.normalized_name, *self.raw_names]).lower()
        if self.category == FeeCategory.CARD_BRAND or is_pass_through_processor_text(text) or is_dues_and_assessments_text(text):
            return "Card brand / network"
        if self.category == FeeCategory.PROCESSOR:
            return "Processor / ISO"
        return "Needs review"


@dataclass
class StatementAnalysis:
    processor_name: str
    merchant_name: str | None
    statement_period: str | None
    total_processing: Decimal
    total_fees: Decimal
    fee_lines: list[FeeLine]
    customer_paid_fees: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)

    @property
    def card_brand_total(self) -> Decimal:
        return sum((line.amount for line in self.fee_lines if line.category == FeeCategory.CARD_BRAND), Decimal("0"))

    @property
    def processor_total(self) -> Decimal:
        return sum((line.amount for line in self.fee_lines if line.category == FeeCategory.PROCESSOR), Decimal("0"))

    @property
    def unknown_total(self) -> Decimal:
        return sum((line.amount for line in self.fee_lines if line.category == FeeCategory.UNKNOWN), Decimal("0"))

    @property
    def effective_rate(self) -> Decimal | None:
        if self.total_processing == 0:
            return None
        return (self.merchant_paid_total_fees / self.total_processing) * Decimal("100")

    @property
    def merchant_paid_total_fees(self) -> Decimal:
        adjusted = self.total_fees - self.customer_paid_fees
        return adjusted if adjusted > 0 else Decimal("0")

    @property
    def processor_rate(self) -> Decimal | None:
        if self.total_processing == 0:
            return None
        return (self.processor_total / self.total_processing) * Decimal("100")

    @property
    def pricing_summary(self) -> PricingSummary:
        processor_lines = [
            line
            for line in self.fee_lines
            if line.category == FeeCategory.PROCESSOR and not is_pass_through_processor_text(line.raw_name.lower())
        ]
        names = " ".join(line.raw_name.lower() for line in processor_lines)
        tiered_discount_rates = detect_tiered_discount_rates(processor_lines)

        if len(tiered_discount_rates) > 1:
            program_type = "Tiered"
        elif tiered_discount_rates or any("flat" in line.raw_name.lower() for line in processor_lines):
            program_type = "Flat rate"
        elif self.card_brand_total > 0 and processor_lines:
            program_type = "IC+"
        else:
            program_type = "Unknown"

        return PricingSummary(
            program_type=program_type,
            rate=detect_markup_rate(processor_lines),
            per_transaction_fee=detect_transaction_fee(processor_lines),
        )

    def grouped_fees(self) -> list[FeeGroup]:
        groups: dict[tuple[FeeCategory, str], FeeGroup] = {}
        for line in self.fee_lines:
            key = (line.category, line.normalized_name)
            if key not in groups:
                groups[key] = FeeGroup(normalized_name=line.normalized_name, category=line.category)
            groups[key].add(line)
        return sorted(groups.values(), key=lambda group: (group.category.value, group.normalized_name.lower()))

    def grouped_fees_for(self, category: FeeCategory) -> list[FeeGroup]:
        return [group for group in self.grouped_fees() if group.category == category]

    def comparison_groups_for(self, role: ComparisonRole) -> list[FeeGroup]:
        return sorted(
            [group for group in self.grouped_fees() if group.comparison_role == role],
            key=comparison_sort_key,
        )

    @property
    def pass_through_total(self) -> Decimal:
        return sum((group.amount for group in self.comparison_groups_for(ComparisonRole.PASS_THROUGH)), Decimal("0"))

    @property
    def card_processing_processor_total(self) -> Decimal:
        return sum((group.amount for group in self.comparison_groups_for(ComparisonRole.CARD_PROCESSING)), Decimal("0"))

    @property
    def monthly_optional_processor_total(self) -> Decimal:
        return sum((group.amount for group in self.comparison_groups_for(ComparisonRole.MONTHLY_OPTIONAL)), Decimal("0"))

    @property
    def competitive_processor_total(self) -> Decimal:
        return self.card_processing_processor_total + self.monthly_optional_processor_total

    @property
    def hidden_processor_total(self) -> Decimal:
        return sum(
            (
                group.amount
                for group in self.comparison_groups_for(ComparisonRole.HIDDEN)
                if group.category == FeeCategory.PROCESSOR
            ),
            Decimal("0"),
        )


def detect_markup_rate(lines: list[FeeLine]) -> Decimal | None:
    discount_candidates: list[Decimal] = []
    volume_candidates: list[Decimal] = []
    for line in lines:
        name = line.raw_name.lower()
        if not line.rate_text:
            continue
        is_discount = any(term in name for term in ("disc", "discount"))
        is_volume = "volume" in name
        if not is_discount and not is_volume:
            continue
        rate = parse_rate_value(line.rate_text)
        if rate is None:
            continue
        if rate <= Decimal("0.10"):
            converted = rate * Decimal("100")
        else:
            converted = rate
        if is_discount:
            discount_candidates.append(converted)
        else:
            volume_candidates.append(converted)
    return most_common_decimal(discount_candidates) or most_common_decimal(volume_candidates)


def detect_tiered_discount_rates(lines: list[FeeLine]) -> set[Decimal]:
    rates: set[Decimal] = set()
    for line in lines:
        name = line.raw_name.lower()
        if not line.rate_text:
            continue
        if not any(term in name for term in ("qualified", "mid qual", "non qual", "non-qualified", "nqual", "mqual", "qual disc")):
            continue
        rate = parse_rate_value(line.rate_text)
        if rate is not None:
            rates.add(rate)
    return rates


def detect_transaction_fee(lines: list[FeeLine]) -> Decimal | None:
    transaction_candidates: list[Decimal] = []
    auth_candidates: list[Decimal] = []
    for line in lines:
        name = line.raw_name.lower()
        if not line.rate_text:
            continue
        if any(term in name for term in ("integrity", "reattempt", "cross border")):
            continue
        is_transaction_fee = any(term in name for term in ("trans", "transaction", "item"))
        is_auth_fee = "auth fee" in name or "authorization fee" in name or "cpu gtwy" in name
        if not is_transaction_fee and not is_auth_fee:
            continue
        rate = parse_rate_value(line.rate_text)
        if rate is None or rate <= Decimal("0") or rate > Decimal("2"):
            continue
        if is_auth_fee:
            auth_candidates.append(rate)
        else:
            transaction_candidates.append(rate)

    transaction_fee = most_common_decimal(transaction_candidates) or Decimal("0")
    auth_fee = most_common_decimal(auth_candidates) or Decimal("0")
    combined = transaction_fee + auth_fee
    return combined if combined else None


def parse_rate_value(value: str) -> Decimal | None:
    import re

    match = re.search(r"\d+(?:\.\d+)?|\.?\d+", value)
    if not match:
        return None
    return Decimal(match.group())


def most_common_decimal(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    counts: dict[Decimal, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts, key=lambda value: (-counts[value], value))[0]


PASS_THROUGH_TERMS = {
    "acq isa",
    "acq support",
    "acquirer",
    "acquirer processor",
    "acqr",
    "assessment",
    "assessments",
    "base ii",
    "clearing connectivity",
    "commercial solutions",
    "commercialsolutions",
    "cross border",
    "data usage",
    "digital enablement",
    "digital investment",
    "digital comm",
    "digtl",
    "dues",
    "due/asmt",
    "file transmission",
    "fixed network",
    "global acquirer",
    "integrity",
    "kilobyte",
    "license volume",
    "location fee",
    "mail/telephone",
    "nabu",
    "network",
    "network access",
    "ntwk",
    "online debit",
    "program fee",
    "reattempt",
    "reg product",
    "regulatory",
    "token",
}

CARD_PROCESSING_TERMS = {
    "auth fee",
    "authorization fee",
    "avs",
    "cpu gtwy",
    "disc 1",
    "discount",
    "mqual disc",
    "nqual disc",
    "gateway",
    "other item",
    "other volume",
    "qual disc",
    "pinless auth",
    "pinless transaction",
    "sale trans",
    "sales discount",
    "sales disc",
    "sales items",
    "sales trans",
    "system processing",
    "trans fee",
    "transaction fee",
    "verification service",
}

MONTHLY_OPTIONAL_TERMS = {
    "accessone",
    "additional fees",
    "annual pci",
    "batch",
    "cardpointe platform",
    "clover",
    "fee adj/rev",
    "minimum discount",
    "monthly",
    "non-validation",
    "non-receipt of pci compliance",
    "non-receipt of pci validation",
    "pci",
    "platform",
    "statement fee",
    "transarmor",
}

HIGH_PRIORITY_TERMS = {
    "disc 1": 0,
    "discount": 0,
    "sales discount": 0,
    "dues": 1,
    "due/asmt": 1,
    "other volume": 1,
    "other item": 1,
    "sales trans": 1,
    "auth fee": 2,
    "authorization fee": 2,
    "monthly": 3,
    "statement fee": 3,
    "pci": 3,
    "batch": 3,
    "platform": 3,
}


def is_dues_and_assessments_text(text: str) -> bool:
    return any(
        term in text
        for term in (
            "dues & assessments",
            "dues and assessments",
            "dues and assess",
            "dues & assess",
            "due/asmt",
        )
    )


def is_pass_through_processor_text(text: str) -> bool:
    return any(term in text for term in PASS_THROUGH_TERMS)


def is_card_processing_processor_text(text: str) -> bool:
    if any(term in text for term in ("network authorization", "network access", "digital enablement")):
        return False
    return any(term in text for term in CARD_PROCESSING_TERMS)


def is_monthly_optional_processor_text(text: str) -> bool:
    return any(term in text for term in MONTHLY_OPTIONAL_TERMS)


def comparison_sort_key(group: FeeGroup) -> tuple[int, str]:
    text = " ".join([group.normalized_name, *group.raw_names]).lower()
    priority = min((rank for term, rank in HIGH_PRIORITY_TERMS.items() if term in text), default=9)
    return priority, group.normalized_name.lower()
