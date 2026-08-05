from __future__ import annotations

import csv
import tkinter as tk
import tkinter.font as tkfont
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from merchant_statement_reader.extract import ExtractionError, extract_statement_text
from merchant_statement_reader.models import ComparisonRole, FeeGroup, StatementAnalysis
from merchant_statement_reader.parser import analyze_statement


class MerchantStatementApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Merchant Statement Reader")
        self.geometry("1380x760")
        self.minsize(1120, 640)
        self.analysis: StatementAnalysis | None = None
        self._build_style()
        self._build_layout()

    def _build_style(self) -> None:
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.configure(bg="#f6f7f9")
        self.style.configure("TFrame", background="#f6f7f9")
        self.style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        self.style.configure("TLabel", background="#f6f7f9", foreground="#1d2430", font=("Segoe UI", 10))
        self.style.configure("Panel.TLabel", background="#ffffff")
        self.style.configure("Title.TLabel", font=("Segoe UI Semibold", 18), background="#f6f7f9")
        self.style.configure("Metric.TLabel", font=("Segoe UI Semibold", 20), background="#ffffff")
        self.style.configure("FeesMetric.TLabel", font=("Segoe UI Semibold", 15), background="#ffffff")
        self.style.configure("PricingMetric.TLabel", font=("Segoe UI Semibold", 15), background="#ffffff")
        self.style.configure("MetricName.TLabel", foreground="#5c6675", background="#ffffff")
        self.style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8))
        self.style.configure("Treeview", font=("Segoe UI", 10), rowheight=28)
        self.style.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))

    def _build_layout(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Merchant Statement Reader", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Upload Statement", command=self.open_statement).pack(side=tk.RIGHT)
        ttk.Button(header, text="Export CSV", command=self.export_csv).pack(side=tk.RIGHT, padx=(0, 8))

        self.status_var = tk.StringVar(value="Upload a PDF or text statement to begin.")
        ttk.Label(outer, textvariable=self.status_var).pack(fill=tk.X, pady=(8, 16))

        metrics = ttk.Frame(outer)
        metrics.pack(fill=tk.X, pady=(0, 10))
        self.metric_vars = {
            "processing": self._metric(metrics, "Total Processing", 0),
            "fees": self._metric(metrics, "Total Fees", 1, "FeesMetric.TLabel"),
            "effective": self._metric(metrics, "Effective Rate", 2),
            "processor": self._metric(metrics, "Processor Pricing", 3, "PricingMetric.TLabel"),
        }

        source_metrics = ttk.Frame(outer)
        source_metrics.pack(fill=tk.X, pady=(0, 16))
        self.source_metric_vars = {
            "processor_iso": self._metric(source_metrics, "Processor / ISO Fees", 0, "FeesMetric.TLabel"),
            "card_brand": self._metric(source_metrics, "Card Brand / Network", 1, "FeesMetric.TLabel"),
            "daily_paid": self._metric(source_metrics, "Daily Paid", 2, "FeesMetric.TLabel"),
        }

        main = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        card_brand_panel = ttk.Frame(main, style="Panel.TFrame", padding=10)
        processor_panel = ttk.Frame(main, style="Panel.TFrame", padding=10)
        main.add(card_brand_panel, weight=1)
        main.add(processor_panel, weight=1)
        self.card_brand_total_var = self._build_fee_panel(card_brand_panel, "Card Processing Charges")
        self.processor_total_var = self._build_fee_panel(processor_panel, "Monthly / Optional Charges")

    def _metric(self, parent: ttk.Frame, name: str, column: int, value_style: str = "Metric.TLabel") -> tk.StringVar:
        card = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 10, 0))
        parent.columnconfigure(column, weight=1)
        ttk.Label(card, text=name, style="MetricName.TLabel").pack(anchor=tk.W)
        value = tk.StringVar(value="--")
        ttk.Label(card, textvariable=value, style=value_style).pack(anchor=tk.W, pady=(4, 0))
        return value

    def _build_fee_panel(self, parent: ttk.Frame, title: str) -> tk.StringVar:
        heading = ttk.Frame(parent, style="Panel.TFrame")
        heading.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(heading, text=title, style="Panel.TLabel", font=("Segoe UI Semibold", 13)).pack(side=tk.LEFT)
        total_var = tk.StringVar(value="$0.00")
        ttk.Label(heading, textvariable=total_var, style="Panel.TLabel", font=("Segoe UI Semibold", 13)).pack(side=tk.RIGHT)

        columns = tuple(COLUMN_HEADINGS)
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for column in columns:
            tree.heading(column, text=COLUMN_HEADINGS[column])
            tree.column(column, width=COLUMN_MIN_WIDTHS[column], minwidth=COLUMN_MIN_WIDTHS[column], anchor=tk.W, stretch=True)

        yscroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.tag_configure("daily_paid", background="#fff3bf", foreground="#1d2430")
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        if title.startswith("Card"):
            self.card_brand_tree = tree
        else:
            self.processor_tree = tree
        return total_var

    def open_statement(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose merchant statement",
            filetypes=[("Statements", "*.pdf *.txt"), ("PDF files", "*.pdf"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            text = extract_statement_text(path)
            self.analysis = analyze_statement(text)
        except ExtractionError as exc:
            messagebox.showerror("Could not read statement", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("Could not parse statement", f"Unexpected error: {exc}")
            return

        self.status_var.set(f"Loaded {Path(path).name} using {self.analysis.processor_name}.")
        self._render_analysis()

    def export_csv(self) -> None:
        if not self.analysis:
            messagebox.showinfo("Nothing to export", "Upload a statement first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export fee table",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        groups = self.analysis.grouped_fees()
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Comparison Role", "Category", "Fee", "Amount", "Volume", "Source", "Brands", "Rates", "Items", "Confidence", "Raw Names"])
            for group in groups:
                writer.writerow([group.comparison_role.value] + group_to_row(group) + [", ".join(sorted(group.raw_names))])
        messagebox.showinfo("Export complete", f"Saved {Path(path).name}.")

    def _render_analysis(self) -> None:
        assert self.analysis is not None
        analysis = self.analysis
        self.metric_vars["processing"].set(money(analysis.total_processing))
        self.metric_vars["fees"].set(total_fees_display(analysis))
        self.metric_vars["effective"].set(percent(analysis.effective_rate))
        self.metric_vars["processor"].set(analysis.pricing_summary.display_text)

        card_groups = card_processing_panel_groups(analysis)
        monthly_groups = analysis.comparison_groups_for(ComparisonRole.MONTHLY_OPTIONAL)
        source_totals = fee_source_totals(card_groups, monthly_groups, analysis)
        self.source_metric_vars["processor_iso"].set(money(source_totals["processor_iso"]))
        self.source_metric_vars["card_brand"].set(money(source_totals["card_brand"]))
        self.source_metric_vars["daily_paid"].set(money(source_totals["daily_paid"]))
        self._render_table(self.card_brand_tree, card_groups, mark_daily_paid=bool(analysis.customer_paid_fees))
        self._render_table(self.processor_tree, monthly_groups)
        self.card_brand_total_var.set(panel_total_display(month_end_groups_total(card_groups, analysis), analysis.customer_paid_fees))
        self.processor_total_var.set(money(month_end_groups_total(monthly_groups, analysis)))
        unknown = f" Needs review: {money(analysis.unknown_total)}." if analysis.unknown_total else ""
        hidden = f" Hidden from comparison: {money(analysis.hidden_processor_total)}." if analysis.hidden_processor_total else ""
        customer_paid = (
            f" Customer-paid fees removed from total: {money(analysis.customer_paid_fees)}."
            if analysis.customer_paid_fees
            else ""
        )
        self.status_var.set(
            f"Loaded using {analysis.processor_name}. Merchant: {analysis.merchant_name or 'not detected'}. "
            f"Period: {analysis.statement_period or 'not detected'}. Month-end fees shown: "
            f"{money(analysis.merchant_paid_total_fees)}.{customer_paid}{unknown}{hidden}"
        )

    def _render_table(self, tree: ttk.Treeview, groups: list[FeeGroup], mark_daily_paid: bool = False) -> None:
        tree.delete(*tree.get_children())
        for group in groups:
            tags = ("daily_paid",) if mark_daily_paid and group.is_likely_daily_paid else ()
            tree.insert("", tk.END, values=group_to_display_row(group, is_daily_paid=bool(tags)), tags=tags)
        self.after_idle(lambda: autofit_tree_columns(tree))


COLUMN_HEADINGS = {
    "fee": "Fee",
    "amount": "Amount",
    "volume": "Volume",
    "source": "Source",
    "rates": "Rate",
    "items": "Items",
}

COLUMN_MIN_WIDTHS = {
    "fee": 150,
    "amount": 82,
    "volume": 78,
    "source": 112,
    "rates": 62,
    "items": 54,
}

COLUMN_MAX_WIDTHS = {
    "fee": 290,
    "amount": 120,
    "volume": 120,
    "source": 165,
    "rates": 125,
    "items": 75,
}


def autofit_tree_columns(tree: ttk.Treeview) -> None:
    columns = list(tree["columns"])
    text_font = tkfont.nametofont("TkDefaultFont")
    heading_font = tkfont.nametofont("TkHeadingFont")
    desired: dict[str, int] = {}

    for column in columns:
        heading_width = heading_font.measure(COLUMN_HEADINGS[column]) + 24
        value_width = max(
            (text_font.measure(str(tree.set(item, column))) + 24 for item in tree.get_children()),
            default=0,
        )
        desired[column] = min(max(heading_width, value_width, COLUMN_MIN_WIDTHS[column]), COLUMN_MAX_WIDTHS[column])

    available = max(tree.winfo_width() - 22, sum(COLUMN_MIN_WIDTHS[column] for column in columns))
    total_desired = sum(desired.values())
    if total_desired > available:
        desired = fit_columns_to_width(desired, available)
    elif total_desired < available:
        desired["fee"] += available - total_desired

    for column in columns:
        tree.column(column, width=int(desired[column]))


def fit_columns_to_width(widths: dict[str, int], available: int) -> dict[str, int]:
    fitted = widths.copy()
    excess = sum(fitted.values()) - available
    shrinkable = [column for column in fitted if fitted[column] > COLUMN_MIN_WIDTHS[column]]
    while excess > 0 and shrinkable:
        per_column = max(1, excess // len(shrinkable))
        next_shrinkable: list[str] = []
        for column in shrinkable:
            room = fitted[column] - COLUMN_MIN_WIDTHS[column]
            shrink = min(room, per_column)
            fitted[column] -= shrink
            excess -= shrink
            if fitted[column] > COLUMN_MIN_WIDTHS[column]:
                next_shrinkable.append(column)
        shrinkable = next_shrinkable
    return fitted


def group_to_row(group: FeeGroup) -> list[str]:
    return [
        group.category.value,
        group.normalized_name,
        money(group.amount),
        volume(group.volume),
        group.source_label,
        ", ".join(sorted(group.brands)),
        ", ".join(sorted(group.rates)),
        str(group.item_count or ""),
        f"{int(group.confidence * 100)}%",
    ]


def group_to_display_row(group: FeeGroup, is_daily_paid: bool = False) -> list[str]:
    fee_name = f"{group.normalized_name} (Daily paid)" if is_daily_paid else group.normalized_name
    return [
        fee_name,
        money(group.amount),
        volume(group.volume),
        group.source_label,
        ", ".join(sorted(group.rates)),
        str(group.item_count or ""),
    ]


def card_processing_panel_groups(analysis: StatementAnalysis) -> list[FeeGroup]:
    groups = [
        *analysis.comparison_groups_for(ComparisonRole.CARD_PROCESSING),
        *analysis.comparison_groups_for(ComparisonRole.PASS_THROUGH),
    ]
    return sorted(
        groups,
        key=lambda group: (
            1 if analysis.customer_paid_fees and group.is_likely_daily_paid else 0,
            source_sort_rank(group),
            group.normalized_name.lower(),
        ),
    )


def source_sort_rank(group: FeeGroup) -> int:
    if group.source_label == "Processor / ISO":
        return 0
    if group.source_label == "Card brand / network":
        return 1
    return 2


def month_end_groups_total(groups: list[FeeGroup], analysis: StatementAnalysis) -> Decimal:
    return sum(
        (group.amount for group in groups if not (analysis.customer_paid_fees and group.is_likely_daily_paid)),
        Decimal("0"),
    )


def fee_source_totals(
    card_groups: list[FeeGroup],
    monthly_groups: list[FeeGroup],
    analysis: StatementAnalysis,
) -> dict[str, Decimal]:
    totals = {
        "processor_iso": Decimal("0"),
        "card_brand": Decimal("0"),
        "daily_paid": analysis.customer_paid_fees,
    }
    for group in [*card_groups, *monthly_groups]:
        if analysis.customer_paid_fees and group.is_likely_daily_paid:
            continue
        if group.source_label == "Processor / ISO":
            totals["processor_iso"] += group.amount
        elif group.source_label == "Card brand / network":
            totals["card_brand"] += group.amount
    return totals


def panel_total_display(month_end_total: Decimal, daily_paid: Decimal) -> str:
    if not daily_paid:
        return money(month_end_total)
    return f"{money(month_end_total)}\nDaily paid: {money(daily_paid)}"


def total_fees_display(analysis: StatementAnalysis) -> str:
    return money(analysis.merchant_paid_total_fees)


def money(value: Decimal) -> str:
    return f"${value:,.2f}"


def volume(value: Decimal) -> str:
    if not value:
        return ""
    return f"{value:,.2f}"


def percent(value: Decimal | None) -> str:
    if value is None:
        return "--"
    return f"{value:.2f}%"
