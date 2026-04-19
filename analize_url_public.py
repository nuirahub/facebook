"""
Pobiera kilka ostatnich wpisów ze stron Facebook z listy w source.md — BEZ Graph API.

Metoda: Playwright uruchamia Chromium i ładuje publiczną stronę tak jak przeglądarka,
a następnie wyciąga treść z węzłów kanału (role=article). Nie musisz być administratorem
danej strony; wystarczy, że treść jest widoczna bez logowania (Facebook często i tak
pokazuje część wpisów gościom).

Ograniczenia (ważne):
  • Regulamin Meta ogranicza automatyczne zbieranie danych — używasz na własną
    odpowiedzialność i w granicach prawa.
  • Facebook często zmienia HTML, może wymagać logowania, captchy lub blokować headless —
    skrypt może nagle przestać działać.
  • Jeśli zobaczysz pusty wynik lub ekran logowania, ustaw opcję --storage-state
    (zapis sesji po jednorazowym zalogowaniu — patrz niżej).

Graph API: odczyt wpisów stron w praktyce wymaga aplikacji Meta i tokenów powiązanych
z uprawnieniami do danych strony (często rola na stronie / przegląd aplikacji) — stąd
Twoje ograniczenia bez admina.

Instalacja:
  pip install playwright
  playwright install chromium

Opcjonalna sesja (gdy Facebook blokuje gościa):
  1) Uruchom: playwright codegen https://www.facebook.com --save-storage=fb_state.json
     Zaloguj się ręcznie, zamknij okno (plik się zapisze).
  2) Potem: python analize_url_public.py --storage-state fb_state.json

Uruchomienie:
  python analize_url_public.py
  python analize_url_public.py --json --limit 3
  python analize_url_public.py --headed   # podgląd okna (debug)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# Parser kont — współdzielony z wersją Graph API
from analize_url_admin import facebook_page_identifier, parse_facebook_accounts

SOURCE_NAME = "source.md"

# Realistyczny UA zmniejsza część blokad headless
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    import os

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _normalize_fb_url(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("/"):
        return "https://www.facebook.com" + href.split("?")[0].split("#")[0]
    if "facebook.com" in href:
        return href.split("?")[0].split("#")[0]
    return None


def _strip_ui_noise(text: str) -> str:
    lines: list[str] = []
    skip_re = re.compile(
        r"^(Like|Comment|Share|Send|·|\d+\s*(h|min|d|w|g|s)\b|See more|Zobacz więcej)$",
        re.I,
    )
    for ln in text.splitlines():
        t = ln.strip()
        if not t or skip_re.match(t):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _extract_post_text(article_locator) -> str:
    """Próbuje kilku selektorów Meta (zmienne między wersjami UI)."""
    for sel in (
        '[data-ad-preview="message"]',
        '[data-ad-comet-preview="message"]',
        'div[data-testid="post_message"]',
        '[dir="auto"] span',
    ):
        loc = article_locator.locator(sel).first
        try:
            if loc.count() and loc.is_visible(timeout=500):
                txt = loc.inner_text(timeout=2000)
                if txt and len(txt.strip()) > 2:
                    return _strip_ui_noise(txt)
        except Exception:
            continue
    try:
        raw = article_locator.inner_text(timeout=3000)
        return _strip_ui_noise(raw) if raw else ""
    except Exception:
        return ""


def _first_post_link(article_locator) -> str | None:
    for pattern in (r'href="([^"]+/posts/[^"]+)"', r'href="([^"]+permalink\.php[^"]*)"'):
        try:
            html = article_locator.evaluate("el => el.outerHTML")  # type: ignore[attr-defined]
        except Exception:
            html = ""
        m = re.search(pattern, html or "")
        if m:
            return _normalize_fb_url(m.group(1).replace("&amp;", "&"))
    try:
        link = article_locator.locator('a[href*="/posts/"]').first
        if link.count():
            return _normalize_fb_url(link.get_attribute("href"))
    except Exception:
        pass
    return None


def fetch_posts_playwright(
    page_username: str,
    limit: int,
    *,
    headless: bool,
    storage_state: Path | None,
    slow_mo_ms: int,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """
    Zwraca: (lista wpisów, error, warning).
    error — sytuacja krytyczna (brak Playwright, timeout ładowania strony).
    warning — miękka uwaga (np. możliwy login, zmieniony UI).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            [],
            "Brak pakietu playwright. Zainstaluj: pip install playwright && playwright install chromium",
            None,
        )

    posts: list[dict[str, Any]] = []
    warning: str | None = None
    url = f"https://www.facebook.com/{page_username}/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        ctx_kw: dict[str, Any] = {
            "user_agent": _CHROME_UA,
            "locale": "pl-PL",
            "viewport": {"width": 1365, "height": 900},
        }
        if storage_state and storage_state.is_file():
            ctx_kw["storage_state"] = str(storage_state)
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            browser.close()
            return [], f"Nie udało się załadować strony: {e}", None

        time.sleep(4.0)

        # typowe banery cookies / zgody
        for label in (
            "Accept all",
            "Allow all cookies",
            "Akceptuj wszystkie",
            "Zezwól na wszystkie pliki cookie",
            "Tylko niezbędne",
        ):
            try:
                page.get_by_role("button", name=re.compile(re.escape(label), re.I)).click(
                    timeout=1500
                )
                time.sleep(0.5)
                break
            except Exception:
                continue

        html_lower = ""
        try:
            html_lower = (page.content() or "").lower()
        except Exception:
            pass
        if "log in to facebook" in html_lower or 'id="email"' in html_lower:
            if not storage_state:
                warning = (
                    "Facebook może wymagać logowania. Spróbuj --storage-state "
                    "(zapis sesji z playwright codegen) albo --headed i ręcznie zaakceptuj cookies."
                )

        # Główny selektor kanału strony
        articles = page.locator('[role="article"]').all()
        picked: list = []
        for art in articles:
            try:
                if not art.is_visible():
                    continue
            except Exception:
                continue
            txt = _extract_post_text(art)
            link = _first_post_link(art)
            if not txt and not link:
                continue
            picked.append((txt, link))
            if len(picked) >= limit:
                break

        if not picked and articles:
            warning = (warning + " " if warning else "") + (
                "Znaleziono role=article, ale bez treści/linku — UI się zmienił albo treść tylko w grafice."
            )

        for text, link in picked[:limit]:
            posts.append(
                {
                    "text": text or "(brak tekstu — np. sam film/obraz)",
                    "permalink_url": link,
                }
            )

        browser.close()

    if not posts and warning is None:
        warning = (
            "Nie znaleziono wpisów w znanym formacie — być może wymagane logowanie lub zmiana układu strony."
        )

    return posts, None, warning


