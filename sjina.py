import requests
import json

def search_monster_news(query, platforms):
    """
    Wyszukuje informacje o produktach za pomocą Jina Search API.
    """
    headers = {
        "Accept": "application/json",
        # Opcjonalnie: "Authorization": "Bearer TWOJ_KLUCZ_JINA" (jeśli masz płatny plan)
    }
    
    results = []
    
    for platform in platforms:
        print(f"--- Przeszukuję: {platform} ---")
        
        # Tworzymy zaawansowane zapytanie (Google Dorking)
        # Przykład: site:instagram.com "Monster Energy" new product
        full_query = f"site:{platform} 'Monster Energy' {query}"
        search_url = f"https://s.jina.ai/{full_query}"
        
        try:
            response = requests.get(search_url, headers=headers)
            if response.status_code == 200:
                # Jina zwraca wyniki w formacie Markdown lub JSON
                # Tutaj pobieramy tekst, który zawiera już zeskrapowane treści z wyników wyszukiwania
                results.append({
                    "platform": platform,
                    "content": response.text[:1000] + "..." # Skracamy do podglądu
                })
            else:
                print(f"Błąd dla {platform}: {response.status_code}")
        except Exception as e:
            print(f"Wystąpił błąd: {e}")
            
    return results

def main():
    # Definiujemy platformy, które nas interesują
    social_platforms = [
        "instagram.com",
        "reddit.com/r/energydrinks",
        "facebook.com"
    ]
    
    keyword = "nowości smaki 2026 premiere" # Możesz zmienić na "new flavor leaks"
    # "Monster Energy" new product after:2025-12-31
    
    found_data = search_monster_news(keyword, social_platforms)
    
    # Wyświetlamy wynik
    for item in found_data:
        print(f"\n[DANE Z {item['platform'].upper()}]:")
        print(item['content'])

if __name__ == "__main__":
    main()