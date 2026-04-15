import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("dashboard.data")

API_KEY = os.environ.get("PAINEL_BEST_API_KEY", "klxMbmr6pWOGO48GNvG746SWnQk_BMl3In4c_9IDpD4")
HEADERS = {
    "api-key": API_KEY,
    "Api-Key": API_KEY,
    "accept": "*/*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

RESELLER_MAP = {
    "alecsmoura": "ADS - ALECS",
    "Grafite": "ADS - GRAFITE",
    "igor01": "ADS - IGOR",
    "jaqueline": "ADS - JAQUELINE",
    "Jonathan01": "ADS - JONATHAN",
    "tdstheflash": "REVENDA JULIO",
    "Junior": "ADS - EVERALDO",
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
    "MicheliRibeiro": "ADS - MICHELI",
    "GuiMendes": "ADS - GUILHERME",
    "Alexandre01": "ADS - ALEXANDRE",
    "Williamfarias": "ADS - WILLIAM",
    "tdsthechosen": "REVENDA JACQUES",
    "tdscr7milgols": "REVENDA GABRIEL",
    "david01": "ADS - DAVID",
    "Luccasdf": "ADS - LUCAS",
    "brunosoares": "ADS - BRUNO",
    "joseotavio": "ADS - JOSE",
    "jhow": "ADS - JHOW",
    "angelo": "ADS - ANGELONATV",
    "rafa": "ADS - RAFANATV",
}

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
    "ADS - EVERALDO": 2580,
    "ADS - MICHELI": 3517,
    "ADS - WILLIAM": 3519,
    "ADS - GUILHERME": 3523,
    "ADS - ALEXANDRE": 3533,
    "ADS - JOSEOTAVIO": 3647,
    "ADS - BRUNO": 3721,
    "ADS - DAVID": 3558,
    "ADS - LUCCAS": 3521,
    # "ADS - DOUGLAS SANDI": 3368,
    # "ADS - MATEUS": 2815,
    # "ADS - KRONE": 3481,
}

BLACKLISTED_USER_IDS = {3566, 2850, 2123, 2861}

_thread_local = threading.local()
_lines_cache = {}
_lines_cache_ttl_seconds = 300


def _build_retry():
    return Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )


