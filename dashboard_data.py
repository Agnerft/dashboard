import requests
from datetime import datetime, timezone, timedelta
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configurações globais
API_KEY = "klxMbmr6pWOGO48GNvG746SWnQk_BMl3In4c_9IDpD4"
HEADERS = {
    "api-key": API_KEY,
    "Api-Key": API_KEY,
    "accept": "*/*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# Mapeamento completo de nomes (Baseado no rankontemgeral.py)
RESELLER_MAP = {
    "alecsmoura": "ADS - ALECS",
    "Grafite": "ADS - GRAFITE",
    "igor01": "ADS - IGOR",
    "jaqueline": "ADS - JAQUELINE",
    "Jonathan01": "ADS - JONATHAN",
    "tdstheflash": "REVENDA JULIO",
    "Junior": "ADS - EVERALDO JR",
    "kaio01": "ADS - KAIO",
    "LucasLoko": "ADS - JOAO LUCAS", 
    "luispaulo": "ADS - PAULO",
    "revendaallan": "ADS - ALLAN",
    "revendadiogo": "ADS - DIOGO",
    "revendaedersonmotta": "ADS - EDERSON",
    "tdsfga": "REVENDA EMERSON",
    "tdsmalware": "REVENDA ERICK",
    "tdsdrvendasnights": "REVENDA HERON",
    "tdsbigseven": "REVENDA IGOR",
    "tdsmessithebest": "REVENDA JACSON",
    "tdspaqueta20vender": "REVENDA JOAO",
    "revendamix": "ADS - JHOW",
    "tdsrobson": "REVENDA ROBSON",
    "tdssmallville": "REVENDA ROGERIO",
    "sandi01": "ADS - DOUGLAS SANDI",
    "MicheliRibeiro": "ADS - MICHELE JR",
    "GuiMendes": "ADS - GUILHERME JR",
    "Alexandre01": "ADS - ALEXANDRE JR",
    "Williamfarias": "ADS - WILLIAM JR",
    "tdsthechosen": "REVENDA JACQUES",
    "tdscr7milgols": "REVENDA GABRIEL",
    "david01": "ADS - DAVID",
    "Luccasdf": "ADS - LUCAS D. JR",
    "brunosoares":"ADS - BRUNO SOARES",
    "joseotavio":"ADS - JOSE OTAVIO",
    "jhow":"ADS - JHOW",
    "angelo":"ADS - ANGELONATV",
    "rafa":"ADS - RAFANATV"

}

# IDs das revendas para busca de ativos (Mapeado pelo nome do RESELLER_MAP)
REVENDEDORES_IDS = {
    "REVENDA GABRIEL": 2054,
    "REVENDA EMERSON": 2915,
    "REVENDA ERICK": 2474,
    "REVENDA HERON": 3034,
    "REVENDA IGOR": 2473,
    "REVENDA JACSON": 3043,
    "REVENDA JOAO": 2858,
    "REVENDA ROBSON": 3095,
    "REVENDA ROGERIO": 3499,
    "REVENDA JACQUES": 3230,
    "REVENDA JULIO": 3472,
    "ADS - JUNIOR": 2580,
    "ADS - MICHELI": 3517,
    "ADS - WILLIAM": 3519,
    "ADS - GUILHERME": 3523,
    "ADS - ALEXANDRE": 3533,
    "ADS - JOSE": 3647,
    "ADS - BRUNO": 3721,
    "ADS - DAVID": 3558,
    "ADS - LUCAS": 3521,
    "ADS - DOUGLAS SANDI": 3368,
    "ADS - MATEUS": 2815,
    "ADS - KRONE": 3481

}

_thread_local = threading.local()
_lines_cache = {}
_lines_cache_ttl_seconds = 300
BLACKLISTED_USER_IDS = {3566, 2850, 2123, 2861}

def _get_session():
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        _thread_local.session = sess
    return sess

def _safe_count(json_data):
    try:
        return int(json_data.get("count", 0))
    except Exception:
        return 0

def get_ativos(revendedores_ids):
    BASE_URL = "https://api.painel.best/lines/"
    data = []

    def fetch_one(nome, uid):
        if uid in BLACKLISTED_USER_IDS:
            return None
        now = time.time()
        cache_entry = _lines_cache.get(uid)
        if cache_entry and (now - cache_entry["ts"]) < _lines_cache_ttl_seconds:
            cached = cache_entry["data"].copy()
            cached["name"] = nome
            return cached

        sess = _get_session()

        resp_total = sess.get(BASE_URL, params={"user_id": uid, "page": 1, "per_page": 1}, timeout=10)
        total_clientes = _safe_count(resp_total.json()) if resp_total.status_code == 200 else 0

        resp_ativos = sess.get(
            BASE_URL,
            params={"user_id": uid, "is_trial": "false", "is_expired": "false", "page": 1, "per_page": 1},
            timeout=10
        )
        ativos_reais = _safe_count(resp_ativos.json()) if resp_ativos.status_code == 200 else 0

        resp_testes_at = sess.get(
            BASE_URL,
            params={"user_id": uid, "is_trial": "true", "is_expired": "false", "page": 1, "per_page": 1},
            timeout=10
        )
        testes_ativos = _safe_count(resp_testes_at.json()) if resp_testes_at.status_code == 200 else 0

        resp_novos = sess.get(
            BASE_URL,
            params={"user_id": uid, "is_trial": "false", "page": 1, "per_page": 1},
            timeout=10
        )
        novos_clientes = _safe_count(resp_novos.json()) if resp_novos.status_code == 200 else 0

        result = {
            "name": nome,
            "total_clientes": total_clientes,
            "ativos_reais": ativos_reais,
            "testes_ativos": testes_ativos,
            "novos_clientes": novos_clientes
        }
        _lines_cache[uid] = {"ts": now, "data": result.copy()}
        return result

    with ThreadPoolExecutor(max_workers=min(16, max(4, len(revendedores_ids)))) as executor:
        futures = [executor.submit(fetch_one, nome, uid) for nome, uid in revendedores_ids.items() if uid not in BLACKLISTED_USER_IDS]
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r is not None:
                    data.append(r)
            except Exception:
                pass

    data.sort(key=lambda x: x.get("ativos_reais", 0), reverse=True)
    return data

def get_ranking(days=7, revendedores_ids=None):
    BASE_URL = "https://api.painel.best/user/logs/"
    br_tz = timezone(timedelta(hours=-3))
    now = datetime.now(br_tz)
    start_date = (now - timedelta(days=max(int(days), 1) - 1)).replace(hour=0, minute=1, second=0, microsecond=0)
    start_ts = int(start_date.timestamp())
    stats = {}
    discovered_ids = {} # display_name -> user_id

    allowed_uids = None
    id_to_name = None
    if isinstance(revendedores_ids, dict) and revendedores_ids:
        id_to_name = {uid: name for name, uid in revendedores_ids.items() if uid not in BLACKLISTED_USER_IDS}
        allowed_uids = set(id_to_name.keys())
        for name in revendedores_ids.keys():
            stats[name] = {"tests": 0, "sales_new": 0, "conversions": 0, "renewals": 0}
        
        def fetch_one_uid(uid: int):
            sess = _get_session()
            tests = 0
            sales = 0
            renewals = 0
            for action in ['new', 'trial-conversion', 'extend']:
                page = 1
                while True:
                    params = {"action": action, "created_at__gte": start_ts, "user_id": uid, "page": page, "per_page": 1000}
                    try:
                        resp = sess.get(BASE_URL, params=params, timeout=20)
                    except Exception:
                        break
                    if resp.status_code != 200:
                        break
                    res = resp.json()
                    results = res.get("results", []) or []
                    if action == "new":
                        tests += len(results)
                    elif action == "trial-conversion":
                        sales += len(results)
                    elif action == "extend":
                        renewals += len(results)
                    if not res.get("next_page"):
                        break
                    page += 1
                    if page > 50:
                        break
            return uid, tests, sales, renewals

        with ThreadPoolExecutor(max_workers=min(16, max(4, len(allowed_uids)))) as executor:
            futures = [executor.submit(fetch_one_uid, uid) for uid in allowed_uids]
            for fut in as_completed(futures):
                try:
                    uid, tests, sales, renewals = fut.result()
                except Exception:
                    continue
                name = id_to_name.get(uid)
                if not name:
                    continue
                if name not in stats:
                    stats[name] = {"tests": 0, "sales_new": 0, "conversions": 0, "renewals": 0}
                stats[name]["tests"] = int(tests or 0)
                stats[name]["sales_new"] = int(sales or 0)
                stats[name]["renewals"] = int(renewals or 0)

    if allowed_uids is None:
        for action in ['new', 'trial-conversion', 'extend']:
            page = 1
            has_next = True
            while has_next:
                params = {"action": action, "created_at__gte": start_ts, "page": page, "per_page": 1000}
                try:
                    resp = _get_session().get(BASE_URL, params=params, timeout=20)
                    if resp.status_code != 200: break
                    res = resp.json()
                    results = res.get('results', [])
                    for log in results:
                        u = log.get('user_username')
                        uid = log.get('user_id')
                        if uid and uid in BLACKLISTED_USER_IDS:
                            continue
                        if not u:
                            continue
                        display_name = RESELLER_MAP.get(u, u)

                        if display_name not in stats:
                            stats[display_name] = {
                                "tests": 0,
                                "sales_new": 0,
                                "conversions": 0,
                                "renewals": 0
                            }

                        if action == "new":
                            stats[display_name]["tests"] += 1
                        elif action == "trial-conversion":
                            stats[display_name]["sales_new"] += 1
                        elif action == "extend":
                            stats[display_name]["renewals"] += 1
                        
                        # Salva o ID mapeado pelo nome de exibição
                        if uid and uid not in BLACKLISTED_USER_IDS and display_name not in discovered_ids:
                            discovered_ids[display_name] = uid

                    if results:
                        last_created_at = results[-1].get("created_at")
                        if last_created_at and int(last_created_at) < start_ts:
                            break

                    has_next = res.get('next_page') is not None
                    page += 1
                    if page > 50: break
                except: break
    
    result = []
    for name, m in stats.items():
        conv = round((m["sales_new"] / m["tests"] * 100), 1) if m["tests"] > 0 else 0.0
        result.append({
            "revenda": name,
            "vendas": m["sales_new"],
            "conversoes": 0,
            "testes": m["tests"],
            "renovacoes": m["renewals"],
            "conversao": conv
        })
    
    return sorted(result, key=lambda x: (x['vendas'], x['conversao']), reverse=True), discovered_ids
