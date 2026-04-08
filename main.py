from fastapi import FastAPI, Request
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import os
import time
import json
import threading
import re
import unicodedata
import secrets
import logging
from datetime import date, datetime, timedelta, timezone
from dashboard_data import get_ativos, get_ranking
import base64
import hmac
import hashlib

app = FastAPI(title="Dashboard Revendas Web")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dashboard")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(os.path.join(BASE_DIR, "templates", "index.html"), "r", encoding="utf-8") as f:
        return f.read()

_CACHE = {}
_CACHE_TTL_SECONDS = 300

_ADS_LOCK = threading.Lock()
_ADS_FILE_PATH = os.path.join(BASE_DIR, "ads_spend.json")
_DATA_CACHE_PATH = os.path.join(BASE_DIR, "data_cache.json")
_REFRESH_THREADS = {}

_USERS_FILE = os.path.join(BASE_DIR, "users.json")
_AUTH_SECRET = os.environ.get("DASH_AUTH_SECRET", "change-me-local-secret")
_USERS_LOCK = threading.Lock()


class AdsEntry(BaseModel):
    date: str
    amount: float


class AdsEntriesPayload(BaseModel):
    revenda: str
    entries: list[AdsEntry]


class AdsIngestTxt(BaseModel):
    date: str
    content: str
    save: bool = True


def _normalize_period(period: str | None, days: int = 7) -> str:
    p = (period or "").strip().lower()
    if p in {"today", "hoje", "1"}:
        return "today"
    if p in {"yesterday", "ontem", "0", "-1"}:
        return "yesterday"
    try:
        n = max(int(days), 1)
    except Exception:
        n = 7
    return str(n)


def _get_period_bounds(period: str | None, days: int = 7) -> tuple[date, date, str]:
    today = date.today()
    p = _normalize_period(period, days)
    if p == "today":
        return today, today, p
    if p == "yesterday":
        y = today - timedelta(days=1)
        return y, y, p
    n = max(int(p), 1)
    start = today - timedelta(days=n - 1)
    return start, today, p


def _load_ads_store():
    if not os.path.exists(_ADS_FILE_PATH):
        return {"revendas": {}}
    try:
        with open(_ADS_FILE_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        if not raw.strip():
            return {"revendas": {}}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"revendas": {}}
        if not isinstance(data.get("revendas"), dict):
            data["revendas"] = {}
        return data
    except Exception:
        try:
            broken_path = _ADS_FILE_PATH + ".broken"
            if os.path.exists(_ADS_FILE_PATH) and not os.path.exists(broken_path):
                os.replace(_ADS_FILE_PATH, broken_path)
        except Exception:
            pass
        return {"revendas": {}}


def _save_ads_store(data):
    tmp_path = _ADS_FILE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _ADS_FILE_PATH)


def _load_data_cache():
    if not os.path.exists(_DATA_CACHE_PATH):
        return {}
    try:
        with open(_DATA_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_data_cache(cache):
    tmp_path = _DATA_CACHE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _DATA_CACHE_PATH)


