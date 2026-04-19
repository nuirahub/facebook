"""
Zestawienie kampanii reklamowych Meta (Facebook Ads) — koszty, zasięg, gdzie
wyświetlano reklamy (placement), podstawowe metryki i krótkie wskazówki.

Wymaga Marketing API (nie wystarczy sam token do strony WWW):
  • Aplikacja w Meta for Developers z produktem „Marketing API”
  • Token użytkownika z uprawnieniem ads_read (często też ads_management
    przy generowaniu tokenu w narzędziach Meta)
  • Dostęp do konta reklamowego (rola co najmniej do podglądu/analityki)

Zmienne środowiskowe (np. plik .env obok skryptu):
  FACEBOOK_ACCESS_TOKEN   — token z ads_read
  FACEBOOK_AD_ACCOUNT_ID  — ID konta: „act_1234567890” lub sam numer

Opcjonalnie:
  FACEBOOK_API_VERSION    — domyślnie v21.0

Przykłady:
  python analuze_costs.py
  python analuze_costs.py --date-preset last_30d
  python analuze_costs.py --from-date 2025-03-01 --to-date 2025-04-01
  python analuze_costs.py --json
  python analuze_costs.py --csv raport.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_VERSION = os.environ.get("FACEBOOK_API_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{DEFAULT_VERSION}"

INSIGHT_FIELDS = (
    "campaign_id,campaign_name,adset_name,ad_name,"
    "spend,impressions,clicks,inline_link_clicks,reach,frequency,"
    "cpc,cpm,ctr,cpp,cost_per_inline_link_click,"
    "actions,action_values,cost_per_action_type"
)


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _normalize_ad_account(raw: str) -> str:
    s = raw.strip()
    if not s.startswith("act_"):
        s = "act_" + s.lstrip("act_")
    return s


def _require_token() -> str:
    t = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    if not t:
        raise SystemExit(
            "Brak FACEBOOK_ACCESS_TOKEN. Ustaw zmienną lub wpisz w pliku .env "
            "(token musi mieć zakres ads_read dla konta reklamowego)."
        )
    return t


def _require_ad_account() -> str:
    aid = os.environ.get("FACEBOOK_AD_ACCOUNT_ID", "").strip()
    if not aid:
        raise SystemExit(
            "Brak FACEBOOK_AD_ACCOUNT_ID. Ustaw np. act_1234567890 (ID z "
            "Menedżera reklam → Ustawienia konta reklamowego)."
        )
    return _normalize_ad_account(aid)


def graph_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token = _require_token()
    merged = {**params, "access_token": token}
    query = urlencode(merged, doseq=True)
    url = f"{GRAPH_BASE}{path}?{query}"
    req = Request(url, headers={"User-Agent": "Instanta-analuze_costs/1.0"})
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def graph_get_paged(path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Pobiera wszystkie strony wyników (paginacja data + paging.next)."""
    data = graph_get(path, params)
    for row in data.get("data") or []:
        yield row
    next_url = (data.get("paging") or {}).get("next")
    while next_url:
        req = Request(next_url, headers={"User-Agent": "Instanta-analuze_costs/1.0"})
        with urlopen(req, timeout=120) as resp:
            chunk = json.loads(resp.read().decode("utf-8"))
        for row in chunk.get("data") or []:
            yield row
        next_url = (chunk.get("paging") or {}).get("next")


def _f(x: Any) -> float:
    try:
        if x is None:
            return 0.0
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    out = {
        "spend": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "inline_link_clicks": 0.0,
        "reach": 0.0,
    }
    for r in rows:
        out["spend"] += _f(r.get("spend"))
        out["impressions"] += _f(r.get("impressions"))
        out["clicks"] += _f(r.get("clicks"))
        out["inline_link_clicks"] += _f(r.get("inline_link_clicks"))
        out["reach"] += _f(r.get("reach"))
    return out


def fetch_campaign_insights(
    ad_account: str,
    date_preset: str | None,
    time_from: str | None,
    time_to: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "level": "campaign",
        "fields": INSIGHT_FIELDS,
        "limit": 500,
    }
    if date_preset:
        params["date_preset"] = date_preset
    else:
        params["time_range"] = json.dumps({"since": time_from, "until": time_to})
    return list(graph_get_paged(f"/{ad_account}/insights", params))


