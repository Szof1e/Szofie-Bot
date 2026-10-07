"""Country catalog and persistent territory helpers for the geopolitical game."""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Country:
    id: str
    name: str
    flag: str
    gdp_b: int
    tier: str
    price: int


def _c(code: str, name: str, flag: str, gdp: int, tier: str, price: int) -> Country:
    return Country(code, name, flag, gdp, tier, price)


_COUNTRY_ROWS = "\nafg|AF|Afghanistan|18\nalb|AL|Albania|27\ndza|DZ|Algeria|269\nand|AD|Andorra|4\nago|AO|Angola|103\natg|AG|Antigua and Barbuda|2\narg|AR|Argentina|638\narm|AM|Armenia|26\naus|AU|Australia|1757\naut|AT|Austria|535\naze|AZ|Azerbaijan|74\nbhs|BS|Bahamas|16\nbhr|BH|Bahrain|47\nbgd|BD|Bangladesh|450\nbrb|BB|Barbados|8\nblr|BY|Belarus|79\nbel|BE|Belgium|671\nblz|BZ|Belize|3\nben|BJ|Benin|21\nbtn|BT|Bhutan|3\nbol|BO|Bolivia|55\nbih|BA|Bosnia and Herzegovina|30\nbwa|BW|Botswana|19\nbra|BR|Brazil|2186\nbrn|BN|Brunei|15\nbgr|BG|Bulgaria|113\nbfa|BF|Burkina Faso|23\nbdi|BI|Burundi|3\ncpv|CV|Cabo Verde|3\nkhm|KH|Cambodia|46\ncmr|CM|Cameroon|53\ncan|CA|Canada|2270\ncaf|CF|Central African Republic|3\ntcd|TD|Chad|20\nchl|CL|Chile|329\nchn|CN|China|18730\ncol|CO|Colombia|421\ncom|KM|Comoros|2\ncog|CG|Republic of the Congo|16\ncod|CD|DR Congo|76\ncri|CR|Costa Rica|97\nciv|CI|Côte d’Ivoire|87\nhrv|HR|Croatia|93\ncub|CU|Cuba|107\ncyp|CY|Cyprus|38\ncze|CZ|Czechia|347\ndnk|DK|Denmark|425\ndji|DJ|Djibouti|4\ndma|DM|Dominica|1\ndom|DO|Dominican Republic|124\necu|EC|Ecuador|124\negy|EG|Egypt|389\nslv|SV|El Salvador|35\ngnq|GQ|Equatorial Guinea|13\neri|ER|Eritrea|3\nest|EE|Estonia|43\nswz|SZ|Eswatini|5\neth|ET|Ethiopia|150\nfji|FJ|Fiji|6\nfin|FI|Finland|299\nfra|FR|France|3160\ngab|GA|Gabon|21\ngmb|GM|Gambia|2\ngeo|GE|Georgia|34\ndeu|DE|Germany|4686\ngha|GH|Ghana|83\ngrc|GR|Greece|256\ngrd|GD|Grenada|1\ngtm|GT|Guatemala|113\ngin|GN|Guinea|25\ngnb|GW|Guinea-Bissau|2\nguy|GY|Guyana|25\nhti|HT|Haiti|24\nhnd|HN|Honduras|37\nhun|HU|Hungary|223\nisl|IS|Iceland|33\nind|IN|India|3761\nidn|ID|Indonesia|1396\nirn|IR|Iran|475\nirq|IQ|Iraq|280\nirl|IE|Ireland|609\nisr|IL|Israel|542\nita|IT|Italy|2383\njam|JM|Jamaica|22\njpn|JP|Japan|4190\njor|JO|Jordan|59\nkaz|KZ|Kazakhstan|291\nken|KE|Kenya|120\nkir|KI|Kiribati|1\nprk|KP|North Korea|20\nkor|KR|South Korea|1875\nkwt|KW|Kuwait|161\nkgz|KG|Kyrgyzstan|18\nlao|LA|Laos|17\nlva|LV|Latvia|44\nlbn|LB|Lebanon|26\nlso|LS|Lesotho|2\nlbr|LR|Liberia|5\nlby|LY|Libya|48\nlie|LI|Liechtenstein|9\nltu|LT|Lithuania|86\nlux|LU|Luxembourg|93\nmdg|MG|Madagascar|18\nmwi|MW|Malawi|11\nmys|MY|Malaysia|422\nmdv|MV|Maldives|7\nmli|ML|Mali|27\nmlt|MT|Malta|25\nmhl|MH|Marshall Islands|1\nmrt|MR|Mauritania|11\nmus|MU|Mauritius|15\nmex|MX|Mexico|1830\nfsm|FM|Micronesia|1\nmda|MD|Moldova|18\nmco|MC|Monaco|11\nmng|MN|Mongolia|24\nmne|ME|Montenegro|8\nmar|MA|Morocco|161\nmoz|MZ|Mozambique|23\nmmr|MM|Myanmar|74\nnam|NA|Namibia|14\nnru|NR|Nauru|1\nnpl|NP|Nepal|43\nnld|NL|Netherlands|1214\nnzl|NZ|New Zealand|261\nnic|NI|Nicaragua|20\nner|NE|Niger|20\nnga|NG|Nigeria|252\nmkd|MK|North Macedonia|17\nnor|NO|Norway|501\nomn|OM|Oman|107\npak|PK|Pakistan|372\nplw|PW|Palau|1\npan|PA|Panama|87\npng|PG|Papua New Guinea|31\npry|PY|Paraguay|45\nper|PE|Peru|292\nphl|PH|Philippines|462\npol|PL|Poland|918\nprt|PT|Portugal|314\nqat|QA|Qatar|216\nrou|RO|Romania|383\nrus|RU|Russia|2186\nrwa|RW|Rwanda|15\nkna|KN|St. Kitts and Nevis|1\nlca|LC|St. Lucia|3\nvct|VC|St. Vincent and the Grenadines|1\nwsm|WS|Samoa|1\nsmr|SM|San Marino|2\nstp|ST|São Tomé and Príncipe|1\nsau|SA|Saudi Arabia|1254\nsen|SN|Senegal|32\nsrb|RS|Serbia|90\nsyc|SC|Seychelles|2\nsle|SL|Sierra Leone|7\nsgp|SG|Singapore|573\nsvk|SK|Slovakia|141\nsvn|SI|Slovenia|73\nslb|SB|Solomon Islands|2\nsom|SO|Somalia|12\nzaf|ZA|South Africa|401\nssd|SS|South Sudan|5\nesp|ES|Spain|1726\nlka|LK|Sri Lanka|100\nsdn|SD|Sudan|50\nsur|SR|Suriname|4\nswe|SE|Sweden|605\nche|CH|Switzerland|970\nsyr|SY|Syria|24\ntjk|TJ|Tajikistan|14\ntza|TZ|Tanzania|79\ntha|TH|Thailand|529\ntls|TL|Timor-Leste|2\ntgo|TG|Togo|11\nton|TO|Tonga|1\ntto|TT|Trinidad and Tobago|26\ntun|TN|Tunisia|51\ntur|TR|Türkiye|1359\ntkm|TM|Turkmenistan|44\ntuv|TV|Tuvalu|1\nuga|UG|Uganda|54\nukr|UA|Ukraine|191\nare|AE|United Arab Emirates|552\ngbr|GB|United Kingdom|3696\nusa|US|United States|29298\nury|UY|Uruguay|82\nuzb|UZ|Uzbekistan|121\nvut|VU|Vanuatu|1\nvat|VA|Vatican City|1\nven|VE|Venezuela|121\nvnm|VN|Vietnam|476\nyem|YE|Yemen|22\nzmb|ZM|Zambia|25\nzwe|ZW|Zimbabwe|42\npse|PS|Palestine|16\n"