def _norm_text(value):
    s = "" if value is None else str(value)
    return re.sub(r"\s+", " ", s).strip()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _week_range(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end


def _month_range(d: date):
    start = d.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    end = next_month - timedelta(days=1)
    return start, end


def _period_cache_key(period: str | None, days: int = 7) -> str:
    start_d, end_d, p = _get_period_bounds(period, days)
    return f"{p}:{start_d.isoformat()}:{end_d.isoformat()}"


def _ads_amounts_for_revenda_in_range(rev_map: dict, start_d: date, end_d: date):
    total = 0.0
    cur = start_d
    while cur <= end_d:
        total += float(rev_map.get(cur.isoformat(), 0) or 0)
        cur += timedelta(days=1)
    return round(total, 2)


def _revenda_match_key(value: str) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("ASCII")
    s = s.upper()
    s = re.sub(r"\b(ADS|REVENDA|REV|OFICIAL)\b", " ", s)
    s = re.sub(r"\b(JR|JUNIOR|NATV)\b", " ", s)
    s = re.sub(r"\d+", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_revenda_alias(canon_name: str) -> str:
    from dashboard_data import REVENDEDORES_IDS
    if canon_name in REVENDEDORES_IDS:
        return canon_name

    norm = _revenda_match_key(canon_name)
    if not norm:
        return canon_name

    known: dict[str, str] = {}
    for k in REVENDEDORES_IDS.keys():
        nk = _revenda_match_key(k)
        if nk and nk not in known:
            known[nk] = k

    if norm in known:
        return known[norm]

    norm_flat = norm.replace(" ", "")
    best_key = None
    best_score = 0
    for nk, k in known.items():
        nk_flat = nk.replace(" ", "")
        if len(norm_flat) < 5 or len(nk_flat) < 5:
            continue
        max_len = min(len(norm_flat), len(nk_flat))
        score = 0
        for i in range(max_len):
            if norm_flat[i] != nk_flat[i]:
                break
            score += 1
        if score > best_score:
            best_score = score
            best_key = k
    if best_key and best_score >= 5:
        return best_key

    in_tokens = set(norm.split())
    best_key = None
    best_overlap = 0
    for nk, k in known.items():
        overlap = len(in_tokens & set(nk.split()))
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = k

    if best_key and best_overlap >= 2:
        return best_key
    if best_key and best_overlap == 1 and len(in_tokens) == 1:
        return best_key
    return canon_name


def _canonicalize_revenda_name(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*-\s*\d{3,8}\s*$", "", s)
    s = re.sub(r"\s+\d{3,8}\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _resolve_revenda_alias(s)


def _load_users():
    if not os.path.exists(_USERS_FILE):
        return []
    try:
        with open(_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_users(users: list[dict]):
    tmp_path = _USERS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _USERS_FILE)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return (
        "pbkdf2_sha256$"
        + str(iterations)
        + "$"
        + base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
        + "$"
        + base64.urlsafe_b64encode(dk).decode("ascii").rstrip("=")
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, it_s, salt_b64, dk_b64 = (password_hash or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(it_s)
        salt = base64.urlsafe_b64decode(salt_b64 + "==")
        expected = base64.urlsafe_b64decode(dk_b64 + "==")
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(expected, got)
    except Exception:
        return False


def _sign_token(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    data = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_AUTH_SECRET.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
    return data + "." + sig


def _verify_token(token: str) -> dict | None:
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        data, sig = parts
        expected = hmac.new(_AUTH_SECRET.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        raw = base64.urlsafe_b64decode(data + "==").decode("utf-8")
        payload = json.loads(raw)
        exp = payload.get("exp")
        if exp and time.time() > float(exp):
            return None
        return payload
    except Exception:
        return None


def _auth_scope_from_request(request: Request) -> dict:
    hdr = request.headers.get("authorization") or request.headers.get("Authorization")
    if not hdr or not hdr.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    tok = hdr.split(" ", 1)[1].strip()
    payload = _verify_token(tok)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid token")
    return payload


def _apply_scope_to_ativos(data, user_role: str, user_rev: str):
    if not isinstance(data, list):
        return []
    if user_role != "admin" and user_rev and user_rev != "*":
        return [a for a in data if a.get("name") == user_rev]
    return data


def _valid_ativos(data):
    return (
        isinstance(data, list)
        and len(data) > 0
        and any(isinstance(item, dict) and item.get("name") for item in data)
    )


@app.post("/auth/login")
def login_user(body: dict):
    try:
        username = (body.get("username") or "").strip()
        password = (body.get("password") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="payload inválido")

    if not username or not password:
        raise HTTPException(status_code=400, detail="credenciais ausentes")

    users = _load_users()
    for u in users:
        if u.get("username") != username:
            continue

        stored_hash = u.get("password_hash")
        stored_plain = u.get("password")
        ok = False

        if stored_hash:
            ok = _verify_password(password, str(stored_hash))
        elif stored_plain is not None:
            ok = str(stored_plain) == password

        if ok:
            role = u.get("role") or "user"
            revenda = u.get("revenda") or ""
            payload = {
                "username": username,
                "role": role,
                "revenda": revenda,
                "iat": time.time(),
                "exp": time.time() + 86400,
            }
            return {"token": _sign_token(payload), "role": role, "revenda": revenda}

    raise HTTPException(status_code=401, detail="credenciais inválidas")


@app.get("/auth/me")
def auth_me(request: Request):
    payload = _auth_scope_from_request(request)
    return {
        "username": payload.get("username"),
        "role": payload.get("role"),
        "revenda": payload.get("revenda"),
    }


@app.get("/auth/users")
def auth_list_users(request: Request):
    scope = _auth_scope_from_request(request)
    if scope.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")

    with _USERS_LOCK:
        users = _load_users()

    out = []
    for u in users:
        out.append(
            {
                "username": u.get("username"),
                "role": u.get("role") or "user",
                "revenda": u.get("revenda") or "",
            }
        )
    return {"users": out}


@app.post("/auth/users")
def auth_create_user(body: dict, request: Request):
    scope = _auth_scope_from_request(request)
    if scope.get("role") != "admin":
        raise HTTPException(status_code=403, detail="forbidden")

    username = _norm_text(body.get("username"))
    password = _norm_text(body.get("password"))
    role = _norm_text(body.get("role") or "user")
    revenda = _norm_text(body.get("revenda") or "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="username e password são obrigatórios")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role inválida")
    if role == "admin":
        revenda = "*"

    with _USERS_LOCK:
        users = _load_users()
        for u in users:
            if u.get("username") == username:
                raise HTTPException(status_code=409, detail="usuário já existe")

        users.append(
            {
                "username": username,
                "password_hash": _hash_password(password),
                "role": role,
                "revenda": revenda,
            }
        )
        _save_users(users)

    return {"status": "ok"}


def _get_ads_canonical():
    from dashboard_data import REVENDEDORES_IDS

    uid_to_name = {uid: name for name, uid in (REVENDEDORES_IDS or {}).items()}
    with _ADS_LOCK:
        store = _load_ads_store()
        raw = store.get("revendas", {}) or {}

    merged: dict[str, dict] = {}
    canon_ids: dict[str, int] = {}

    for raw_name, rev_map in raw.items():
        uid = _extract_user_id_from_ads_name(raw_name)
        canon = uid_to_name.get(uid) if uid else None
        if not canon:
            canon = _canonicalize_revenda_name(raw_name)
        if not canon:
            continue
        if uid and canon not in canon_ids:
            canon_ids[canon] = uid
        merged.setdefault(canon, {})
        for d, amt in (rev_map or {}).items():
            try:
                v = float(amt or 0)
            except Exception:
                v = 0.0
            merged[canon][d] = round(float(merged[canon].get(d, 0) or 0) + v, 2)

    return merged, canon_ids


def _extract_user_id_from_ads_name(name: str):
    m = re.search(r"(?:-|\s)\s*(\d{3,8})\s*$", name or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


@app.get("/api/data")
def get_data(days: int = 7, period: str | None = None, force: int = 0, request: Request = None):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role")
    user_rev = scope.get("revenda")
    period_key = _period_cache_key(period, days)
    cache_key = f"days:{period_key}"
    now = time.time()

    if cache_key in _CACHE and (now - _CACHE[cache_key]["ts"]) < _CACHE_TTL_SECONDS and not force:
        data = _CACHE[cache_key]["data"].copy()
        data["meta"] = {"source": "memory", "refreshing": False, "ts": _CACHE[cache_key]["ts"]}
        if user_role != "admin" and user_rev and isinstance(data.get("charts", {}).get("ranking"), list):
            rows = [r for r in data["charts"]["ranking"] if r.get("revenda") == user_rev]
            data["charts"]["ranking"] = rows
            s = {"total_clientes": 0, "ativos_reais": 0, "testes_ativos": 0, "novos_clientes": 0, "vendas": 0}
            for r in rows:
                s["vendas"] += int(r.get("vendas", 0) or 0)
            data["summary"]["vendas"] = s["vendas"]
        return data

    disk_cache = _load_data_cache()
    disk_entry = disk_cache.get(cache_key)

    if force and disk_entry:
        if cache_key not in _REFRESH_THREADS or not _REFRESH_THREADS[cache_key].is_alive():
            import threading as _th

            def _refresh_worker():
                try:
                    payload = _compute_payload(days, period)
                    _CACHE[cache_key] = {"ts": time.time(), "data": payload}
                    disk_cache[cache_key] = {"ts": _CACHE[cache_key]["ts"], "data": payload}
                    _save_data_cache(disk_cache)
                except Exception:
                    logger.exception("Erro ao atualizar payload principal em background.")

            t = _th.Thread(target=_refresh_worker, daemon=True)
            _REFRESH_THREADS[cache_key] = t
            t.start()

        resp = disk_entry["data"].copy()
        resp["meta"] = {"source": "disk", "refreshing": True, "ts": disk_entry.get("ts")}
        return resp

    payload = _compute_payload(days, period)
    _CACHE[cache_key] = {"ts": time.time(), "data": payload}
    disk_cache[cache_key] = {"ts": _CACHE[cache_key]["ts"], "data": payload}
    _save_data_cache(disk_cache)

    data = payload.copy()
    data["meta"] = {"source": "fresh", "refreshing": False, "ts": _CACHE[cache_key]["ts"]}

    if user_role != "admin" and user_rev and isinstance(data.get("charts", {}).get("ranking"), list):
        rows = [r for r in data["charts"]["ranking"] if r.get("revenda") == user_rev]
        data["charts"]["ranking"] = rows
        s = {"vendas": 0}
        for r in rows:
            s["vendas"] += int(r.get("vendas", 0) or 0)
        data["summary"]["vendas"] = s["vendas"]

    return data


def _compute_payload(days: int, period: str | None = None):
    from dashboard_data import REVENDEDORES_IDS

    start_d, end_d, normalized_period = _get_period_bounds(period, days)
    ranking_days = 1 if normalized_period in {"today", "yesterday"} else max(int(normalized_period), 1)

    try:
        ranking, _ = get_ranking(ranking_days, REVENDEDORES_IDS, period=normalized_period)
        if not isinstance(ranking, list):
            ranking = []
    except Exception:
        logger.exception("Erro ao buscar ranking.")
        ranking = []

    try:
        ativos_info = get_ativos(REVENDEDORES_IDS)
        if not isinstance(ativos_info, list):
            ativos_info = []
    except Exception:
        logger.exception("Erro ao buscar ativos em _compute_payload.")
        ativos_info = []

    ativos_map = {
        item.get("name"): item
        for item in ativos_info
        if isinstance(item, dict) and item.get("name")
    }

    ads_revendas, _ = _get_ads_canonical()
    detalhamento_completo = []

    ranking_map = {
        item.get("revenda"): item
        for item in (ranking or [])
        if isinstance(item, dict) and item.get("revenda")
    }

    for name in REVENDEDORES_IDS.keys():
        rank = ranking_map.get(
            name,
            {
                "vendas": 0,
                "conversoes": 0,
                "testes": 0,
                "renovacoes": 0,
                "conversao": 0,
            },
        )
        ativos = ativos_map.get(
            name,
            {
                "total_clientes": 0,
                "ativos_reais": 0,
                "testes_ativos": 0,
                "novos_clientes": 0,
            },
        )

        rev_map = ads_revendas.get(name, {})
        ads_today = float(rev_map.get(date.today().isoformat(), 0) or 0)
        ads_period = _ads_amounts_for_revenda_in_range(rev_map, start_d, end_d)
        sales_total = int(rank.get("vendas", 0) or 0) + int(rank.get("conversoes", 0) or 0)
        cost_per_sale = round((ads_period / sales_total), 2) if sales_total > 0 else 0.0

        detalhamento_completo.append(
            {
                "revenda": name,
                "vendas": int(rank.get("vendas", 0) or 0),
                "conversoes": int(rank.get("conversoes", 0) or 0),
                "testes": int(rank.get("testes", 0) or 0),
                "renovacoes": int(rank.get("renovacoes", 0) or 0),
                "conversao": float(rank.get("conversao", 0) or 0),
                "total_clientes": int(ativos.get("total_clientes", 0) or 0),
                "ativos_reais": int(ativos.get("ativos_reais", 0) or 0),
                "testes_ativos_reais": int(ativos.get("testes_ativos", 0) or 0),
                "novos_clientes": int(ativos.get("novos_clientes", 0) or 0),
                "ads_today": round(ads_today, 2),
                "ads_period": round(ads_period, 2),
                "sales_total": sales_total,
                "cost_per_sale": cost_per_sale,
            }
        )

    detalhamento_completo.sort(key=lambda x: x["vendas"], reverse=True)

    total_clientes = sum(int(item.get("total_clientes", 0) or 0) for item in ativos_info if isinstance(item, dict))
    ativos_reais = sum(int(item.get("ativos_reais", 0) or 0) for item in ativos_info if isinstance(item, dict))
    testes_ativos = sum(int(item.get("testes_ativos", 0) or 0) for item in ativos_info if isinstance(item, dict))
    novos_clientes = sum(int(item.get("novos_clientes", 0) or 0) for item in ativos_info if isinstance(item, dict))
    total_vendas = sum(int(item.get("vendas", 0) or 0) for item in ranking if isinstance(item, dict))

    ads_today_total = round(
        sum(float((ads_revendas.get(name, {}) or {}).get(date.today().isoformat(), 0) or 0) for name in REVENDEDORES_IDS.keys()),
        2,
    )

    ads_period_total = round(
        sum(
            _ads_amounts_for_revenda_in_range((ads_revendas.get(name, {}) or {}), start_d, end_d)
            for name in REVENDEDORES_IDS.keys()
        ),
        2,
    )

    return {
        "summary": {
            "total_clientes": total_clientes,
            "ativos_reais": ativos_reais,
            "testes_ativos": testes_ativos,
            "novos_clientes": novos_clientes,
            "vendas": total_vendas,
            "ads_today": ads_today_total,
            "ads_period": ads_period_total,
            "period_start": start_d.isoformat(),
            "period_end": end_d.isoformat(),
            "period_label": normalized_period,
        },
        "charts": {
            "ranking": detalhamento_completo
        },
    }


@app.get("/api/ads/all")
def get_ads_all(days: int = 7, period: str | None = None, request: Request = None):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""

    start_d, end_d, normalized_period = _get_period_bounds(period, days)

    ads_revendas, _ = _get_ads_canonical()
    from dashboard_data import REVENDEDORES_IDS

    names = sorted(REVENDEDORES_IDS.keys())
    if user_role != "admin" and user_rev and user_rev != "*":
        names = [n for n in names if n == user_rev]

    rows = []
    ads_period_total = 0.0
    for name in names:
        rev_map = ads_revendas.get(name, {}) or {}
        ads_period = _ads_amounts_for_revenda_in_range(rev_map, start_d, end_d)
        ads_period_total += ads_period
        rows.append({"revenda": name, "ads_period": round(ads_period, 2)})

    return {
        "summary": {
            "ads_period": round(ads_period_total, 2),
            "period_start": start_d.isoformat(),
            "period_end": end_d.isoformat(),
            "period_label": normalized_period,
        },
        "rows": rows,
    }


@app.get("/api/partials/ativos")
def partial_ativos(force: int = 0, days: int = 7, period: str | None = None, request: Request = None):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""
    cache = _load_data_cache()
    key = f"ativos:{_period_cache_key(period, days)}"
    now = time.time()

    if not force and key in _CACHE and (now - _CACHE[key]["ts"]) < _CACHE_TTL_SECONDS:
        data = _CACHE[key]["data"]
        return {"ativos": _apply_scope_to_ativos(data, user_role, user_rev), "source": "memory"}

    if not force and key in cache:
        if key not in _REFRESH_THREADS or not _REFRESH_THREADS[key].is_alive():
            import threading as _th

            def _refresh():
                from dashboard_data import REVENDEDORES_IDS
                try:
                    data2 = get_ativos(REVENDEDORES_IDS)
                    logger.info("Refresh /api/partials/ativos retornou %s registros.", len(data2) if isinstance(data2, list) else "inválido")
                    if _valid_ativos(data2):
                        _CACHE[key] = {"ts": time.time(), "data": data2}
                        cache[key] = {"ts": _CACHE[key]["ts"], "data": data2}
                        _save_data_cache(cache)
                        logger.info("Cache de ativos atualizado com sucesso.")
                    else:
                        logger.warning("Refresh de ativos retornou vazio/inválido; cache anterior mantido.")
                except Exception:
                    logger.exception("Erro ao atualizar ativos em background.")

            t = _th.Thread(target=_refresh, daemon=True)
            _REFRESH_THREADS[key] = t
            t.start()

        entry = cache.get(key)
        data = entry.get("data") if isinstance(entry, dict) else entry
        return {"ativos": _apply_scope_to_ativos(data, user_role, user_rev), "source": "disk"}

    from dashboard_data import REVENDEDORES_IDS
    try:
        data = get_ativos(REVENDEDORES_IDS)
        logger.info("get_ativos retornou %s registros.", len(data) if isinstance(data, list) else "inválido")

        if not _valid_ativos(data):
            raise HTTPException(status_code=502, detail="get_ativos retornou vazio ou formato inválido")

        _CACHE[key] = {"ts": time.time(), "data": data}
        cache[key] = {"ts": _CACHE[key]["ts"], "data": data}
        _save_data_cache(cache)

        return {"ativos": _apply_scope_to_ativos(data, user_role, user_rev), "source": "fresh"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro ao buscar ativos.")
        raise HTTPException(status_code=500, detail=f"erro ao buscar ativos: {str(e)}")


@app.get("/api/partials/logs")
def partial_logs(days: int = 7, period: str | None = None, force: int = 0, request: Request = None):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""
    cache = _load_data_cache()
    period_key = _period_cache_key(period, days)
    key = f"logs:{period_key}"
    now = time.time()
    _, _, normalized_period = _get_period_bounds(period, days)
    ranking_days = 1 if normalized_period in {"today", "yesterday"} else max(int(normalized_period), 1)

    if not force and key in _CACHE and (now - _CACHE[key]["ts"]) < _CACHE_TTL_SECONDS:
        data = _CACHE[key]["data"]
        ranking = data.get("ranking", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if user_role != "admin" and user_rev and user_rev != "*":
            ranking = [r for r in ranking if r.get("revenda") == user_rev]
        return {"ranking": ranking, "discovered_ids": {}, "source": "memory"}

    if not force and key in cache:
        if key not in _REFRESH_THREADS or not _REFRESH_THREADS[key].is_alive():
            import threading as _th

            def _refresh():
                from dashboard_data import REVENDEDORES_IDS
                try:
                    ranking2, _ = get_ranking(ranking_days, REVENDEDORES_IDS, period=normalized_period)
                    payload2 = {"ranking": ranking2, "discovered_ids": {}}
                    _CACHE[key] = {"ts": time.time(), "data": payload2}
                    cache[key] = {"ts": _CACHE[key]["ts"], "data": payload2}
                    _save_data_cache(cache)
                except Exception:
                    logger.exception("Erro ao atualizar logs em background.")

            t = _th.Thread(target=_refresh, daemon=True)
            _REFRESH_THREADS[key] = t
            t.start()

        entry = cache.get(key)
        data = entry.get("data") if isinstance(entry, dict) else entry
        ranking = data.get("ranking", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if user_role != "admin" and user_rev and user_rev != "*":
            ranking = [r for r in ranking if r.get("revenda") == user_rev]
        return {"ranking": ranking, "discovered_ids": {}, "source": "disk"}

    from dashboard_data import REVENDEDORES_IDS
    ranking, _ = get_ranking(ranking_days, REVENDEDORES_IDS, period=normalized_period)
    payload = {"ranking": ranking, "discovered_ids": {}}
    _CACHE[key] = {"ts": time.time(), "data": payload}
    cache[key] = {"ts": _CACHE[key]["ts"], "data": payload}
    _save_data_cache(cache)

    if user_role != "admin" and user_rev and user_rev != "*":
        payload["ranking"] = [r for r in payload["ranking"] if r.get("revenda") == user_rev]
    payload["source"] = "fresh"
    return payload


@app.get("/api/ads/summary")
def get_ads_summary(revenda: str, request: Request = None):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""

    revenda = _canonicalize_revenda_name(revenda)
    if user_role != "admin" and user_rev and user_rev != "*" and revenda != user_rev:
        raise HTTPException(status_code=403, detail="forbidden")

    today = date.today()
    week_start, week_end = _week_range(today)
    month_start, month_end = _month_range(today)
    ads_revendas, _ = _get_ads_canonical()
    rev_map = ads_revendas.get(revenda, {}) or {}

    def amount_for(day: date) -> float:
        return float(rev_map.get(day.isoformat(), 0) or 0)

    week_days = []
    week_total = 0.0
    for i in range(7):
        day = week_start + timedelta(days=i)
        amt = amount_for(day)
        week_total += amt
        week_days.append({"date": day.isoformat(), "amount": amt})

    month_days = []
    month_total = 0.0
    cur = month_start
    while cur <= month_end:
        amt = amount_for(cur)
        month_total += amt
        if amt:
            month_days.append({"date": cur.isoformat(), "amount": amt})
        cur += timedelta(days=1)

    return {
        "revenda": revenda,
        "week": {
            "start": week_start.isoformat(),
            "end": week_end.isoformat(),
            "total": round(week_total, 2),
            "days": week_days,
        },
        "month": {
            "start": month_start.isoformat(),
            "end": month_end.isoformat(),
            "total": round(month_total, 2),
            "days": month_days,
        },
    }


@app.post("/api/ads/entries")
def upsert_ads_entries(payload: AdsEntriesPayload, request: Request):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""

    try:
        revenda = _canonicalize_revenda_name(payload.revenda)
        if not revenda:
            raise ValueError("revenda vazia")
        parsed_entries = [(_parse_date(e.date), float(e.amount)) for e in payload.entries]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if user_role != "admin" and user_rev and user_rev != "*" and revenda != user_rev:
        raise HTTPException(status_code=403, detail="forbidden")

    with _ADS_LOCK:
        store = _load_ads_store()
        store.setdefault("revendas", {})
        store["revendas"].setdefault(revenda, {})
        rev_map = store["revendas"][revenda]

        for day, amount in parsed_entries:
            key = day.isoformat()
            if amount <= 0:
                if key in rev_map:
                    del rev_map[key]
            else:
                rev_map[key] = round(amount, 2)

        _save_ads_store(store)

    return {"status": "ok"}


def _parse_brl_to_float(text: str) -> float:
    t = text.replace("R$", "").replace(".", "").replace(" ", "").replace("\u00A0", "")
    t = t.replace(",", ".")
    try:
        return float(t)
    except Exception:
        return 0.0


def _normalize_revenda_name(header: str) -> str:
    h = header.strip()
    if h.upper().startswith("ADS -"):
        return _canonicalize_revenda_name("ADS - " + h.split("ADS -", 1)[1].strip())

    parts = h.split()
    name_parts = []
    for p in parts:
        if any(ch.isdigit() for ch in p):
            break
        name_parts.append(p)
    base = " ".join(name_parts).strip()
    if not base:
        base = h
    return _canonicalize_revenda_name(f"ADS - {base}")


@app.post("/api/ads/ingest-txt")
def ingest_ads_txt(payload: AdsIngestTxt, request: Request):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""

    try:
        day = _parse_date(payload.date)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Data inválida (use YYYY-MM-DD): {e}")

    lines = [ln.rstrip() for ln in payload.content.splitlines()]
    blocks = []
    cur = []
    for ln in lines:
        if ln.strip() == "" and cur:
            blocks.append(cur)
            cur = []
        else:
            if ln.strip() != "":
                cur.append(ln)
    if cur:
        blocks.append(cur)

    parsed = []
    for blk in blocks:
        header = blk[0] if blk else ""
        revenda = _normalize_revenda_name(header)
        total_line = next((l for l in blk if "Total a pagar" in l or "Total a Pagar" in l), "")
        if not total_line:
            continue
        val_str = total_line.split("R$", 1)[-1].strip()
        amount = _parse_brl_to_float(val_str)
        parsed.append({"revenda": revenda, "date": day.isoformat(), "amount": round(amount, 2)})

    if user_role != "admin" and user_rev and user_rev != "*":
        parsed = [it for it in parsed if it.get("revenda") == user_rev]
        if payload.save and not parsed:
            raise HTTPException(status_code=403, detail="forbidden")

    if payload.save and parsed:
        with _ADS_LOCK:
            store = _load_ads_store()
            store.setdefault("revendas", {})
            for item in parsed:
                rev = item["revenda"]
                d = item["date"]
                amt = item["amount"]
                store["revendas"].setdefault(rev, {})
                if amt <= 0:
                    store["revendas"][rev].pop(d, None)
                else:
                    store["revendas"][rev][d] = amt
            _save_ads_store(store)

    return {"parsed": parsed, "saved": bool(payload.save)}


@app.post("/api/ads/bulk-entries")
def bulk_ads_entries(payload: list[AdsEntriesPayload], request: Request):
    scope = _auth_scope_from_request(request)
    user_role = scope.get("role") or "user"
    user_rev = scope.get("revenda") or ""

    if not isinstance(payload, list) or not payload:
        raise HTTPException(status_code=400, detail="payload inválido")

    agg: dict[str, dict[str, float]] = {}
    for item in payload:
        rev = _canonicalize_revenda_name(item.revenda or "")
        if not rev:
            continue
        if user_role != "admin" and user_rev and user_rev != "*" and rev != user_rev:
            continue
        for e in item.entries:
            try:
                d = _parse_date(e.date).isoformat()
                amt = float(e.amount)
            except Exception:
                continue
            agg.setdefault(rev, {})
            agg[rev][d] = round(agg[rev].get(d, 0.0) + amt, 2)

    if user_role != "admin" and user_rev and user_rev != "*" and not agg:
        raise HTTPException(status_code=403, detail="forbidden")

    updated = 0
    with _ADS_LOCK:
        store = _load_ads_store()
        store.setdefault("revendas", {})
        for rev, days in agg.items():
            store["revendas"].setdefault(rev, {})
            for d, amt in days.items():
                if amt <= 0:
                    if d in store["revendas"][rev]:
                        del store["revendas"][rev][d]
                else:
                    store["revendas"][rev][d] = amt
                    updated += 1
        _save_ads_store(store)

    return {"status": "ok", "updated": updated, "revendas": len(agg)}


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.environ.get("PORT", "8504"))
    except Exception:
        port = 8504
    uvicorn.run(app, host=host, port=port)