def fetch_placement_breakdown(
    ad_account: str,
    date_preset: str | None,
    time_from: str | None,
    time_to: str | None,
) -> list[dict[str, Any]]:
    """Platforma + pozycja (np. Feed vs Stories) — gdzie najwięcej wyświetleń."""
    params: dict[str, Any] = {
        "level": "campaign",
        "breakdowns": "publisher_platform,platform_position",
        "fields": INSIGHT_FIELDS,
        "limit": 500,
    }
    if date_preset:
        params["date_preset"] = date_preset
    else:
        params["time_range"] = json.dumps({"since": time_from, "until": time_to})
    return list(graph_get_paged(f"/{ad_account}/insights", params))


def fetch_age_gender_breakdown(
    ad_account: str,
    date_preset: str | None,
    time_from: str | None,
    time_to: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "level": "campaign",
        "breakdowns": "age,gender",
        "fields": INSIGHT_FIELDS,
        "limit": 500,
    }
    if date_preset:
        params["date_preset"] = date_preset
    else:
        params["time_range"] = json.dumps({"since": time_from, "until": time_to})
    try:
        return list(graph_get_paged(f"/{ad_account}/insights", params))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if "age" in body.lower() or "breakdown" in body.lower():
            return []
        raise


def fetch_region_breakdown(
    ad_account: str,
    date_preset: str | None,
    time_from: str | None,
    time_to: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "level": "campaign",
        "breakdowns": "region",
        "fields": INSIGHT_FIELDS,
        "limit": 500,
    }
    if date_preset:
        params["date_preset"] = date_preset
    else:
        params["time_range"] = json.dumps({"since": time_from, "until": time_to})
    try:
        return list(graph_get_paged(f"/{ad_account}/insights", params))
    except HTTPError:
        return []


