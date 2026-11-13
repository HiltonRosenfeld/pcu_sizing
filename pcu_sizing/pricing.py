"""
Pricing — Astra DB PCU and storage pricing, plus the region -> geography / tier mapping,
parsed from two web pages and cached on disk.

  * IBM "Astra DB pricing" page: prices by provider, geographic region (North America, EMEA,
    APAC, SA) and tier (Standard, Premium, Premium Plus).
  * DataStax "Astra DB Serverless database regions" page: for each specific cloud provider region
    (e.g. AWS ap-southeast-1) its geography and its pricing tier.

Both pages block automated requests, so they are supplied by the user as saved/rendered HTML
(uploaded or pasted).  refresh_pricing() parses whichever pages are given and merges them into
PRICING_FILE; load_pricing() reads that cache.  Nothing is fetched at app start.

Prices are in Resource Units (RU).  PCU prices are the *Reserved* (RCU) columns.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

PRICING_URL = "https://www.ibm.com/docs/en/watsonxdata/saas?topic=services-astra-db-pricing"
REGIONS_URL = "https://docs.datastax.com/astra-db-serverless/databases/database-regions.html"
PRICING_FILE = Path(__file__).resolve().parent.parent / "data" / "pricing.json"

PROVIDERS = ("AWS", "GCP", "Azure")
TIERS = ("Standard", "Premium", "Premium Plus")


# ---------------------------------------------------------------------------
# Parsing the pricing page
# ---------------------------------------------------------------------------

# Page table -> key used in the cache.  Matched on the table caption (all keywords, lower-case);
# more specific rules first (the cache add-on captions also contain "Large PCU Shared").
_TABLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("cache_shared",    ("cache optimization", "shared")),
    ("cache_dedicated", ("cache optimization", "dedicated")),
    ("large_shared",    ("large pcu", "shared")),
    ("large_dedicated", ("large pcu", "dedicated")),
    ("small",           ("small pcu",)),
    ("medium",          ("medium pcu",)),
    ("storage",         ("storage pricing",)),
]
_TABLE_LABELS = {
    "small": "Small PCU",
    "medium": "Medium PCU",
    "large_shared": "Large PCU - Shared",
    "large_dedicated": "Large PCU - Dedicated",
    "cache_shared": "Cache optimization add-on - Shared",
    "cache_dedicated": "Cache optimization add-on - Dedicated",
    "storage": "Storage pricing",
}
# Tables whose columns are "<tier> (Reserved)" / "<tier> (Hourly)"; others have one column per tier.
PCU_TABLES = frozenset({"small", "medium", "large_shared", "large_dedicated"})

_PROVIDER_ALIASES = {
    "aws": "AWS", "amazon": "AWS", "amazonwebservices": "AWS",
    "gcp": "GCP", "google": "GCP", "googlecloud": "GCP",
    "azure": "Azure", "microsoft": "Azure", "microsoftazure": "Azure",
}


def canonical_provider(raw) -> Optional[str]:
    """Return 'AWS' / 'GCP' / 'Azure' for a provider name as found in data, else None."""
    key = re.sub(r"[^a-z]", "", str(raw or "").lower())
    return _PROVIDER_ALIASES.get(key)


class _TableParser(HTMLParser):
    """Collects every <table> as {'caption': str, 'heading': str, 'rows': [[cell text, ...], ...]}."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[dict] = []
        self._table: Optional[dict] = None
        self._row: Optional[list[str]] = None
        self._cell: Optional[list[str]] = None
        self._in_caption = False
        self._heading = ""            # text of the most recent <h1>-<h4> before a table
        self._in_heading = False

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4"):
            self._in_heading = True
            self._heading = ""
        elif tag == "table":
            self._table = {"caption": "", "heading": " ".join(self._heading.split()), "rows": []}
            self.tables.append(self._table)
        elif self._table is None:
            return
        elif tag == "caption":
            self._in_caption = True
        elif tag == "tr":
            self._row = []
            self._table["rows"].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4"):
            self._in_heading = False
        elif tag == "caption":
            self._in_caption = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))  # also folds &nbsp;
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "table":
            self._table = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading += data
        if self._in_caption and self._table is not None:
            self._table["caption"] += data
        if self._cell is not None:
            self._cell.append(data)


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None  # blank cell: no price published for this combination


