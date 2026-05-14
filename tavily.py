import requests

def search_monster_tavily(query):
    # API Key pobierzesz za darmo na tavily.com (dają 1000 darmowych zapytań/msc)
    TAVILY_API_KEY = "TWOJ_KLUCZ_API"
    
    url = "https://api.tavily.com/search"
    
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced", # 'advanced' lepiej radzi sobie z newsami i socialami
        "include_domains": ["instagram.com", "reddit.com", "facebook.com", "tiktok.com"],
        "max_results": 5
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        results = response.json()

        print(f"\n--- Wyniki wyszukiwania dla: {query} ---\n")
        
        for result in results.get("results", []):
            print(f"TYTUŁ: {result['title']}")
            print(f"URL: {result['url']}")
            print(f"TREŚĆ: {result['content'][:300]}...") # Skondensowana treść strony
            print("-" * 30)
            
    except Exception as e:
        print(f"Błąd: {e}")

# Przykład użycia dla "leaków" nowych smaków
search_monster_tavily("Monster Energy new flavor leak 2026")