def build_report(
    campaigns: list[dict[str, Any]],
    placements: list[dict[str, Any]],
    age_gender: list[dict[str, Any]],
    regions: list[dict[str, Any]],
) -> dict[str, Any]:
    total = _summarize_rows(campaigns)
    # CTR średni ważony
    imp, clk = total["impressions"], total["clicks"]
    total["ctr_pct"] = (clk / imp * 100.0) if imp else 0.0
    total["cpc"] = (total["spend"] / clk) if clk else 0.0
    total["cpm"] = (total["spend"] / imp * 1000.0) if imp else 0.0

    # Top kampanie po koszcie
    by_camp: dict[str, dict[str, Any]] = {}
    for r in campaigns:
        cid = str(r.get("campaign_id", ""))
        if cid not in by_camp:
            by_camp[cid] = {
                "campaign_id": cid,
                "campaign_name": r.get("campaign_name", ""),
                "rows": [],
            }
        by_camp[cid]["rows"].append(r)
    camp_list = []
    for cid, block in by_camp.items():
        s = _summarize_rows(block["rows"])
        camp_list.append(
            {
                "campaign_id": cid,
                "campaign_name": block["rows"][0].get("campaign_name", ""),
                **s,
                "ctr_pct": (s["clicks"] / s["impressions"] * 100.0)
                if s["impressions"]
                else 0.0,
                "cpc": (s["spend"] / s["clicks"]) if s["clicks"] else 0.0,
            }
        )
    camp_list.sort(key=lambda x: x["spend"], reverse=True)

    # Placement — sumuj po (platform, position)
    place_map: dict[tuple[str, str], list[dict]] = {}
    for r in placements:
        key = (
            str(r.get("publisher_platform", "?")),
            str(r.get("platform_position", "?")),
        )
        place_map.setdefault(key, []).append(r)
    placement_summary = []
    for (pub, pos), rows in place_map.items():
        s = _summarize_rows(rows)
        placement_summary.append(
            {
                "publisher_platform": pub,
                "platform_position": pos,
                **s,
                "ctr_pct": (s["clicks"] / s["impressions"] * 100.0)
                if s["impressions"]
                else 0.0,
                "cpc": (s["spend"] / s["clicks"]) if s["clicks"] else 0.0,
            }
        )
    placement_summary.sort(key=lambda x: x["impressions"], reverse=True)

    # Wiek/płeć
    ag_list = []
    for r in age_gender:
        s = _summarize_rows([r])
        ag_list.append(
            {
                "age": r.get("age"),
                "gender": r.get("gender"),
                **s,
                "ctr_pct": (s["clicks"] / s["impressions"] * 100.0)
                if s["impressions"]
                else 0.0,
            }
        )
    ag_list.sort(key=lambda x: x["impressions"], reverse=True)

    reg_list = []
    for r in regions:
        s = _summarize_rows([r])
        reg_list.append(
            {
                "region": r.get("region"),
                **s,
                "ctr_pct": (s["clicks"] / s["impressions"] * 100.0)
                if s["impressions"]
                else 0.0,
            }
        )
    reg_list.sort(key=lambda x: x["impressions"], reverse=True)

    hints: list[str] = []
    if total["spend"] > 0 and placement_summary:
        top_p = placement_summary[0]
        hints.append(
            f"Najwięcej wyświetleń: {top_p['publisher_platform']} / {top_p['platform_position']} "
            f"({int(top_p['impressions']):,} wyśw., {top_p['ctr_pct']:.3f}% CTR)."
        )
        worst = max(placement_summary, key=lambda x: x["cpc"] if x["clicks"] else 0)
        best = min(
            [p for p in placement_summary if p["clicks"] > 0],
            key=lambda x: x["cpc"],
            default=None,
        )
        if best and worst["cpc"] > 0 and best["cpc"] > 0 and worst != best:
            hints.append(
                f"Najtańszy klik (CPC): {best['publisher_platform']}/{best['platform_position']} "
                f"({best['cpc']:.2f} vs najdroższy {worst['cpc']:.2f}). Rozważ przesunięcie budżetu."
            )
    if total["impressions"] > 0 and total["reach"] > 0:
        freq = total["impressions"] / total["reach"]
        if freq > 4:
            hints.append(
                f"Średnia częstotliwość ~{freq:.1f} — możliwe zmęczenie kreacji; testuj nowe materiały lub zawęź grupę."
            )
    if total["ctr_pct"] < 0.5 and total["impressions"] > 1000:
        hints.append(
            "CTR poniżej ~0,5% przy dużej liczbie wyświetleń — sprawdź nagłówek, CTA i dopasowanie odbiorców."
        )

    return {
        "total": total,
        "campaigns": camp_list,
        "placements": placement_summary,
        "age_gender": ag_list[:30],
        "regions": reg_list[:30],
        "hints": hints,
    }


def print_report(rep: dict[str, Any]) -> None:
    t = rep["total"]
    print("=== Podsumowanie konta (wybrany okres) ===")
    print(f"  Koszt (spend):     {t['spend']:.2f}")
    print(f"  Wyświetlenia:      {int(t['impressions']):,}")
    print(f"  Kliknięcia:        {int(t['clicks']):,}")
    print(f"  Kliknięcia w link: {int(t['inline_link_clicks']):,}")
    print(f"  Zasięg (reach):   {int(t['reach']):,}")
    print(f"  CTR:               {t['ctr_pct']:.3f}%")
    print(f"  CPC (śr.):         {t['cpc']:.2f}")
    print(f"  CPM (śr.):         {t['cpm']:.2f}")
    print()

    print("=== Kampanie (wg kosztu) ===")
    for c in rep["campaigns"][:25]:
        print(
            f"  • {c.get('campaign_name', '?')[:70]}\n"
            f"    spend {c['spend']:.2f} | wyśw. {int(c['impressions']):,} | "
            f"CTR {c['ctr_pct']:.3f}% | CPC {c['cpc']:.2f}"
        )
    if len(rep["campaigns"]) > 25:
        print(f"  … (+{len(rep['campaigns']) - 25} kampanii)")
    print()

    print("=== Gdzie wyświetlano (platforma / pozycja, wg wyświetleń) ===")
    for p in rep["placements"][:20]:
        print(
            f"  • {p['publisher_platform']} / {p['platform_position']}\n"
            f"    wyśw. {int(p['impressions']):,} | kliknięcia {int(p['clicks']):,} | "
            f"CTR {p['ctr_pct']:.3f}% | spend {p['spend']:.2f}"
        )
    print()

    if rep["age_gender"]:
        print("=== Wiek i płeć (wg wyświetleń, top) ===")
        for row in rep["age_gender"][:15]:
            print(
                f"  • {row.get('age')} / {row.get('gender')}: "
                f"{int(row['impressions']):,} wyśw., CTR {row['ctr_pct']:.3f}%"
            )
        print()

    if rep["regions"]:
        print("=== Region (top) ===")
        for row in rep["regions"][:15]:
            print(
                f"  • {row.get('region')}: {int(row['impressions']):,} wyśw., "
                f"spend {row['spend']:.2f}"
            )
        print()

    if rep["hints"]:
        print("=== Wskazówki (heurystyki) ===")
        for h in rep["hints"]:
            print(f"  • {h}")
    else:
        print("=== Wskazówki ===\n  (brak automatycznych — za mało danych)")


