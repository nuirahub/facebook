"""
Łączy się z Facebookiem (Graph API) i pobiera po 3 ostatnie wpisy z kont
wymienionych w source.md (kolumna z adresem facebook.com).

Wymagane zmienne środowiskowe:
  FACEBOOK_ACCESS_TOKEN — token z uprawnieniami do odczytu treści stron,
    np. pages_read_engagement (token użytkownika-strony lub Page Access Token).

Opcjonalnie:
  FACEBOOK_API_VERSION — domyślnie v21.0

Uwaga: Meta ogranicza dostęp do /feed i /posts — token musi mieć dostęp
do danej strony (np. administrator strony). Sam App Token zwykle nie wystarczy.

Uruchomienie:
  set FACEBOOK_ACCESS_TOKEN=twój_token
  python analize_url.py
  python analize_url.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DEFAULT_VERSION = os.environ.get("FACEBOOK_API_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.facebook.com/{DEFAULT_VERSION}"
SOURCE_NAME = "source.md"


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


def parse_facebook_accounts(md_text: str) -> list[tuple[str, str]]:
    """Zwraca listę (nazwa, url) z tabeli markdown; pomija wiersze bez URL facebook."""
    rows: list[tuple[str, str]] = []
    for line in md_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|\s*[-:]+\s*\|", line):
            continue
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue
        name, url = cells[0], cells[1]
        if name.lower() in ("podmiot", "--------"):
            continue
        if not url.startswith("http") or "facebook.com" not in url.lower():
            continue
        rows.append((name, url))
    return rows


def facebook_page_identifier(url: str) -> str:
    p = urlparse(url.strip())
    path = p.path.strip("/").split("/")
    if not path:
        raise ValueError(f"Nie można wyciągnąć identyfikatora strony z URL: {url}")
    head = path[0].lower()
    if head == "profile.php":
        ids = parse_qs(p.query).get("id")
        if not ids:
            raise ValueError(f"Brak id w profile.php: {url}")
        return ids[0]
    return path[0]


def graph_get(path: str, params: dict[str, str]) -> dict[str, Any]:
    token = os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Brak FACEBOOK_ACCESS_TOKEN. Ustaw zmienną środowiskową lub plik .env"
        )
    query = urlencode({**params, "access_token": token})
    url = f"{GRAPH_BASE}{path}?{query}"
    req = Request(url, headers={"User-Agent": "Instanta-analize_url/1.0"})
    with urlopen(req, timeout=60) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)


def fetch_page_posts(page_id_or_username: str, limit: int = 3) -> list[dict[str, Any]]:
    """
    Pobiera ostatnie wpisy ze strony. Używa pola 'posts' na obiekcie strony
    (z limitowanym zagnieżdżeniem).
    """
    fields = (
        f"posts.limit({limit})"
        "{{message,story,created_time,permalink_url,status_type,is_published}}"
    )
    raw = graph_get(f"/{page_id_or_username}", {"fields": fields})
    posts_obj = raw.get("posts") or {}
    data = posts_obj.get("data") or []
    return data[:limit]


def run(source_path: Path, limit: int) -> list[dict[str, Any]]:
    if not os.environ.get("FACEBOOK_ACCESS_TOKEN", "").strip():
        raise RuntimeError(
            "Brak FACEBOOK_ACCESS_TOKEN. Ustaw zmienną środowiskową lub wpisz token w plik .env obok skryptu."
        )
    md = source_path.read_text(encoding="utf-8")
    accounts = parse_facebook_accounts(md)
    if not accounts:
        raise RuntimeError(f"Brak kont z URL facebook.com w {source_path}")

    results: list[dict[str, Any]] = []
    for name, url in accounts:
        try:
            ident = facebook_page_identifier(url)
        except ValueError as e:
            results.append({"name": name, "url": url, "error": str(e), "posts": []})
            continue
        try:
            posts = fetch_page_posts(ident, limit=limit)
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            results.append(
                {
                    "name": name,
                    "url": url,
                    "page_identifier": ident,
                    "error": f"HTTP {e.code}: {err_body}",
                    "posts": [],
                }
            )
        except URLError as e:
            results.append(
                {
                    "name": name,
                    "url": url,
                    "page_identifier": ident,
                    "error": str(e.reason),
                    "posts": [],
                }
            )
        except RuntimeError as e:
            results.append(
                {
                    "name": name,
                    "url": url,
                    "page_identifier": ident,
                    "error": str(e),
                    "posts": [],
                }
            )
        else:
            normalized = []
            for p in posts:
                text = (p.get("message") or p.get("story") or "").strip()
                normalized.append(
                    {
                        "created_time": p.get("created_time"),
                        "permalink_url": p.get("permalink_url"),
                        "text": text,
                    }
                )
            results.append(
                {
                    "name": name,
                    "url": url,
                    "page_identifier": ident,
                    "posts": normalized,
                }
            )
    return results


def print_human(results: list[dict[str, Any]]) -> None:
    for block in results:
        print("=" * 60)
        print(block["name"])
        print(block["url"])
        if block.get("error"):
            print(f"  [błąd] {block['error']}")
            continue
        posts = block.get("posts") or []
        if not posts:
            print("  (brak wpisów lub pusta lista)")
            continue
        for i, post in enumerate(posts, 1):
            print(f"  --- Wpis {i} ---")
            print(f"  Data: {post.get('created_time', '—')}")
            if post.get("permalink_url"):
                print(f"  Link: {post['permalink_url']}")
            body = post.get("text") or "(tylko multimedia / brak tekstu)"
            print(f"  {body[:2000]}{'…' if len(body) > 2000 else ''}")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    _load_dotenv()
    ap = argparse.ArgumentParser(
        description="Pobierz ostatnie wpisy z kont z source.md"
    )
    ap.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent / SOURCE_NAME,
        help="Ścieżka do source.md",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Liczba ostatnich wpisów na konto (domyślnie 3)",
    )
    ap.add_argument("--json", action="store_true", help="Wynik jako JSON na stdout")
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"Brak pliku: {args.source}", file=sys.stderr)
        return 1

    try:
        results = run(args.source, limit=args.limit)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