def _flag(alpha2: str) -> str:
    return "".join((chr(127462 + ord(char) - ord("A")) for char in alpha2.upper()))


def _tier(gdp_b: int) -> str:
    if gdp_b >= 3500:
        return "global"
    if gdp_b >= 1000:
        return "major"
    if gdp_b >= 250:
        return "regional"
    if gdp_b >= 25:
        return "developing"
    return "micro"


_LEGACY_PRICES = {
    "are": 40000000000,
    "arg": 22000000000,
    "aus": 300000000000,
    "bel": 48000000000,
    "bra": 360000000000,
    "brn": 500000000,
    "btn": 150000000,
    "can": 350000000000,
    "che": 62000000000,
    "chn": 1850000000000,
    "deu": 1300000000000,
    "dnk": 25000000000,
    "egy": 20000000000,
    "esp": 280000000000,
    "fin": 9500000000,
    "fji": 250000000,
    "fra": 450000000000,
    "gbr": 475000000000,
    "grc": 7500000000,
    "hun": 6000000000,
    "idn": 240000000000,
    "ind": 1150000000000,
    "irl": 42000000000,
    "isl": 900000000,
    "ita": 390000000000,
    "jpn": 1220000000000,
    "ken": 3500000000,
    "kor": 320000000000,
    "lka": 2500000000,
    "lux": 700000000,
    "mar": 4500000000,
    "mco": 750000000,
    "mex": 260000000000,
    "mys": 32000000000,
    "nld": 68000000000,
    "nor": 24000000000,
    "nzl": 8000000000,
    "phl": 28000000000,
    "plw": 100000000,
    "pol": 55000000000,
    "prt": 9000000000,
    "qat": 7000000000,
    "rus": 340000000000,
    "sau": 70000000000,
    "sgp": 38000000000,
    "swe": 44000000000,
    "tha": 35000000000,
    "tur": 72000000000,
    "ukr": 5500000000,
    "usa": 2900000000000,
    "vnm": 30000000000,
    "zaf": 26000000000,
}


