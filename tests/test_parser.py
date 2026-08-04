from decimal import Decimal

from merchant_statement_reader.models import ComparisonRole, FeeCategory
from merchant_statement_reader.parser import analyze_statement


SAMPLE = """
Fiserv Merchant Processing Statement
Merchant: Sample Coffee LLC
Statement Period: June 2026
Total Processing Volume: $100,000.00
Total Fees: $2,900.00
Visa Other Item Rate 0.10% $50.00
Mastercard Other Item Rate 0.12% $60.00
Discover Network Assessment $30.00
Discount Fee 0.50% $500.00
Monthly Statement Fee $15.00
PCI Compliance Fee $25.00
"""


def test_fiserv_combines_like_brand_fees() -> None:
    analysis = analyze_statement(SAMPLE)
    groups = analysis.grouped_fees()
    other_item = next(group for group in groups if group.normalized_name == "Other Item Rate")

    assert analysis.processor_name == "Fiserv"
    assert analysis.total_processing == Decimal("100000.00")
    assert analysis.effective_rate == Decimal("2.900")
    assert other_item.category == FeeCategory.CARD_BRAND
    assert other_item.amount == Decimal("110.00")
    assert other_item.brands == {"Visa", "Mastercard"}


def test_processor_rate_excludes_card_brand_fees() -> None:
    analysis = analyze_statement(SAMPLE)

    assert analysis.processor_total == Decimal("540.00")
    assert analysis.processor_rate == Decimal("0.5400")


CARD_PROCESSING_SAMPLE = """
YOUR CARD PROCESSING STATEMENT
SAMPLE RESTAURANT Page 1 of 9 THIS IS NOT A BILL
Statement Period 06/01/26 - 06/30/26
Page 1 Total Amount Submitted $126,253.02
Page 4 Fees -$4,978.47
FEES Amount charged to authorize, process and settle card transactions
TRANSACTION FEES Type Amount
MASTERCARD
MC-DOMESTIC MERIT I (DB) Interchange charges -$0.79
MASTERCARD ASSESSMENT FEE 0.0014 TIMES $26675.12 Interchange charges -$37.35
MASTERCARD SALES TRANS FEE 634 TRANSACTIONS AT 0.05 Service charges -$31.70
MASTERCARD SALES DISCOUNT 0.0025 DISC RATE TIMES $18361.51 Service charges -$45.90
VISA
VISA SALES TRANS FEE 1405 TRANSACTIONS AT 0.05 Service charges -$70.25
VISA SALES DISCOUNT 0.0025 DISC RATE TIMES $39709.94 Service charges -$99.27
VISA AUTH FEE 2675 TRANSACTIONS AT 0.25 Fees -$668.75
TOTAL -$4,978.47
Total Interchange Charges/Program Fees -$2,934.27
"""


def test_pricing_summary_detects_ic_plus_markup_and_transaction_fee() -> None:
    analysis = analyze_statement(CARD_PROCESSING_SAMPLE)
    pricing = analysis.pricing_summary

    assert pricing.program_type == "IC+"
    assert pricing.rate == Decimal("0.2500")
    assert pricing.per_transaction_fee == Decimal("0.30")


FISERV_STYLE_CARD_PROCESSING_SAMPLE = """
YOUR CARD PROCESSING STATEMENT
SAMPLE SERVICE COMPANY Page 1 of 5 THIS IS NOT A BILL
StatementPeriod 06/01/26 - 06/30/26
Page 3 Amounts Submitted $16,154.47
Page 3 Fees Charged -$711.66
Month End Charge -$708.98
Less Discount Paid -$2.68
FEES CHARGED
Date Type Description Volume Rate Total
MASTERCARD
06/30/26 CF OTHER ITEM FEES 9.00 0.02000 -$0.18
06/30/26 CF DISC 1 3740.84 0.03000 -$112.22
06/30/26 CF DUES & ASSESSMENTS 0.00000 -$3.08
VISA
06/30/26 CF OTHER ITEM FEES 18.00 0.02000 -$0.36
06/30/26 CF DISC 1 6587.41 0.03000 -$197.62
06/30/26 CF DUES & ASSESSMENTS 0.00000 -$7.25
Total Card Fees -$320.00
06/30/26 MISC STATEMENT FEE 0.00000 -$10.00
Total (Misc Fees and Card Fees) -$711.66
"""


def test_pricing_summary_detects_fiserv_style_markup() -> None:
    analysis = analyze_statement(FISERV_STYLE_CARD_PROCESSING_SAMPLE)
    pricing = analysis.pricing_summary
    dues = next(group for group in analysis.comparison_groups_for(ComparisonRole.CARD_PROCESSING) if group.normalized_name == "Dues And Assessments")

    assert pricing.program_type == "IC+"
    assert pricing.rate == Decimal("3.00000")
    assert pricing.per_transaction_fee == Decimal("0.02000")
    assert analysis.customer_paid_fees == Decimal("2.68")
    assert analysis.merchant_paid_total_fees == Decimal("708.98")
    assert dues.amount == Decimal("10.33")