def _build_table(rows: list[list[str]]) -> dict:
    """rows (header first) -> {provider: {geo region: {column heading: price or None}}}."""
    if not rows or [c.lower() for c in rows[0][:2]] != ["provider", "region"]:
        return {}
    header = rows[0]
    table: dict = {}
    for row in rows[1:]:
        if len(row) < 3:
            continue
        provider = canonical_provider(row[0]) or row[0]
        prices = {header[i]: _to_float(row[i]) for i in range(2, min(len(row), len(header)))}
        table.setdefault(provider, {})[row[1]] = prices
    return table


def parse_pricing_html(html: str) -> dict:
    """
    Parse the saved pricing page into the cache structure (without refresh metadata).

    Raises ValueError if any of the seven pricing tables is missing or has no prices.
    """
    parser = _TableParser()
    parser.feed(html)

    tables: dict = {}
    for page_table in parser.tables:
        caption = " ".join(page_table["caption"].split()).lower()
        for key, keywords in _TABLE_RULES:
            if all(k in caption for k in keywords):
                if key not in tables:
                    tables[key] = _build_table(page_table["rows"])
                break

    def has_prices(table: dict) -> bool:
        return any(
            v is not None for geos in table.values() for cols in geos.values() for v in cols.values()
        )

    missing = [_TABLE_LABELS[k] for k in _TABLE_LABELS if not has_prices(tables.get(k, {}))]
    if missing:
        raise ValueError(
            "Could not find pricing data for: " + ", ".join(missing) + ". "
            "Supply the fully rendered Astra DB pricing page (saved after it finished loading)."
        )

    text = re.sub(r"<[^>]+>", " ", html)
    updated = re.search(r"Last Updated\s*:?\s*(\d{4}-\d{2}-\d{2})", text)
    return {
        "version": 1,
        "source_url": PRICING_URL,
        "page_last_updated": updated.group(1) if updated else None,
        "tables": tables,
    }


# ---------------------------------------------------------------------------
# Parsing the regions page
# ---------------------------------------------------------------------------

# Geography column on the regions page -> geographic pricing region on the pricing page.
_GEOGRAPHY_TO_GEO = {"americas": "North America", "asia pacific": "APAC", "emea": "EMEA"}
# The regions page files South America under "Americas"; these locations are priced as SA.
_SA_LOCATIONS = ("sao paulo", "são paulo", "santiago")


def _provider_from_heading(heading: str) -> Optional[str]:
    h = heading.lower()
    if "aws" in h or "amazon" in h:
        return "AWS"
    if "google" in h or "gcp" in h:
        return "GCP"
    if "azure" in h or "microsoft" in h:
        return "Azure"
    return None


def parse_regions_html(html: str) -> dict:
    """
    Parse the saved Astra DB regions page into {provider: {region: {"geo", "tier", "location"}}}.

    Each provider's table (identified by its heading, e.g. "AWS regions") lists a Location
    ("Americas Virginia"), a Region ("us-east-1") and a Pricing tier.  The geography is mapped to
    the pricing page's geographic region; São Paulo is mapped to SA.

    Raises ValueError if a provider's table is missing.
    """
    parser = _TableParser()
    parser.feed(html)

    regions: dict[str, dict[str, dict]] = {}
    for page_table in parser.tables:
        rows = page_table["rows"]
        provider = _provider_from_heading(page_table["heading"])
        if provider is None or not rows:
            continue
        header = [c.lower() for c in rows[0]]
        if "region" not in header or "pricing" not in header:
            continue
        i_loc = header.index("location") if "location" in header else None
        i_region, i_tier = header.index("region"), header.index("pricing")

        for row in rows[1:]:
            if len(row) <= max(i_region, i_tier):
                continue
            location = row[i_loc] if i_loc is not None else ""
            region, tier = row[i_region], row[i_tier]
            geography = next(
                (g for g in _GEOGRAPHY_TO_GEO if location.lower().startswith(g)), None
            )
            if not region or tier not in TIERS or geography is None:
                continue
            geo = _GEOGRAPHY_TO_GEO[geography]
            if any(name in location.lower() for name in _SA_LOCATIONS):
                geo = "SA"
            regions.setdefault(provider, {})[region] = {
                "geo": geo, "tier": tier, "location": location,
            }

    missing = [p for p in PROVIDERS if not regions.get(p)]
    if missing:
        raise ValueError(
            "Could not find region data for: " + ", ".join(missing) + ". Supply the fully "
            "rendered Astra DB Serverless database regions page."
        )
    return regions


# ---------------------------------------------------------------------------
# Cache file
# ---------------------------------------------------------------------------