def run(
    source_path: Path,
    limit: int,
    *,
    headless: bool,
    storage_state: Path | None,
    slow_mo_ms: int,
) -> list[dict[str, Any]]:
    md = source_path.read_text(encoding="utf-8")
    accounts = parse_facebook_accounts(md)
    if not accounts:
        raise RuntimeError(f"Brak kont z URL facebook.com w {source_path}")

    results: list[dict[str, Any]] = []
    for name, url in accounts:
        try:
            ident = facebook_page_identifier(url)
        except ValueError as e:
            results.append({"name": name, "url": url, "error": str(e), "posts": [], "warning": None})
            continue
        posts, err, warn = fetch_posts_playwright(
            ident,
            limit,
            headless=headless,
            storage_state=storage_state,
            slow_mo_ms=slow_mo_ms,
        )
        results.append(
            {
                "name": name,
                "url": url,
                "page_identifier": ident,
                "posts": posts,
                "warning": warn,
                "error": err,
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
        if block.get("warning"):
            print(f"  [uwaga] {block['warning']}")
        posts = block.get("posts") or []
        if not posts:
            print("  (brak wpisów)")
            continue
        for i, post in enumerate(posts, 1):
            print(f"  --- Wpis {i} ---")
            if post.get("permalink_url"):
                print(f"  Link: {post['permalink_url']}")
            body = post.get("text") or ""
            print(f"  {body[:2500]}{'…' if len(body) > 2500 else ''}")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    _load_dotenv()
    ap = argparse.ArgumentParser(
        description="Pobierz ostatnie wpisy z publicznych stron FB (bez Graph API, przez Playwright)"
    )
    ap.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent / SOURCE_NAME,
        help="Ścieżka do source.md",
    )
    ap.add_argument("--limit", type=int, default=3, help="Liczba wpisów na konto")
    ap.add_argument("--json", action="store_true", help="JSON na stdout")
    ap.add_argument(
        "--headed",
        action="store_true",
        help="Pokaż okno przeglądarki (łatwiej ominąć część blokad / debug)",
    )
    ap.add_argument(
        "--storage-state",
        type=Path,
        default=None,
        help="Plik JSON ze stanem sesji Playwright (np. po codegen --save-storage)",
    )
    ap.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help="Opóźnienie akcji w ms (np. 100 przy debugowaniu)",
    )
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"Brak pliku: {args.source}", file=sys.stderr)
        return 1

    try:
        results = run(
            args.source,
            args.limit,
            headless=not args.headed,
            storage_state=args.storage_state,
            slow_mo_ms=args.slow_mo,
        )
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