def _price(code: str, gdp_b: int) -> int:
    if code in _LEGACY_PRICES:
        return _LEGACY_PRICES[code]
    tier = _tier(gdp_b)
    if tier == "global":
        return max(1000000000000, gdp_b * 100000000)
    if tier == "major":
        return 250000000000 + gdp_b * 80000000
    if tier == "regional":
        return 15000000000 + gdp_b * 50000000
    if tier == "developing":
        return 1000000000 + gdp_b * 30000000
    return max(100000000, 100000000 + gdp_b * 50000000)


COUNTRIES: List[Country] = []
for _row in _COUNTRY_ROWS.strip().splitlines():
    _code, _alpha2, _name, _gdp = _row.split("|", 3)
    _gdp_b = max(1, int(_gdp))
    COUNTRIES.append(_c(_code, _name, _flag(_alpha2), _gdp_b, _tier(_gdp_b), _price(_code, _gdp_b)))
BY_ID = {c.id: c for c in COUNTRIES}
TIER_DEFENSE = {"micro": 18, "developing": 32, "regional": 50, "major": 70, "global": 90}
DEFAULT_DEVELOPMENT_MAX_LEVEL = 20
DEFAULT_DEVELOPMENT_GDP_PCT = 3
DEFAULT_DEVELOPMENT_BASE_COST_PCT = 5
DEFAULT_DEVELOPMENT_COST_GROWTH_PCT = 10


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(raw: Any) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def state(doc: Dict[str, Any]) -> Dict[str, Any]:
    root = doc.setdefault("countries", {})
    if not isinstance(root, dict):
        root = doc["countries"] = {}
    if not isinstance(root.get("territories"), dict):
        root["territories"] = {}
    if not isinstance(root.get("history"), list):
        root["history"] = []
    return root


def owned(doc: Dict[str, Any], user_id: int) -> List[tuple[Country, Dict[str, Any]]]:
    out = []
    for cid, territory in state(doc)["territories"].items():
        if isinstance(territory, dict) and int(territory.get("owner", 0)) == int(user_id) and (cid in BY_ID):
            out.append((BY_ID[cid], territory))
    out.sort(key=lambda pair: str(pair[1].get("acquired_at", "")))
    return out


def production_multiplier(index: int) -> float:
    """Every territory produces at its full rate, regardless of empire size."""
    return 1.0


def development_level(territory: Optional[Dict[str, Any]]) -> int:
    if not isinstance(territory, dict):
        return 0
    try:
        return max(0, int(territory.get("development_level", 0)))
    except (TypeError, ValueError):
        return 0


def development_cap(territory: Optional[Dict[str, Any]], default: int = DEFAULT_DEVELOPMENT_MAX_LEVEL) -> int:
    if isinstance(territory, dict):
        try:
            return max(1, int(territory.get("development_max_level", default)))
        except (TypeError, ValueError):
            pass
    return max(1, int(default))


def has_growth_path(territory: Optional[Dict[str, Any]]) -> bool:
    return isinstance(territory, dict) and "growth_target_gdp_b" in territory


def development_progress(territory: Optional[Dict[str, Any]], level: Optional[int] = None) -> float:
    """0..1 progress through a custom slow-growth path."""
    if not has_growth_path(territory):
        return 0.0
    current = development_level(territory) if level is None else max(0, int(level))
    cap = development_cap(territory)
    try:
        power = max(1.0, float(territory.get("growth_curve_power", 1)))
    except (TypeError, ValueError):
        power = 1.0
    return min(1.0, current / cap) ** power


