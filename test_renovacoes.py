import requests
import time
from datetime import datetime, timedelta, timezone

API_KEY = "klxMbmr6pWOGO48GNvG746SWnQk_BMl3In4c_9IDpD4"
HEADERS = {
    "api-key": API_KEY,
    "Api-Key": API_KEY,
    "accept": "*/*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

# Testar endpoint de logs de renovação (extend)
base_url = "https://api.painel.best/user/logs/"

# Período: últimos 30 dias
br_tz = timezone(timedelta(hours=-3))
now = datetime.now(br_tz)
start_date = (now - timedelta(days=30)).replace(hour=0, minute=1, second=0, microsecond=0)
start_ts = int(start_date.timestamp())
end_ts = int(now.timestamp())

print(f"Testando renovações de {start_date} até {now}")
print(f"Timestamps: {start_ts} até {end_ts}")

# Testar com um user_id específico - ADS - EVERALDO = 2580
test_uid = 2580

params = {
    "action": "extend",
    "created_at__gte": start_ts,
    "user_id": test_uid,
    "page": 1,
    "per_page": 1000,
}

try:
    print(f"\nBuscando renovações para user_id={test_uid}...")
    resp = requests.get(base_url, params=params, headers=HEADERS, timeout=30)
    print(f"Status: {resp.status_code}")
    print(f"URL: {resp.url}")
    
    if resp.status_code == 200:
        data = resp.json()
        results = data.get("results", [])
        print(f"Total de resultados: {len(results)}")
        
        if results:
            print("\nPrimeiros 5 resultados:")
            for i, log in enumerate(results[:5]):
                print(f"  {i+1}. created_at={log.get('created_at')}, action={log.get('action')}")
        else:
            print("Nenhuma renovação encontrada.")
    else:
        print(f"Erro: {resp.text}")
except Exception as e:
    print(f"Erro na requisição: {e}")

# Testar sem filtro de user_id para ver se há dados
print("\n\nTestando sem filtro de user_id...")
params2 = {
    "action": "extend",
    "created_at__gte": start_ts,
    "page": 1,
    "per_page": 100,
}

try:
    resp = requests.get(base_url, params=params2, headers=HEADERS, timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        results = data.get("results", [])
        print(f"Total de renovações (sem filtro): {len(results)}")
        if results:
            print("Primeiros 3 resultados:")
            for log in results[:3]:
                print(f"  user_id={log.get('user_id')}, user_username={log.get('user_username')}, created_at={log.get('created_at')}")
except Exception as e:
    print(f"Erro: {e}")
