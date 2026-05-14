import json

import requests


def search_monster_serper(
    query, day_start="2026-05-13", day_end_exclusive="2026-05-14"
):
    url = "https://google.serper.dev/search"

    # TUTAJ WKLEJ SWÓJ KLUCZ Z SERPER.DEV
    api_key = ""

    # Budujemy zapytanie ograniczające wyniki do konkretnych portali
    # Używamy operatora OR, aby przeszukać wszystko naraz
    # Zakres dat: cały kalendarzowy dzień day_start (before jest wyłącznie)
    date_filter = f"after:{day_start} before:{day_end_exclusive}"
    full_query = f"{query} (site:instagram.com OR site:reddit.com OR site:facebook.com) {date_filter}"

    payload = json.dumps(
        {
            "q": full_query,
            "gl": "pl",  # Wyniki z Polski (możesz zmienić na "us")
            "hl": "pl",  # Język polski
            "autocorrect": True,
        }
    )

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    try:
        response = requests.request("POST", url, headers=headers, data=payload)
        response.raise_for_status()
        results = response.json()

        print(
            f"--- Wyniki dla: {query} (daty indeksu: {day_start} ≤ dzień < {day_end_exclusive}) ---\n"
        )

        # Iterujemy po wynikach organicznych
        for result in results.get("organic", []):
            title = result.get("title")
            link = result.get("link")
            snippet = result.get("snippet")

            print(f"📌 {title}")
            print(f"🔗 {link}")
            print(f"📝 Opis: {snippet}")
            print("-" * 50)

            # W tym miejscu możesz dodać wywołanie Jiny:
            # content = requests.get(f"https://r.jina.ai/{link}").text

    except Exception as e:
        print(f"Błąd podczas wyszukiwania: {e}")


# Wywołanie funkcji
search_monster_serper("Monster Energy nowe produkty smaki 2026")
