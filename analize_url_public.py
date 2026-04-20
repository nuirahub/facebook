"""
Pobiera kilka ostatnich wpisów ze stron Facebook z listy w source.json — BEZ Graph API.

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
  Wynik czytelny: scrap_facebook_YYYYMMDD.md (k wpisów na konto, domyślnie k=3) oraz podsumowanie na stdout.
  python analize_url_public.py --json --limit 3
  python analize_url_public.py --headed   # podgląd okna (debug)
  python analize_url_public.py --feed-scroll-rounds 30   # więcej przewinięć = więcej doładowanych wpisów
  Postęp (log) idzie na stderr; na stdout tylko krótkie podsumowanie po zakończeniu (pełna treść w scrap_facebook_YYYYMMDD.md).
  python analize_url_public.py --quiet   # bez komunikatów postępu na stderr
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from analize_url_admin import facebook_page_identifier

SOURCE_NAME = "source.json"


def scrap_report_filename() -> str:
    """Nazwa pliku wyniku: scrap_facebook_YYYYMMDD.md (data uruchomienia)."""
    return f"scrap_facebook_{datetime.now().strftime('%Y%m%d')}.md"

# Feed ładuje się leniwie — bez przewijania często widać tylko 1 wpis.
_FEED_SCROLL_PX = 950
_FEED_SCROLL_PAUSE_S = 1.25
_DEFAULT_FEED_SCROLL_ROUNDS = 22

# Realistyczny UA zmniejsza część blokad headless
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _log(msg: str, *, quiet: bool) -> None:
    """Postęp na stderr — nie miesza się z --json na stdout."""
    if quiet:
        return
    print(msg, file=sys.stderr, flush=True)


def _normalize_fb_url(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("/"):
        return "https://www.facebook.com" + href.split("?")[0].split("#")[0]
    if "facebook.com" in href:
        return href.split("?")[0].split("#")[0]
    return None


def _dedup_key(link: str | None, text: str) -> str:
    """Klucz do odrzucania duplikatów (ten sam post w głównym bloku i w komentarzu)."""
    if link:
        return link
    h = hashlib.sha256(text.strip().encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"notext:{h}"


def _is_top_level_article(article_locator) -> bool:
    """True, jeśli węzeł nie jest zagnieżdżonym role=article (np. wątek komentarza)."""
    try:
        return bool(
            article_locator.evaluate(
                """el => {
                    let p = el.parentElement;
                    while (p) {
                        if (p.getAttribute && p.getAttribute("role") === "article") return false;
                        p = p.parentElement;
                    }
                    return true;
                }"""
            )
        )
    except Exception:
        return False


def _try_dismiss_login_modal(page, *, quiet: bool, progress_prefix: str) -> None:
    """
    Po pojawieniu się okna logowania / nakładki próbuje kliknąć X (Close / Zamknij)
    albo wysłać Escape. UI Meta bywa zmienne — kilka strategii.
    """
    pf = progress_prefix
    time.sleep(2.5)

    def _click_if_visible(locator, desc: str) -> bool:
        try:
            if locator.count() and locator.first.is_visible(timeout=800):
                locator.first.click(timeout=3000)
                time.sleep(0.7)
                _log(f"{pf}Zamknięto nakładkę: {desc}.", quiet=quiet)
                return True
        except Exception:
            pass
        return False

    dialog = page.locator('[role="dialog"], [role="alertdialog"]')
    for sel, desc in (
        ('[aria-label="Close" i]', "Close (aria)"),
        ('[aria-label="Zamknij" i]', "Zamknij (aria)"),
        ('[aria-label="Zamknij okno dialogowe" i]', "Zamknij okno dialogowe"),
        ('button[aria-label="Close" i]', "przycisk Close"),
        ('[data-testid="close_button" i]', "data-testid close"),
        ('div[role="button"][aria-label="Close" i]', "div Close"),
    ):
        try:
            if _click_if_visible(dialog.locator(sel), desc):
                return
        except Exception:
            continue
        try:
            if _click_if_visible(page.locator(sel), f"{desc} (bez dialogu)"):
                return
        except Exception:
            continue

    for name_re in (
        re.compile(r"^(close|zamknij)$", re.I),
        re.compile(r"^(close dialog|zamknij)$", re.I),
    ):
        try:
            btn = page.get_by_role("button", name=name_re).first
            if btn.count() and btn.is_visible(timeout=800):
                btn.click(timeout=3000)
                time.sleep(0.7)
                _log(f"{pf}Zamknięto modal (przycisk tekstowy).", quiet=quiet)
                return
        except Exception:
            continue

    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        _log(f"{pf}Wysłano Escape (możliwe zamknięcie modala).", quiet=quiet)
    except Exception:
        pass


def load_facebook_accounts_json(source_path: Path) -> list[tuple[str, str]]:
    """
    Wczytuje listę kont: [{"name": "...", "url": "https://www.facebook.com/..."}, ...].
    Pomija wpisy bez url lub z adresem innym niż facebook.com.
    """
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Niepoprawny JSON w {source_path}: {e}") from e
    if not isinstance(raw, list):
        raise RuntimeError(f"{source_path}: oczekiwana lista JSON (tablica obiektów).")
    rows: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url_val = item.get("url")
        if url_val is None:
            continue
        url = str(url_val).strip()
        if not url or "facebook.com" not in url.lower():
            continue
        if not url.startswith("http"):
            continue
        if not name:
            name = url
        rows.append((name, url))
    return rows


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
    # Najpierw treść samego wpisu — szerokie [dir=auto] span łapie też komentarze pod postem.
    for sel in (
        '[data-ad-preview="message"]',
        '[data-ad-comet-preview="message"]',
        'div[data-testid="post_message"]',
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
        loc_span = article_locator.locator(
            ':scope [data-ad-preview="message"] ~ [dir="auto"] span, '
            ':scope [data-ad-comet-preview="message"] ~ [dir="auto"] span'
        ).first
        if loc_span.count():
            txt = loc_span.inner_text(timeout=2000)
            if txt and len(txt.strip()) > 2:
                return _strip_ui_noise(txt)
    except Exception:
        pass
    try:
        raw = article_locator.inner_text(timeout=3000)
        return _strip_ui_noise(raw) if raw else ""
    except Exception:
        return ""


def _first_post_link(article_locator) -> str | None:
    try:
        html = article_locator.evaluate("el => el.outerHTML")  # type: ignore[attr-defined]
    except Exception:
        html = ""
    html = html or ""
    for pattern in (
        r'href="([^"]+/posts/[^"]+)"',
        r'href="([^"]+permalink\.php[^"]*)"',
    ):
        m = re.search(pattern, html)
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
    feed_scroll_rounds: int,
    quiet: bool,
    progress_prefix: str = "",
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
    pf = progress_prefix

    _log(f"{pf}Uruchamianie Playwright (Chromium)…", quiet=quiet)
    with sync_playwright() as p:
        _log(f"{pf}Przeglądarka gotowa, otwieranie {url}", quiet=quiet)
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo_ms)
        try:
            ctx_kw: dict[str, Any] = {
                "user_agent": _CHROME_UA,
                "locale": "pl-PL",
                "viewport": {"width": 1365, "height": 900},
            }
            if storage_state and storage_state.is_file():
                ctx_kw["storage_state"] = str(storage_state)
            context = browser.new_context(**ctx_kw)
            page = context.new_page()
            nav_response = None
            try:
                nav_response = page.goto(
                    url, wait_until="domcontentloaded", timeout=90_000
                )
            except Exception as e:
                return [], f"Nie udało się załadować strony: {e}", None

            http_status = nav_response.status if nav_response else None
            _log(
                f"{pf}Status HTTP po wejściu na stronę: {http_status if http_status is not None else 'brak'}",
                quiet=quiet,
            )

            _log(f"{pf}Strona wczytana, krótka pauza na render (ok. 4 s)…", quiet=quiet)
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
                    page.get_by_role(
                        "button", name=re.compile(re.escape(label), re.I)
                    ).click(timeout=1500)
                    time.sleep(0.5)
                    break
                except Exception:
                    continue

            _try_dismiss_login_modal(page, quiet=quiet, progress_prefix=pf)

            html_lower = ""
            try:
                html_lower = (page.content() or "").lower()
            except Exception:
                pass
            login_wall = (
                "log in to facebook" in html_lower or 'id="email"' in html_lower
            )
            if login_wall:
                if not storage_state:
                    warning = (
                        "Facebook może wymagać logowania. Spróbuj --storage-state "
                        "(zapis sesji z playwright codegen) albo --headed i ręcznie zaakceptuj cookies."
                    )

            seen_keys: set[str] = set()
            picked: list[tuple[str, str | None]] = []
            any_articles = False
            max_articles_in_round = 0
            max_rounds = max(1, feed_scroll_rounds)
            _log(
                f"{pf}Zbieranie do {limit} unikalnych wpisów (do {max_rounds} przewinięć feedu)…",
                quiet=quiet,
            )

            for round_i in range(max_rounds):
                articles = page.locator('[role="article"]').all()
                if articles:
                    any_articles = True
                    max_articles_in_round = max(max_articles_in_round, len(articles))
                for art in articles:
                    if len(picked) >= limit:
                        break
                    if not _is_top_level_article(art):
                        continue
                    try:
                        if not art.is_visible():
                            continue
                    except Exception:
                        continue
                    txt = _extract_post_text(art)
                    link = _first_post_link(art)
                    if not txt and not link:
                        continue
                    key = _dedup_key(link, txt)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    picked.append((txt, link))
                if len(picked) >= limit:
                    break
                if round_i > 0 and round_i % 5 == 0:
                    _log(
                        f"{pf}  … przewinięcie {round_i}/{max_rounds}, "
                        f"zebrano {len(picked)}/{limit} wpisów",
                        quiet=quiet,
                    )
                try:
                    page.evaluate(f"window.scrollBy(0, {_FEED_SCROLL_PX})")
                except Exception:
                    pass
                time.sleep(_FEED_SCROLL_PAUSE_S)

            articles_after_scroll = 0
            try:
                articles_after_scroll = page.locator('[role="article"]').count()
            except Exception:
                pass

            diag_tail = (
                f" [HTTP {http_status}; węzłów [role=article] max w rundzie: {max_articles_in_round}, "
                f"na końcu: {articles_after_scroll}; sygnał logowania w HTML: {'tak' if login_wall else 'nie'}. "
                "Facebook nie zwraca tu jawnego „kodu błędu” jak Graph API — przy braku wpisów chodzi o to, "
                "co trafiło do DOM (paywall, leniwe ładowanie, zmiana układu), nie o komunikat z API.]"
            )

            if not picked and any_articles:
                warning = (warning + " " if warning else "") + (
                    "Znaleziono role=article, ale bez treści/linku — UI się zmienił albo treść tylko w grafice."
                )
                warning = (warning + " " if warning else "") + diag_tail
            elif 0 < len(picked) < limit and any_articles:
                warning = (warning + " " if warning else "") + (
                    f"Zebrano tylko {len(picked)} unikalnych wpisów (limit {limit}) — "
                    "Facebook mógł nie doładować feedu; spróbuj --headed, --storage-state lub zwiększ --feed-scroll-rounds."
                )
                warning = (warning + " " if warning else "") + diag_tail

            for text, link in picked[:limit]:
                posts.append(
                    {
                        "text": text or "(brak tekstu — np. sam film/obraz)",
                        "permalink_url": link,
                    }
                )
            _log(
                f"{pf}Zamykam przeglądarkę (zebrano {len(posts)} wpisów).", quiet=quiet
            )
        finally:
            browser.close()

    if not posts and warning is None:
        warning = "Nie znaleziono wpisów w znanym formacie — być może wymagane logowanie lub zmiana układu strony."

    return posts, None, warning


def run(
    source_path: Path,
    limit: int,
    *,
    headless: bool,
    storage_state: Path | None,
    slow_mo_ms: int,
    feed_scroll_rounds: int,
    quiet: bool,
) -> list[dict[str, Any]]:
    accounts = load_facebook_accounts_json(source_path)
    if not accounts:
        raise RuntimeError(
            f"Brak kont z polem url wskazującym na facebook.com w {source_path}"
        )

    _log(
        f"Wczytano {len(accounts)} kont z {source_path}. Kolejno: scraping (to może potrwać kilka minut).",
        quiet=quiet,
    )
    results: list[dict[str, Any]] = []
    total = len(accounts)
    for idx, (name, url) in enumerate(accounts, start=1):
        prefix = f"[{idx}/{total}] {name} — "
        _log(f"{prefix}przygotowanie…", quiet=quiet)
        try:
            ident = facebook_page_identifier(url)
        except ValueError as e:
            _log(f"{prefix}błąd URL: {e}", quiet=quiet)
            results.append(
                {
                    "name": name,
                    "url": url,
                    "page_identifier": None,
                    "posts": [],
                    "warning": None,
                    "error": str(e),
                }
            )
            continue
        _log(f"{prefix}@{ident}", quiet=quiet)
        posts, err, warn = fetch_posts_playwright(
            ident,
            limit,
            headless=headless,
            storage_state=storage_state,
            slow_mo_ms=slow_mo_ms,
            feed_scroll_rounds=feed_scroll_rounds,
            quiet=quiet,
            progress_prefix=prefix,
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
        if err:
            _log(f"{prefix}zakończono z błędem: {err}", quiet=quiet)
        else:
            _log(
                f"{prefix}zakończono: {len(posts)} wpisów"
                + (f", uwaga: {warn}" if warn else "")
                + ".",
                quiet=quiet,
            )
    _log("Wszystkie konta przetworzone.", quiet=quiet)
    return results


def _fence_body(text: str) -> str:
    """Treść do bloku kodu w MD — unika zamykania ``` w środku."""
    t = text.replace("```", "'''")
    return f"```\n{t}\n```"


def results_to_markdown(results: list[dict[str, Any]], *, k: int) -> str:
    """Raport markdown: k wpisów na konto (k = limit)."""
    lines: list[str] = [
        "# Scrap Facebook — zebrane wpisy",
        "",
        f"*Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · **k = {k}** wpisów na konto.*",
        "",
    ]
    for block in results:
        name = block.get("name", "")
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"**URL:** {block.get('url', '')}")
        lines.append("")
        if block.get("error"):
            lines.append(f"**Błąd:** {block['error']}")
            lines.append("")
            continue
        if block.get("warning"):
            lines.append(f"*Uwaga:* {block['warning']}")
            lines.append("")
        posts = (block.get("posts") or [])[:k]
        if not posts:
            lines.append("*Brak wpisów.*")
            lines.append("")
            continue
        for i, post in enumerate(posts, 1):
            lines.append(f"### Wpis {i}")
            lines.append("")
            if post.get("permalink_url"):
                lines.append(f"**Link:** {post['permalink_url']}")
                lines.append("")
            body = (post.get("text") or "").strip()
            if body:
                lines.append(_fence_body(body))
                lines.append("")
            else:
                lines.append("*(brak tekstu)*")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def print_final_summary(
    results: list[dict[str, Any]],
    *,
    k: int,
    report_path: Path,
    file: TextIO,
) -> None:
    """Krótkie podsumowanie na koniec (stdout albo stderr — np. przy --json tylko stderr, żeby stdout = sam JSON)."""
    lines: list[str] = [
        "",
        "── Podsumowanie ──",
        f"k = {k} wpisów na konto · pełna treść: {report_path}",
        "",
    ]
    for block in results:
        name = block.get("name", "?")
        if block.get("error"):
            err = str(block["error"]).replace("\n", " ")
            if len(err) > 120:
                err = err[:117] + "…"
            lines.append(f"• {name}: błąd — {err}")
            continue
        n = len(block.get("posts") or [])
        warn = block.get("warning")
        if warn:
            w = str(warn).replace("\n", " ")
            if len(w) > 200:
                w = w[:197] + "…"
            lines.append(f"• {name}: {n}/{k} wpisów · uwaga: {w}")
        else:
            lines.append(f"• {name}: {n}/{k} wpisów")
    print("\n".join(lines), file=file, flush=True)


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(
        description="Pobierz ostatnie wpisy z publicznych stron FB (bez Graph API, przez Playwright)"
    )
    ap.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent / SOURCE_NAME,
        help="Ścieżka do source.json (lista obiektów z polami name, url)",
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
    ap.add_argument(
        "--feed-scroll-rounds",
        type=int,
        default=_DEFAULT_FEED_SCROLL_ROUNDS,
        metavar="N",
        help=(
            "Ile razy przewinąć feed w dół (ładowanie kolejnych wpisów). "
            f"Domyślnie {_DEFAULT_FEED_SCROLL_ROUNDS}."
        ),
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Bez komunikatów postępu na stderr (tylko wynik / błędy).",
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
            feed_scroll_rounds=args.feed_scroll_rounds,
            quiet=args.quiet,
        )
    except KeyboardInterrupt:
        print(
            "\nPrzerwano (Ctrl+C). Jeśli został proces Chromium, możesz go zamknąć w menedżerze zadań.",
            file=sys.stderr,
        )
        return 130
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    k = max(1, args.limit)
    out_md = Path(__file__).resolve().parent / scrap_report_filename()
    try:
        out_md.write_text(results_to_markdown(results, k=k), encoding="utf-8")
    except OSError as e:
        print(f"Nie można zapisać {out_md}: {e}", file=sys.stderr)
        return 1
    print(f"Zapisano raport: {out_md}", file=sys.stderr, flush=True)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print_final_summary(results, k=k, report_path=out_md, file=sys.stderr)
    else:
        print_final_summary(results, k=k, report_path=out_md, file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
