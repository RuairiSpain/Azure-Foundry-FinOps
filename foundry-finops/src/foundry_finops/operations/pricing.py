"""Azure Retail Prices API (public, no auth) and a price sheet for estimating token cost."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from .._ids import odata_quote as _odata
from ._base import _Operations


@dataclass
class ModelPrice:
    """USD per 1M tokens."""

    input: float
    output: float
    cached_input: float | None = None


def unit_multiplier(unit_of_measure: str) -> float | None:
    """Factor that turns a price per meter unit into a price per 1M tokens, or ``None`` if the unit isn't a token pack.

    ``"1K"`` (a price per thousand tokens) becomes x1000 and ``"1M"`` x1. Anything else (hours, images, ...) is not
    converted, so no price is made up for it.
    """
    unit = (unit_of_measure or "").strip().upper()
    if unit.startswith("1K"):
        return 1000.0
    if unit.startswith("1M"):
        return 1.0
    return None


def classify_meter(meter_name: str) -> tuple[str, str] | None:
    """Split a model meter name such as ``"gpt-5.4 mini inp glbl"`` into ``(model, kind)``.

    ``kind`` is ``input``, ``output`` or ``cached_input``. Returns ``None`` for meters that are not per-token
    input/output/cached meters (batch, fine-tuning and other meters).
    """
    meter = (meter_name or "").lower()
    if "batch" in meter or "ft" in meter.split():
        return None
    kind = ("output" if "outp" in meter else "cached_input" if "cache" in meter
            else "input" if "inp" in meter else None)
    if kind is None:
        return None
    model = meter.split(" inp")[0].split(" outp")[0].split(" cached")[0].replace(" glbl", "").strip()
    return (model, kind) if model else None


@dataclass
class PriceSheet:
    """Model → price lookup used to *estimate* cost from token counts.

    Retail prices aren't your negotiated prices. Either load your own with :meth:`from_mapping`, derive them from
    your billing with ``fo.cost.effective_prices(...).to_price_sheet()``, or apply a discount you know with
    :meth:`scaled`. Billed cost always comes from Cost Management.
    """

    prices: dict[str, ModelPrice] = field(default_factory=dict)
    currency: str = "USD"
    #: Where the prices came from: ``retail``, ``billed``, or ``custom``.
    source: str = "custom"
    #: Discount that :meth:`scaled` applied, in percent. It is an input supplied by the caller, not a service value.
    discount_percent: float = 0.0

    def scaled(self, discount_percent: float) -> PriceSheet:
        """A copy with every price reduced by ``discount_percent`` (for example ``15`` for 15 % off).

        Use it for a discount you know (an enterprise agreement rate, for instance). The percentage is applied as
        given and recorded on the new sheet; nothing is looked up. To see the discount your billing actually implies,
        compare a billed sheet with the retail one using :func:`effective_discounts`.
        """
        if not 0 <= discount_percent < 100:
            raise ValueError("discount_percent must be at least 0 and below 100")
        factor = 1 - discount_percent / 100
        scaled = {
            k: ModelPrice(v.input * factor, v.output * factor,
                          None if v.cached_input is None else v.cached_input * factor)
            for k, v in self.prices.items()
        }
        combined = (1 - (1 - self.discount_percent / 100) * factor) * 100  # successive discounts compound
        return PriceSheet(scaled, self.currency, self.source, round(combined, 6))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Mapping[str, float]], currency: str = "USD") -> PriceSheet:
        prices: dict[str, ModelPrice] = {}
        for model, v in data.items():
            try:
                prices[model.lower()] = ModelPrice(**v)
            except TypeError as exc:
                raise ValueError(
                    f"Invalid price for {model!r}: use keys input, output and optionally cached_input ({exc})"
                ) from exc
        return cls(prices, currency)

    def to_records(self) -> list[dict[str, Any]]:
        return [{"model": m, "input": p.input, "output": p.output, "cached_input": p.cached_input,
                 "currency": self.currency, "source": self.source, "discount_percent": self.discount_percent}
                for m, p in sorted(self.prices.items())]

    def lookup(self, model: str) -> ModelPrice | None:
        """Exact match, else the longest price key the model name extends with a version/date suffix.

        ``gpt-5.4-mini-2026-03-01`` resolves to ``gpt-5.4-mini``, but ``gpt-4o-mini`` must *not* fall
        back to ``gpt-4o``: a different model has a different price.
        """
        m = model.lower()
        if m in self.prices:
            return self.prices[m]
        best = max(
            (k for k in self.prices if m.startswith(k + "-") and m[len(k) + 1 : len(k) + 2].isdigit()),
            key=len,
            default=None,
        )
        return self.prices.get(best) if best else None

    def estimate(self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float | None:
        p = self.lookup(model)
        if p is None:
            return None
        cached_rate = p.cached_input if p.cached_input is not None else p.input
        cached = min(max(cached_tokens, 0), max(input_tokens, 0))  # cached tokens are a subset of the input
        uncached = max(input_tokens, 0) - cached
        return round((uncached * p.input + cached * cached_rate + max(output_tokens, 0) * p.output) / 1_000_000, 6)


class PricingOperations(_Operations):
    """Query public retail meters (unauthenticated)."""

    def retail_prices(self, odata_filter: str, *, currency: str = "USD") -> Iterator[dict[str, Any]]:
        """Iterate retail price items, e.g. ``"serviceName eq 'Foundry Models' and armRegionName eq 'eastus2'"``."""
        url: str | None = self._cloud.retail_prices
        params: dict[str, Any] | None = {
            "api-version": self._v.retail_prices, "$filter": odata_filter, "currencyCode": currency,
        }
        while url:
            body = self._t.request("GET", url, scope="", params=params, authenticate=False).body or {}
            yield from body.get("Items") or []
            url, params = body.get("NextPageLink"), None

    def openai_price_sheet(
        self, region: str = "eastus2", *, service_name: str = "Foundry Models", discount_percent: float = 0.0
    ) -> PriceSheet:
        """Best-effort per-1M-token sheet built from retail meters (Global/Standard, input/output).

        Meter naming changes over time, so check the result and override with your own prices.
        ``discount_percent`` applies a discount you know to every price (see :meth:`PriceSheet.scaled`); the default
        is none, i.e. the public retail price.
        """
        sheet: dict[str, dict[str, float]] = {}
        flt = f"serviceName eq '{_odata(service_name)}' and armRegionName eq '{_odata(region)}'"
        for item in self.retail_prices(flt):
            if item.get("type") not in (None, "Consumption"):  # skip reservations and dev/test meters
                continue
            mult = unit_multiplier(item.get("unitOfMeasure") or "")
            classified = classify_meter(item.get("meterName") or "")
            if mult is None or classified is None:
                continue
            try:
                price = float(item.get("retailPrice") or 0)
            except (TypeError, ValueError):
                continue
            model, kind = classified
            entry = sheet.setdefault(model, {"input": 0.0, "output": 0.0})
            if not entry.get(kind):  # keep the first non-zero meter per kind
                entry[kind] = price * mult
        out = PriceSheet.from_mapping({k: v for k, v in sheet.items() if v.get("input") and v.get("output")})
        out.source = "retail"
        return out.scaled(discount_percent) if discount_percent else out


@dataclass
class EffectivePrice:
    """What one meter actually cost per unit over a period, from billed cost and quantity."""

    meter: str
    unit: str
    quantity: float
    cost: float
    currency: str | None
    #: ``cost / quantity`` in the meter's own unit.
    unit_price: float
    #: Price per 1M tokens, only for token-priced meters (``1K`` and ``1M`` units); ``None`` otherwise.
    price_per_1m_tokens: float | None


@dataclass
class EffectivePrices:
    """Effective unit prices derived from your own billed cost (see ``fo.cost.effective_prices``).

    ``skipped`` lists meters that could not produce a price, each with the reason (no quantity, unknown currency mix,
    ...). Nothing is estimated for them.
    """

    rows: list[EffectivePrice] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]]) -> EffectivePrices:
        """Build from Cost Management rows holding ``Meter``, ``UnitOfMeasure``, ``Cost`` and ``UsageQuantity``."""
        out = cls()
        totals: dict[tuple[str, str, str | None], list[float]] = {}
        for r in records:
            meter, unit = str(r.get("Meter") or ""), str(r.get("UnitOfMeasure") or "")
            cost = r.get("Cost", r.get("PreTaxCost"))
            qty = r.get("UsageQuantity")
            if not meter:
                continue
            if cost is None or qty is None:
                out.skipped.append({"meter": meter, "unit": unit, "reason": "the row has no cost or quantity"})
                continue
            acc = totals.setdefault((meter, unit, r.get("Currency")), [0.0, 0.0])
            acc[0] += float(cost)
            acc[1] += float(qty)
        for (meter, unit, currency), (cost, qty) in sorted(totals.items()):
            if qty <= 0:
                out.skipped.append({"meter": meter, "unit": unit, "reason": "no billed quantity in the period"})
                continue
            unit_price = cost / qty
            mult = unit_multiplier(unit)
            out.rows.append(EffectivePrice(meter, unit, qty, cost, currency, unit_price,
                                           None if mult is None else unit_price * mult))
        return out

    def to_records(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.rows]

    def to_price_sheet(self) -> PriceSheet:
        """A :class:`PriceSheet` (USD or your billing currency per 1M tokens) from the token-priced meters.

        Only models with both an input and an output price appear. Meters in more than one currency are refused.
        """
        currencies = {r.currency for r in self.rows if r.price_per_1m_tokens is not None}
        if len(currencies) > 1:
            raise ValueError(f"The billed meters use several currencies ({sorted(map(str, currencies))}); "
                             "query one currency at a time")
        sheet: dict[str, dict[str, float]] = {}
        for r in self.rows:
            classified = classify_meter(r.meter)
            if r.price_per_1m_tokens is None or classified is None:
                continue
            model, kind = classified
            sheet.setdefault(model, {})[kind] = r.price_per_1m_tokens
        out = PriceSheet.from_mapping({m: v for m, v in sheet.items() if v.get("input") and v.get("output")},
                                      currency=next(iter(currencies), None) or "USD")
        out.source = "billed"
        return out


def effective_discounts(billed: PriceSheet, retail: PriceSheet) -> list[dict[str, Any]]:
    """Compare prices derived from your billing with retail prices, model by model.

    ``discount_percent`` is ``(1 - billed / retail) x 100`` for each model and price kind present in **both** sheets,
    so it is what your own invoices imply. Models missing from either sheet are left out. Negative values mean you paid
    more than retail. Sheets in different currencies are refused.
    """
    if billed.currency != retail.currency:
        raise ValueError(f"Can't compare {billed.currency} billed prices with {retail.currency} retail prices")
    out = []
    for model in sorted(set(billed.prices) & set(retail.prices)):
        b, r = billed.prices[model], retail.prices[model]
        for kind in ("input", "output", "cached_input"):
            bv, rv = getattr(b, kind), getattr(r, kind)
            if bv is None or not rv:
                continue
            out.append({"model": model, "kind": kind, "billed_per_1m": round(bv, 6), "retail_per_1m": round(rv, 6),
                        "discount_percent": round((1 - bv / rv) * 100, 2), "currency": billed.currency})
    return out
