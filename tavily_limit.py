import requests

TAVILY_API_KEY = "TWOJ_KLUCZ_TAVILY"

def smart_monster_search(product_query):
    search_url = "https://api.tavily.com/search"
    
    # Skupiamy się na źródłach, gdzie najszybciej pojawiają się nowości
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": f"Monster Energy {product_query} 2026 leaks rumors",
        "search_depth": "advanced",
        "include_domains": ["reddit.com", "instagram.com", "stacker.com", "bevnet.com"],
        "max_results": 5
    }

    response = requests.post(search_url, json=payload)
    results = response.json().get("results", [])

    for res in results:
        print(f"\n🚀 ZNALEZIONO: {res['title']}")
        print(f"🔗 URL: {res['url']}")
        
        # Jeśli treść z Tavily jest krótka, używamy Jiny jako "lupy"
        if len(res.get('content', '')) < 200:
            print("🔍 Treść zbyt krótka, doczytuję przez Jinę...")
            full_content = requests.get(f"https://r.jina.ai/{res['url']}").text
            print(f"📝 Pełny tekst: {full_content[:500]}...")
        else:
            print(f"📝 Wyciągnięta treść: {res['content'][:500]}...")

# Szukamy konkretów
smart_monster_search("new flavors")