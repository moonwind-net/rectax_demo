from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from ..config import settings


ROUNDING_MODE_MAP = {
    "floor": ROUND_FLOOR,
    "round": ROUND_HALF_UP,
    "ceil": ROUND_CEILING,
}


def normalize_rounding_mode(mode: str | None) -> str:
    return mode if mode in ROUNDING_MODE_MAP else "floor"


def normalize_rounding_level(level: str | None) -> str:
    return level if level in {"document", "tax_rate", "line"} else "tax_rate"


class CompanyTaxConfig:
    def __init__(self, jpy_rounding_mode: str, tax_rounding_level: str):
        self.jpy_rounding_mode = normalize_rounding_mode(jpy_rounding_mode)
        self.tax_rounding_level = normalize_rounding_level(tax_rounding_level)


def get_company_tax_config(company) -> CompanyTaxConfig:
    return CompanyTaxConfig(
        getattr(company, "jpy_rounding_mode", None) or settings.jpy_rounding_mode,
        getattr(company, "tax_rounding_level", None) or settings.tax_rounding_level,
    )


def round_to_yen(value: float | int | str | Decimal | None, mode: str) -> int | None:
    if value is None or value == "":
        return None
    rounding = ROUNDING_MODE_MAP[normalize_rounding_mode(mode)]
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=rounding))


def inclusive_tax_from_total(total_amount: float | int | str | Decimal | None, rate_percent: float | int | str | Decimal | None, mode: str) -> int | None:
    if total_amount in (None, "") or rate_percent in (None, ""):
        return None
    total = Decimal(str(total_amount))
    rate = Decimal(str(rate_percent))
    if total <= 0 or rate < 0:
        return None
    raw_tax = total * rate / (Decimal("100") + rate)
    return round_to_yen(raw_tax, mode)


def _allocate_by_cumulative(raw_values: list[Decimal], mode: str) -> list[int]:
    assigned: list[int] = []
    cumulative_raw = Decimal("0")
    cumulative_assigned = 0
    for raw_value in raw_values:
        cumulative_raw += raw_value
        next_cumulative_target = round_to_yen(cumulative_raw, mode) or 0
        next_value = next_cumulative_target - cumulative_assigned
        assigned.append(next_value)
        cumulative_assigned += next_value
    return assigned


def normalize_tax_lines(lines: list[dict] | None, mode: str, level: str) -> tuple[list[dict], int]:
    if not lines:
        return [], 0

    normalized_mode = normalize_rounding_mode(mode)
    normalized_level = normalize_rounding_level(level)

    prepared: list[dict] = []
    for raw_line in lines:
        tax_rate = raw_line.get("tax_rate")
        taxable_amount = raw_line.get("taxable_amount")
        if tax_rate in (None, "") or taxable_amount in (None, ""):
            continue
        normalized_taxable = round_to_yen(taxable_amount, normalized_mode)
        if normalized_taxable is None:
            continue
        rate_decimal = Decimal(str(tax_rate))
        raw_tax = Decimal(str(normalized_taxable)) * rate_decimal / Decimal("100")
        prepared.append(
            {
                "tax_rate": float(rate_decimal),
                "taxable_amount": normalized_taxable,
                "raw_tax": raw_tax,
                "is_reduced_tax": bool(raw_line.get("is_reduced_tax", False)),
            }
        )

    if not prepared:
        return [], 0

    if normalized_level == "line":
        tax_amounts = [round_to_yen(line["raw_tax"], normalized_mode) or 0 for line in prepared]
    elif normalized_level == "tax_rate":
        tax_amounts = [0] * len(prepared)
        grouped_indexes: dict[float, list[int]] = {}
        for index, line in enumerate(prepared):
            grouped_indexes.setdefault(line["tax_rate"], []).append(index)
        for indexes in grouped_indexes.values():
            raw_values = [prepared[index]["raw_tax"] for index in indexes]
            allocated = _allocate_by_cumulative(raw_values, normalized_mode)
            for position, index in enumerate(indexes):
                tax_amounts[index] = allocated[position]
    else:
        tax_amounts = _allocate_by_cumulative([line["raw_tax"] for line in prepared], normalized_mode)

    normalized_lines = []
    for index, line in enumerate(prepared):
        normalized_lines.append(
            {
                "tax_rate": line["tax_rate"],
                "taxable_amount": line["taxable_amount"],
                "tax_amount": tax_amounts[index],
                "is_reduced_tax": line["is_reduced_tax"],
            }
        )
    return normalized_lines, sum(line["tax_amount"] for line in normalized_lines)