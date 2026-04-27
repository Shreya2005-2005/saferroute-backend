import requests
try:
    resp = requests.get("http://localhost:8000/")
    print(f"Status: {resp.status_code}")
    print(f"Content: {resp.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