def write_csv(path: Path, rep: dict[str, Any]) -> None:
    rows_out: list[dict[str, Any]] = []
    for p in rep["placements"]:
        rows_out.append(
            {
                "typ": "placement",
                "publisher_platform": p["publisher_platform"],
                "platform_position": p["platform_position"],
                "spend": p["spend"],
                "impressions": p["impressions"],
                "clicks": p["clicks"],
                "ctr_pct": round(p["ctr_pct"], 4),
                "cpc": round(p["cpc"], 4),
            }
        )
    for c in rep["campaigns"]:
        rows_out.append(
            {
                "typ": "campaign",
                "campaign_name": c.get("campaign_name"),
                "campaign_id": c.get("campaign_id"),
                "spend": c["spend"],
                "impressions": c["impressions"],
                "clicks": c["clicks"],
                "ctr_pct": round(c["ctr_pct"], 4),
                "cpc": round(c["cpc"], 4),
            }
        )
    if not rows_out:
        return
    fieldnames = list(rows_out[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_out)


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    _load_dotenv()
    ap = argparse.ArgumentParser(
        description="Zestawienie kosztów i miejsc emisji kampanii Meta Ads"
    )
    ap.add_argument(
        "--date-preset",
        default="last_30d",
        help="np. last_7d, last_30d, last_90d, this_month, last_month, lifetime",
    )
    ap.add_argument(
        "--from-date", help="YYYY-MM-DD (z --to-date, zamiast --date-preset)"
    )
    ap.add_argument("--to-date", help="YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", help="Pełny raport JSON")
    ap.add_argument("--csv", type=Path, help="Zapis placement + kampanie do CSV")
    args = ap.parse_args()

    use_preset = not (args.from_date and args.to_date)
    if not use_preset and (not args.from_date or not args.to_date):
        print(
            "Podaj oba: --from-date i --to-date albo użyj --date-preset.",
            file=sys.stderr,
        )
        return 1

    ad_account = _require_ad_account()

    try:
        campaigns = fetch_campaign_insights(
            ad_account,
            args.date_preset if use_preset else None,
            args.from_date if not use_preset else None,
            args.to_date if not use_preset else None,
        )
        placements = fetch_placement_breakdown(
            ad_account,
            args.date_preset if use_preset else None,
            args.from_date if not use_preset else None,
            args.to_date if not use_preset else None,
        )
        age_gender = fetch_age_gender_breakdown(
            ad_account,
            args.date_preset if use_preset else None,
            args.from_date if not use_preset else None,
            args.to_date if not use_preset else None,
        )
        regions = fetch_region_breakdown(
            ad_account,
            args.date_preset if use_preset else None,
            args.from_date if not use_preset else None,
            args.to_date if not use_preset else None,
        )
    except HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"Błąd API ({e.code}): {err}", file=sys.stderr)
        return 1
    except URLError as e:
        print(f"Błąd sieci: {e.reason}", file=sys.stderr)
        return 1

    rep = build_report(campaigns, placements, age_gender, regions)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)

    if args.csv:
        write_csv(args.csv, rep)
        print(f"\nZapisano CSV: {args.csv}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