def _get_session():
    sess = getattr(_thread_local, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(HEADERS)
        adapter = HTTPAdapter(max_retries=_build_retry(), pool_connections=20, pool_maxsize=20)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
        _thread_local.session = sess
    return sess


def _request_json(url: str, *, params: dict, timeout: int = 20):
    resp = _get_session().get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _safe_count(json_data):
    try:
        if not isinstance(json_data, dict):
            return 0
        if "count" in json_data:
            return int(json_data.get("count") or 0)
        if "total" in json_data:
            return int(json_data.get("total") or 0)
        results = json_data.get("results")
        if isinstance(results, list):
            return len(results)
        return 0
    except Exception:
        return 0


def _get_log_window(days=7, period=None, start_ts=None, end_ts=None):
    br_tz = timezone(timedelta(hours=-3))
    now = datetime.now(br_tz)

    if start_ts is not None and end_ts is not None:
        return int(start_ts), int(end_ts)

    p = (period or "").strip().lower()
    if p in {"today", "hoje", "1"}:
        start_date = now.replace(hour=0, minute=1, second=0, microsecond=0)
        end_date = now
        return int(start_date.timestamp()), int(end_date.timestamp())

    if p in {"yesterday", "ontem", "0", "-1"}:
        y = now - timedelta(days=1)
        start_date = y.replace(hour=0, minute=1, second=0, microsecond=0)
        end_date = y.replace(hour=23, minute=59, second=59, microsecond=0)
        return int(start_date.timestamp()), int(end_date.timestamp())

    n = max(int(days), 1)
    start_date = (now - timedelta(days=n - 1)).replace(hour=0, minute=1, second=0, microsecond=0)
    end_date = now
    return int(start_date.timestamp()), int(end_date.timestamp())


def get_ativos(revendedores_ids, use_cache_only=False):
    base_url = "https://api.painel.best/lines/"
    data = []

    def fetch_one(nome, uid):
        if uid in BLACKLISTED_USER_IDS:
            return None

        now = time.time()
        cache_entry = _lines_cache.get(uid)
        
        # Se tem cache válido, usa ele imediatamente
        if cache_entry and (now - cache_entry["ts"]) < _lines_cache_ttl_seconds:
            cached = cache_entry["data"].copy()
            cached["name"] = nome
            cached["from_cache"] = True
            return cached
        
        # Se só quer cache, não faz requisição
        if use_cache_only:
            return {
                "name": nome,
                "total_clientes": 0,
                "ativos_reais": 0,
                "testes_ativos": 0,
                "novos_clientes": 0,
                "loading": True
            }

        def fetch_count(extra_params=None, timeout=12):
            params = {"user_id": uid, "page": 1, "per_page": 1}
            if extra_params:
                params.update(extra_params)
            try:
                payload = _request_json(base_url, params=params, timeout=timeout)
                return _safe_count(payload)
            except Exception as e:
                logger.warning("Timeout/contagem falhou para %s: %s", nome, e)
                return 0

        try:
            # Fazer todas as contagens com timeout menor
            result = {
                "name": nome,
                "total_clientes": fetch_count(),
                "ativos_reais": fetch_count({"is_trial": "false", "is_expired": "false"}),
                "testes_ativos": fetch_count({"is_trial": "true", "is_expired": "false"}),
                "novos_clientes": fetch_count({"is_trial": "false"}),
            }
            _lines_cache[uid] = {"ts": now, "data": result.copy()}
            return result
        except Exception as e:
            logger.warning("Falha ao buscar ativos para %s (%s): %s", nome, uid, e)
            fallback = cache_entry["data"].copy() if cache_entry else {
                "name": nome,
                "total_clientes": 0,
                "ativos_reais": 0,
                "testes_ativos": 0,
                "novos_clientes": 0,
            }
            fallback["name"] = nome
            fallback["stale"] = bool(cache_entry)
            return fallback

    max_workers = min(20, max(4, len(revendedores_ids) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(fetch_one, nome, uid)
            for nome, uid in revendedores_ids.items()
            if uid not in BLACKLISTED_USER_IDS
        ]
        for fut in as_completed(futures):
            try:
                r = fut.result(timeout=45)  # Timeout total por worker
                if r is not None:
                    data.append(r)
            except Exception as e:
                logger.exception("Erro inesperado ao montar ativos: %s", e)

    data.sort(key=lambda x: x.get("ativos_reais", 0), reverse=True)
    return data


def _count_logs_for_uid(uid: int, start_ts: int, end_ts: int):
    base_url = "https://api.painel.best/user/logs/"
    counters = {"tests": 0, "sales_new": 0, "renewals": 0}
    action_map = {
        "new": "tests",
        "trial-conversion": "sales_new",
        "extend": "renewals",
    }

    for action, target in action_map.items():
        page = 1
        consecutive_errors = 0
        while True:
            params = {
                "action": action,
                "created_at__gte": start_ts,
                "user_id": uid,
                "page": page,
                "per_page": 1000,
            }
            try:
                res = _request_json(base_url, params=params, timeout=15)
                consecutive_errors = 0  # Reset error count on success
            except Exception as e:
                consecutive_errors += 1
                logger.warning("Falha ao buscar logs %s para uid=%s (tentativa %s): %s", action, uid, consecutive_errors, e)
                if consecutive_errors >= 3:
                    logger.error("Muitos erros consecutivos para uid=%s, abortando action=%s", uid, action)
                    break
                continue  # Tenta novamente a mesma página

            results = res.get("results", []) or []
            if not results:
                break

            valid_count = 0
            for log in results:
                created_at = int(log.get("created_at") or 0)
                if created_at < start_ts or created_at > end_ts:
                    continue
                valid_count += 1

            counters[target] += valid_count

            last_created_at = int(results[-1].get("created_at") or 0)
            if last_created_at and last_created_at < start_ts:
                break
            if not res.get("next_page"):
                break
            page += 1
            if page > 50:
                break

    return counters


def get_ranking(days=7, revendedores_ids=None, period=None, start_ts=None, end_ts=None):
    start_ts, end_ts = _get_log_window(days=days, period=period, start_ts=start_ts, end_ts=end_ts)
    stats = {}
    discovered_ids = {}

    if isinstance(revendedores_ids, dict) and revendedores_ids:
        id_to_name = {uid: name for name, uid in revendedores_ids.items() if uid not in BLACKLISTED_USER_IDS}
        allowed_uids = set(id_to_name.keys())

        for name in revendedores_ids.keys():
            stats[name] = {"tests": 0, "sales_new": 0, "conversions": 0, "renewals": 0}

        # Aumentar workers para 20 (antes era 8)
        max_workers = min(20, max(4, len(allowed_uids) or 1))
        logger.info(f"Iniciando busca de ranking com {max_workers} workers para {len(allowed_uids)} revendedores")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_count_logs_for_uid, uid, start_ts, end_ts) for uid in allowed_uids]
            future_to_uid = {future: uid for future, uid in zip(futures, allowed_uids)}

            completed = 0
            for fut in as_completed(future_to_uid):
                uid = future_to_uid[fut]
                try:
                    counts = fut.result(timeout=120)  # Timeout de 2 minutos por revendedor
                    completed += 1
                    if completed % 5 == 0:
                        logger.info(f"Progresso: {completed}/{len(allowed_uids)} revendedores processados")
                except Exception as e:
                    logger.warning("Erro ao consolidar logs do uid=%s: %s", uid, e)
                    continue

                name = id_to_name.get(uid)
                if not name:
                    continue

                stats[name]["tests"] = int(counts.get("tests", 0) or 0)
                stats[name]["sales_new"] = int(counts.get("sales_new", 0) or 0)
                stats[name]["renewals"] = int(counts.get("renewals", 0) or 0)
                discovered_ids[name] = uid
        
        logger.info(f"Busca de ranking concluída. {completed}/{len(allowed_uids)} revendedores com sucesso.")
    else:
        base_url = "https://api.painel.best/user/logs/"
        action_map = {
            "new": "tests",
            "trial-conversion": "sales_new",
            "extend": "renewals",
        }

        for action, target in action_map.items():
            page = 1
            while True:
                params = {
                    "action": action,
                    "created_at__gte": start_ts,
                    "page": page,
                    "per_page": 1000,
                }
                try:
                    res = _request_json(base_url, params=params, timeout=25)
                except Exception as e:
                    logger.warning("Falha ao buscar logs globais %s: %s", action, e)
                    break

                results = res.get("results", []) or []
                if not results:
                    break

                for log in results:
                    uid = log.get("user_id")
                    if uid and uid in BLACKLISTED_USER_IDS:
                        continue

                    created_at = int(log.get("created_at") or 0)
                    if created_at < start_ts or created_at > end_ts:
                        continue

                    username = log.get("user_username")
                    if not username:
                        continue

                    display_name = RESELLER_MAP.get(username, username)
                    stats.setdefault(display_name, {"tests": 0, "sales_new": 0, "conversions": 0, "renewals": 0})
                    stats[display_name][target] += 1

                    if uid and display_name not in discovered_ids:
                        discovered_ids[display_name] = uid

                last_created_at = int(results[-1].get("created_at") or 0)
                if last_created_at and last_created_at < start_ts:
                    break
                if not res.get("next_page"):
                    break
                page += 1
                if page > 50:
                    break

    result = []
    for name, m in stats.items():
        tests = int(m.get("tests", 0) or 0)
        sales_new = int(m.get("sales_new", 0) or 0)
        renewals = int(m.get("renewals", 0) or 0)
        conv = round((sales_new / tests * 100), 1) if tests > 0 else 0.0
        result.append(
            {
                "revenda": name,
                "vendas": sales_new,
                "conversoes": 0,
                "testes": tests,
                "renovacoes": renewals,
                "conversao": conv,
            }
        )

    return sorted(result, key=lambda x: (x["vendas"], x["conversao"]), reverse=True), discovered_ids