PAYSAFE_SAMPLE = """
MERCHANT SERVICES
YOUR CARD PROCESSING STATEMENT
SAMPLE RETAIL MERCHANT Page 1 of 5 THIS IS NOT A BILL
StatementPeriod 06/01/26 - 06/30/26
Customer Service 800-000-0000
Page 2 Amounts Submitted $55,687.60
Page 3 Fees Charged -$2,537.66
Month End Charge -$395.93
Less Discount Paid -$2,141.73
FEES CHARGED
Date Type Description Volume Rate Total
MASTERCARD
06/30/26 CF NQUAL DISC 10287.96 0.03846 -$395.67
06/30/26 CF QUAL DISC 1756.03 0.03846 -$67.54
06/30/26 CF MQUAL DISC 484.53 0.03846 -$18.64
06/30/26 CF DUES & ASSESSMENTS 0.00000 -$17.54
AUTHS & AVS
06/30/26 CF CPU GTWY 232.00 0.1000 -$23.20
06/30/26 CF ACQ SUPPORT FEE 451.53 0.00847 -$3.82
AMEXCT043
06/30/26 CF SYSTEM PROCESSING FEE 5078.66 0.00400 -$20.31
VISA
06/30/26 CF CR DUES AND ASSESS 19616.89 0.00140 -$27.46
06/30/26 CF NQUAL DISC 17462.03 0.03846 -$671.59
VS OFLN DB
06/30/26 CF DB DUES AND ASSESS 16860.52 0.00130 -$21.92
06/30/26 CF QUAL DISC 7134.05 0.03846 -$274.38
Total Card Fees -$2,443.44
06/30/26 MISC BATCH HEADER 42.00 0.2500 -$10.50
06/30/26 MISC **ADDITIONAL FEES 0.00000 -$54.90
06/30/26 MISC STATEMENT FEE 0.00000 -$10.00
Total (Misc Fees and Card Fees) -$2,537.66
"""


def test_paysafe_statement_detects_flat_pricing_when_tier_rates_match() -> None:
    analysis = analyze_statement(PAYSAFE_SAMPLE)
    pricing = analysis.pricing_summary
    dues = next(group for group in analysis.comparison_groups_for(ComparisonRole.CARD_PROCESSING) if group.normalized_name == "Dues And Assessments")

    assert analysis.processor_name == "Paysafe"
    assert analysis.total_processing == Decimal("55687.60")
    assert analysis.total_fees == Decimal("2537.66")
    assert analysis.customer_paid_fees == Decimal("2141.73")
    assert analysis.merchant_paid_total_fees == Decimal("395.93")
    assert pricing.program_type == "Flat rate"
    assert pricing.rate == Decimal("3.84600")
    assert pricing.per_transaction_fee == Decimal("0.1000")
    assert analysis.hidden_processor_total == Decimal("0")
    assert dues.amount == Decimal("66.92")


TRUE_TIERED_SAMPLE = PAYSAFE_SAMPLE.replace(
    "06/30/26 CF MQUAL DISC 484.53 0.03846 -$18.64",
    "06/30/26 CF MQUAL DISC 484.53 0.02846 -$13.79",
).replace(
    "06/30/26 CF QUAL DISC 1756.03 0.03846 -$67.54",
    "06/30/26 CF QUAL DISC 1756.03 0.01846 -$32.42",
)


def test_paysafe_statement_detects_tiered_pricing_when_rates_differ() -> None:
    pricing = analyze_statement(TRUE_TIERED_SAMPLE).pricing_summary

    assert pricing.program_type == "Tiered"


MAVERICK_SAMPLE = """
Processing Month: 06-26 1217
Merchant Number: xxxx000000000000
Amount Deducted:
SAMPLE MERCHANT OWNER
$ 118.48
Minimum Discount Fee is $49.99
Plan Summary
Plan Number of Amount of Number of Amount of Net Average Disc Disc Discount
Code Sales Sales Credits Credits Sales Ticket P/I % Due
VS 00 .00 00 .00 .00 .00 0.05000 0.5000 .00
VD 00 .00 00 .00 .00 .00 0.05000 0.5000 .00
MC 00 .00 00 .00 .00 .00 0.05000 0.5000 .00
Fees
Count Amount Rate % Rate Per Item Description Fees Paid Total
OTHER FEES:
3.50000 Monthly Online Access Fee .00 3.50
25.00000 Non-Receipt of PCI Compliance .00 25.00
39.99000 PCI Program .00 39.99
Total Other Fees: 68.49
Total Fees Due: 68.49
Minimum Discount Due 49.99
Fees Due 68.49
Amount Deducted 118.48
"""


def test_maverick_statement_detects_flat_rate_and_monthly_fees() -> None:
    analysis = analyze_statement(MAVERICK_SAMPLE)
    pricing = analysis.pricing_summary

    assert analysis.processor_name == "Maverick"
    assert analysis.total_processing == Decimal("0.00")
    assert analysis.total_fees == Decimal("118.48")
    assert pricing.program_type == "Flat rate"
    assert pricing.rate == Decimal("0.5000")
    assert pricing.per_transaction_fee == Decimal("0.05000")
    assert analysis.card_processing_processor_total == Decimal("0")
    assert analysis.monthly_optional_processor_total == Decimal("118.48")
