import requests

def google_search_free(query, api_key, cx_id):
    url = f"https://www.googleapis.com/customsearch/v1"
    params = {
        'q': query,
        'key': api_key,
        'cx': cx_id  # ID Twojej wyszukiwarki
    }
    response = requests.get(url, params=params)
    return response.json()