def load_pricing(path: Path = PRICING_FILE) -> Optional[dict]:
    """Return the cached data, or None if the cache file is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def missing_parts(data: Optional[dict]) -> list[str]:
    """Names of the cached parts that are absent ("pricing page", "regions page")."""
    data = data or {}
    return [
        name for key, name in (("tables", "pricing page"), ("regions", "regions page"))
        if not data.get(key)
    ]


def refresh_pricing(
    pricing_html: Optional[str] = None,
    regions_html: Optional[str] = None,
    path: Path = PRICING_FILE,
) -> dict:
    """
    Parse the supplied page(s) and merge them into the cache file.

    Each page is optional: a page that is not supplied keeps its previously cached data.  If a
    supplied page fails to parse nothing is written and the existing cache is left untouched.
    """
    if not (pricing_html or "").strip() and not (regions_html or "").strip():
        raise ValueError("Supply the pricing page, the regions page, or both.")

    data = load_pricing(path) or {"version": 2}
    data["version"] = 2
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if (pricing_html or "").strip():
        parsed = parse_pricing_html(pricing_html)
        data.update({
            "source_url": parsed["source_url"],
            "page_last_updated": parsed["page_last_updated"],
            "tables": parsed["tables"],
            "pricing_refreshed_at": now,
        })
    if (regions_html or "").strip():
        data.update({
            "regions": parse_regions_html(regions_html),
            "regions_source_url": REGIONS_URL,
            "regions_refreshed_at": now,
        })
    data["refreshed_at"] = now  # stamp of the latest change; part of the app's cache key

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return data


# ---------------------------------------------------------------------------
# Price lookups
# ---------------------------------------------------------------------------

class PriceBook:
    """Rate lookups (RU) from cached pricing data.  Tier is given per lookup (it depends on region)."""

    def __init__(self, data: dict) -> None:
        self.data = data

    def _rate(self, table: str, provider: str, geo: str, tier: str) -> Optional[float]:
        column = f"{tier} (Reserved)" if table in PCU_TABLES else tier
        return self.data["tables"].get(table, {}).get(provider, {}).get(geo, {}).get(column)

    def pcu_rate(self, pcu_type, provider: str, geo: str, tier: str) -> Optional[float]:
        """
        Reserved RU per PCU per hour, or None if not published for this provider/region/tier.

        pcu_type needs `price_table` and `addon_table` attributes; a Cache Optimized PCU is the
        Large PCU price plus the cache optimization add-on.
        """
        base = self._rate(pcu_type.price_table, provider, geo, tier)
        if base is None:
            return None
        if pcu_type.addon_table:
            addon = self._rate(pcu_type.addon_table, provider, geo, tier)
            if addon is None:
                return None
            return round(base + addon, 6)
        return base

    def storage_rate(self, provider: str, geo: str, tier: str) -> Optional[float]:
        """RU per GB stored per month, or None if not published."""
        return self._rate("storage", provider, geo, tier)


# ---------------------------------------------------------------------------
# Cloud provider region -> geographic pricing region and tier (from the regions page)
# ---------------------------------------------------------------------------

def _norm_region(region) -> str:
    """Case/spacing-insensitive region key ('East US' == 'eastus'; 'us-east-1' == 'US_East_1')."""
    return re.sub(r"[\s_-]+", "", str(region or "").strip().lower())


def locate(data: dict, provider, region) -> Optional[tuple[str, str, str]]:
    """(canonical provider, geographic pricing region, tier) for a provider region, or None
    if the provider is unrecognised or the region is not on the regions page."""
    canon = canonical_provider(provider)
    if canon is None:
        return None
    wanted = _norm_region(region)
    for name, info in (data.get("regions") or {}).get(canon, {}).items():
        if _norm_region(name) == wanted:
            return canon, info["geo"], info["tier"]
    return None


def unmapped_locations(data: dict, databases) -> list[tuple[str, str]]:
    """Distinct (provider, region) pairs, as found in the data, that are not on the regions page."""
    seen: dict[tuple[str, str], None] = {}
    for db in databases:
        if locate(data, db.provider, db.region) is None:
            seen[(db.provider, db.region)] = None
    return list(seen)


def resolve_locations(data: dict, databases) -> list:
    """
    Return copies of the databases with a canonical `provider`, a `geo_region` and a `tier` set.

    Databases whose provider/region is not on the regions page keep a blank geo region and tier,
    so they cannot be priced (their groups are flagged "no pricing available").
    """
    resolved = []
    for db in databases:
        found = locate(data, db.provider, db.region)
        if found:
            resolved.append(replace(db, provider=found[0], geo_region=found[1], tier=found[2]))
        else:
            resolved.append(replace(db, geo_region="", tier=""))
    return resolved