def development_multiplier(
    territory: Optional[Dict[str, Any]], per_level_pct: int = DEFAULT_DEVELOPMENT_GDP_PCT
) -> float:
    level = development_level(territory)
    return 1 + level * max(0, int(per_level_pct)) / 100


def base_gdp_b(country: Country, territory: Optional[Dict[str, Any]] = None) -> float:
    """Territory-specific campaign baseline, or the catalog GDP by default."""
    if isinstance(territory, dict):
        try:
            return max(1.0, float(territory.get("base_gdp_b", country.gdp_b)))
        except (TypeError, ValueError):
            pass
    return float(country.gdp_b)


def economic_value(
    country: Country, territory: Optional[Dict[str, Any]] = None, level: Optional[int] = None
) -> int:
    """Production/development basis; starter grants can normalize this fairly."""
    if isinstance(territory, dict):
        try:
            base = max(1, int(territory.get("economic_value", country.price)))
            if "economic_target_value" in territory:
                target = max(base, int(territory.get("economic_target_value", base)))
                return max(1, int(round(base + (target - base) * development_progress(territory, level))))
            return base
        except (TypeError, ValueError):
            pass
    return int(country.price)


def territory_tier(country: Country, territory: Optional[Dict[str, Any]] = None) -> str:
    if isinstance(territory, dict):
        if has_growth_path(territory):
            return _tier(max(1, int(effective_gdp_b(country, territory))))
        tier = str(territory.get("tier_override", ""))
        if tier in TIER_DEFENSE:
            return tier
    return country.tier


def effective_gdp_b(
    country: Country,
    territory: Optional[Dict[str, Any]] = None,
    per_level_pct: int = DEFAULT_DEVELOPMENT_GDP_PCT,
) -> float:
    base = base_gdp_b(country, territory)
    if has_growth_path(territory):
        try:
            target = max(base, float(territory.get("growth_target_gdp_b", base)))
        except (TypeError, ValueError):
            target = base
        value = base + (target - base) * development_progress(territory)
    else:
        value = base * development_multiplier(territory, per_level_pct)
    if value < 10:
        rendered = round(value, 2)
        return int(rendered) if rendered.is_integer() else rendered
    if value < 100:
        rendered = round(value, 1)
        return int(rendered) if rendered.is_integer() else rendered
    return int(round(value))


def development_cost(
    country: Country,
    current_level: int,
    levels: int = 1,
    *,
    base_cost_pct: int = DEFAULT_DEVELOPMENT_BASE_COST_PCT,
    growth_pct: int = DEFAULT_DEVELOPMENT_COST_GROWTH_PCT,
    territory: Optional[Dict[str, Any]] = None,
) -> int:
    """Price one or more levels. Every later level costs more than the last."""
    start = max(0, int(current_level))
    count = max(0, int(levels))
    if isinstance(territory, dict) and "development_cost_growth_pct" in territory:
        try:
            growth_pct = int(territory["development_cost_growth_pct"])
        except (TypeError, ValueError):
            pass
    growth = max(0, int(growth_pct)) / 100
    return sum(
        (
            max(
                1,
                int(
                    economic_value(country, territory, start + offset)
                    * max(1, int(base_cost_pct))
                    / 100
                    * (1 + (start + offset) * growth)
                ),
            )
            for offset in range(count)
        )
    )


def gross_per_day(
    country: Country,
    index: int,
    territory: Optional[Dict[str, Any]] = None,
    per_level_pct: int = DEFAULT_DEVELOPMENT_GDP_PCT,
) -> int:
    growth = 1.0 if has_growth_path(territory) else development_multiplier(territory, per_level_pct)
    return max(1, int(economic_value(country, territory) / 80 * production_multiplier(index) * growth))


def upkeep_per_day(
    country: Country, diplomat_pct: int = 0, territory: Optional[Dict[str, Any]] = None
) -> int:
    if isinstance(territory, dict) and territory.get("fair_start_upkeep"):
        diplomat_pct = 0
    base = economic_value(country, territory) / 1000
    return max(1, int(base * (1 - max(0, min(50, diplomat_pct)) / 100)))


def append_history(doc: Dict[str, Any], text: str) -> None:
    history = state(doc)["history"]
    history.append({"at": now().isoformat(), "text": str(text)[:500]})
    del history[:-100]
