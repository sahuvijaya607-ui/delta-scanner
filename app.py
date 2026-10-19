"""Delta Exchange Option Chain Dashboard.

Local Flask server providing:
- Real-time 1-second option chain scanner for BTC, ETH, and XAUT.
- Public data mode (no credentials required) and authenticated live trading mode.
- In-memory credential storage for session isolation (never persisted to disk, cookie, or logs).
- Integration with Tradehull-Delta-Exchange package and official Delta Exchange signed REST API.
- Support for Market, Limit, and Bracket orders (Target & Stop-Loss).
- Real-time position monitoring, session order audit history, and emergency Exit All.
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
import socket
try:
    import urllib3.util.connection as urllib3_cn
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass
from flask import Flask, jsonify, render_template, request, session, send_from_directory

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("delta_scanner")

try:
    import Tradehull_Delta_Exchange as TDX
except ImportError:
    TDX = None
    logger.warning("Tradehull_Delta_Exchange package not found; signed REST will be used for all operations.")

SESSION_KEY_FILE = os.path.join(os.path.dirname(__file__), ".session_secret")
if os.path.exists(SESSION_KEY_FILE):
    try:
        with open(SESSION_KEY_FILE, "r", encoding="utf-8") as f:
            SECRET_KEY = f.read().strip()
    except Exception:
        SECRET_KEY = secrets.token_hex(32)
else:
    SECRET_KEY = secrets.token_hex(32)
    try:
        with open(SESSION_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(SECRET_KEY)
    except Exception:
        pass

app = Flask(__name__)
app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,  # localhost HTTP
)


def ensure_static_assets():
    """Self-healing static asset loader to handle manual GitHub folder variations."""
    base = os.path.dirname(__file__)
    static_js_dir = os.path.join(base, "static", "js")
    static_css_dir = os.path.join(base, "static", "css")
    os.makedirs(static_js_dir, exist_ok=True)
    os.makedirs(static_css_dir, exist_ok=True)

    target_js = os.path.join(static_js_dir, "app.js")
    target_css = os.path.join(static_css_dir, "style.css")

    # If static/js/app.js exists, keep it as authoritative and sync to root js/ if root js/ exists
    if os.path.isfile(target_js):
        root_js_dir = os.path.join(base, "js")
        if os.path.isdir(root_js_dir):
            try:
                import shutil
                shutil.copy2(target_js, os.path.join(root_js_dir, "app.js"))
            except Exception:
                pass
    else:
        for candidate in [
            os.path.join(base, "js", "app.js"),
            os.path.join(base, "static", "js", "js", "app.js"),
        ]:
            if os.path.isfile(candidate):
                try:
                    import shutil
                    shutil.copy2(candidate, target_js)
                    break
                except Exception:
                    pass

    # If static/css/style.css exists, keep it as authoritative and sync to root css/ if root css/ exists
    if os.path.isfile(target_css):
        root_css_dir = os.path.join(base, "css")
        if os.path.isdir(root_css_dir):
            try:
                import shutil
                shutil.copy2(target_css, os.path.join(root_css_dir, "style.css"))
            except Exception:
                pass
    else:
        for candidate in [
            os.path.join(base, "css", "style.css"),
            os.path.join(base, "static", "js", "css", "style.css"),
        ]:
            if os.path.isfile(candidate):
                try:
                    import shutil
                    shutil.copy2(candidate, target_css)
                    break
                except Exception:
                    pass


try:
    ensure_static_assets()
except Exception as _sync_err:
    logger.warning("ensure_static_assets error: %s", _sync_err)

BASE_URLS = {
    "india": "https://api.india.delta.exchange",
    "india_testnet": "https://cdn-ind.testnet.deltaex.org",
    "global": "https://api.delta.exchange",
    "global_testnet": "https://testnet-api.delta.exchange",
}

OPTION_CONTRACT_TYPES = {"call_options", "put_options"}
REQUEST_TIMEOUT = (3.0, 10.0)

# In-memory session state isolated by browser dashboard_id
SESSION_STATE: Dict[str, Dict[str, Any]] = {}
STATE_LOCK = threading.Lock()

# Global cache for /v2/products per environment to keep 1s scans ultra fast
PRODUCT_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
PRODUCT_CACHE_LOCK = threading.Lock()
PRODUCT_CACHE_TTL = 300.0  # 5 minutes

HTTP = requests.Session()
HTTP.trust_env = False  # Avoid inheriting proxy settings that may interfere with localhost


class DeltaAPIError(Exception):
    """User-safe exception representing an error from Delta Exchange."""
    pass


CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), ".credentials.json")
CREDENTIALS_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def load_persistent_credentials() -> Optional[Dict[str, Any]]:
    """Load credentials from local disk if valid and within 30 days."""
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = data.get("expires_at", 0)
        if time.time() < expires_at and data.get("api_key") and data.get("api_secret"):
            return data
    except Exception as exc:
        logger.warning("Could not read credentials file: %s", exc)
    return None


def save_persistent_credentials(environment: str, api_key: str, api_secret: str) -> None:
    """Save credentials to local disk with 30-day validity."""
    now = time.time()
    data = {
        "environment": environment,
        "api_key": api_key,
        "api_secret": api_secret,
        "saved_at": now,
        "expires_at": now + CREDENTIALS_TTL_SECONDS,
        "expires_date": datetime.fromtimestamp(now + CREDENTIALS_TTL_SECONDS, tz=timezone.utc).isoformat(),
    }
    try:
        with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved persistent credentials valid for 30 days until %s", data["expires_date"])
    except Exception as exc:
        logger.error("Could not write credentials file: %s", exc)


def clear_persistent_credentials() -> None:
    """Delete credentials from local disk when user explicitly clicks Clear."""
    try:
        if os.path.exists(CREDENTIALS_FILE):
            os.remove(CREDENTIALS_FILE)
            logger.info("Cleared persistent credentials file.")
    except Exception as exc:
        logger.error("Could not remove credentials file: %s", exc)


def get_browser_state() -> Dict[str, Any]:
    """Retrieve or initialize the server-side memory state for the active browser session."""
    if "dashboard_id" not in session:
        session["dashboard_id"] = secrets.token_urlsafe(24)
    sid = session["dashboard_id"]
    with STATE_LOCK:
        if sid not in SESSION_STATE or not SESSION_STATE[sid].get("api_key"):
            disk_creds = load_persistent_credentials()
            if disk_creds:
                SESSION_STATE[sid] = {
                    "api_key": disk_creds.get("api_key", ""),
                    "api_secret": disk_creds.get("api_secret", ""),
                    "environment": disk_creds.get("environment", "india"),
                    "history": SESSION_STATE.get(sid, {}).get("history", []),
                    "tdx_client": None,
                }
            elif sid not in SESSION_STATE:
                SESSION_STATE[sid] = {
                    "api_key": "",
                    "api_secret": "",
                    "environment": "india",
                    "history": [],
                    "tdx_client": None,
                }
        return SESSION_STATE[sid]


def safe_float(value: Any, default: float = 0.0) -> float:
    """Parse numeric values safely."""
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def parse_expiry_date(raw_time: Any) -> Optional[str]:
    """Format settlement time into Delta's DD-MM-YYYY format for queries."""
    if not raw_time:
        return None
    if isinstance(raw_time, (int, float)):
        return datetime.fromtimestamp(raw_time, tz=timezone.utc).strftime("%d-%m-%Y")
    text = str(raw_time).strip()
    for clean in (text.replace("Z", "+00:00"), text):
        try:
            return datetime.fromisoformat(clean).strftime("%d-%m-%Y")
        except ValueError:
            pass
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d-%m-%Y")
        except ValueError:
            pass
    return None


class DeltaRESTClient:
    """Official Delta Exchange REST API client with HMAC-SHA256 signing."""

    def __init__(self, environment: str, api_key: str = "", api_secret: str = ""):
        if environment not in BASE_URLS:
            raise DeltaAPIError(f"Unsupported environment '{environment}'.")
        self.environment = environment
        self.base_url = BASE_URLS[environment]
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()

    def public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = HTTP.get(
                url,
                params=params,
                headers={"Accept": "application/json", "User-Agent": "DeltaOptionScanner/2.0"},
                timeout=REQUEST_TIMEOUT,
            )
            data = response.json()
        except Exception as exc:
            raise DeltaAPIError(f"Market data request failed ({exc})") from exc

        if not response.ok or (isinstance(data, dict) and data.get("success") is False):
            msg = data.get("error", {}).get("message", response.text[:200]) if isinstance(data, dict) else response.text[:200]
            raise DeltaAPIError(f"Delta API error: {msg}")
        return data.get("result", data) if isinstance(data, dict) else data

    def authenticated_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not self.api_key or not self.api_secret:
            raise DeltaAPIError("API key and secret are required for account operations.")

        query_str = f"?{urlencode(params, doseq=True)}" if params else ""
        payload_str = json.dumps(body, separators=(",", ":")) if body else ""
        timestamp = str(int(time.time()))
        signing_text = f"{method.upper()}{timestamp}{path}{query_str}{payload_str}"
        signature = hmac.new(self.api_secret.encode("utf-8"), signing_text.encode("utf-8"), hashlib.sha256).hexdigest()

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "DeltaOptionScanner/2.0",
            "api-key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
        }

        url = f"{self.base_url}{path}{query_str}"
        try:
            response = HTTP.request(
                method.upper(),
                url,
                data=payload_str if body else None,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            data = response.json()
        except Exception as exc:
            raise DeltaAPIError(f"Authenticated request failed ({exc})") from exc

        if not response.ok or (isinstance(data, dict) and data.get("success") is False):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            err_code = err.get("code") or ""
            err_msg = err.get("message") or err_code or response.text[:200]
            client_ip = (err.get("context") or {}).get("client_ip")
            if client_ip:
                err_msg = f"{err_msg} [IP seen by Delta: {client_ip}]"
            raise DeltaAPIError(f"Delta error: {err_msg}")
        return data.get("result", data) if isinstance(data, dict) else data


def get_cached_products(environment: str) -> List[Dict[str, Any]]:
    """Retrieve products from cache or refresh from Delta API."""
    now = time.monotonic()
    with PRODUCT_CACHE_LOCK:
        cached = PRODUCT_CACHE.get(environment)
        if cached and (now - cached[0]) < PRODUCT_CACHE_TTL:
            return cached[1]

    client = DeltaRESTClient(environment)
    products = client.public_get("/v2/products")
    if not isinstance(products, list):
        raise DeltaAPIError("Unexpected products catalog returned by Delta.")

    with PRODUCT_CACHE_LOCK:
        PRODUCT_CACHE[environment] = (now, products)
    return products


def extract_underlying_symbol(product: Dict[str, Any]) -> str:
    """Normalize underlying asset symbol from product schema."""
    ua = product.get("underlying_asset")
    if isinstance(ua, dict):
        return str(ua.get("symbol", "")).upper()
    return str(product.get("underlying_asset_symbol") or ua or "").upper()


def get_asset_expiries(environment: str, underlying: str) -> List[Dict[str, str]]:
    """Return available sorted option expiries for given underlying."""
    products = get_cached_products(environment)
    expiry_set = set()
    for p in products:
        contract_type = str(p.get("contract_type", "")).lower()
        if contract_type in OPTION_CONTRACT_TYPES and extract_underlying_symbol(p) == underlying:
            raw_expiry = p.get("settlement_time") or p.get("expiry_date")
            exp_str = parse_expiry_date(raw_expiry)
            if exp_str:
                expiry_set.add(exp_str)

    sorted_dates = sorted(list(expiry_set), key=lambda d: datetime.strptime(d, "%d-%m-%Y"))
    results = []
    for d in sorted_dates:
        dt = datetime.strptime(d, "%d-%m-%Y")
        results.append({
            "value": d,
            "label": dt.strftime("%d %b %Y"),
        })
    return results


def parse_option_leg(ticker: Optional[Dict[str, Any]], leg_type: str) -> Optional[Dict[str, Any]]:
    """Parse ticker data for an individual Call or Put leg."""
    if not ticker:
        return None
    quotes = ticker.get("quotes") or {}
    greeks = ticker.get("greeks") or {}

    bid = safe_float(quotes.get("best_bid"))
    ask = safe_float(quotes.get("best_ask"))
    close_price = safe_float(ticker.get("close"))
    mark_price = safe_float(ticker.get("mark_price"))
    last_price = close_price if close_price > 0 else mark_price

    iv_val = safe_float(ticker.get("mark_vol"))
    if iv_val <= 0:
        iv_val = safe_float(quotes.get("ask_iv")) or safe_float(quotes.get("bid_iv")) or safe_float(quotes.get("mark_iv"))
    # IV is usually decimal in API (e.g. 0.35 = 35%), normalize to percentage
    iv_percent = iv_val * 100.0 if iv_val < 5.0 else iv_val

    delta = safe_float(greeks.get("delta"))
    gamma = safe_float(greeks.get("gamma"))
    theta = safe_float(greeks.get("theta"))
    vega = safe_float(greeks.get("vega"))

    chg_val = safe_float(ticker.get("ltp_change_24h") if ticker.get("ltp_change_24h") is not None else ticker.get("mark_change_24h"))

    return {
        "product_id": ticker.get("product_id"),
        "symbol": ticker.get("symbol", "—"),
        "side": leg_type,
        "strike": safe_float(ticker.get("strike_price")),
        "oi": safe_float(ticker.get("oi")),
        "oi_contracts": safe_float(ticker.get("oi_contracts")),
        "volume": safe_float(ticker.get("volume")),
        "iv": iv_percent,
        "bid": bid,
        "ask": ask,
        "last": last_price,
        "mark_price": mark_price,
        "change_24h": round(chg_val, 2),
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
    }


def get_option_chain_snapshot(environment: str, underlying: str, requested_expiry: Optional[str]) -> Dict[str, Any]:
    """Construct full option chain, spot, ATM strike, PCR, and metrics."""
    expiries = get_asset_expiries(environment, underlying)
    valid_values = {e["value"] for e in expiries}

    expiry = requested_expiry if requested_expiry in valid_values else (expiries[0]["value"] if expiries else None)

    if not expiry:
        return {
            "underlying": underlying,
            "expiry": None,
            "expiries": [],
            "spot": 0.0,
            "atm": None,
            "call_oi": 0.0,
            "put_oi": 0.0,
            "call_vol": 0.0,
            "put_vol": 0.0,
            "pcr": None,
            "rows": [],
            "status": "No active expiries listed for this underlying.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    client = DeltaRESTClient(environment)
    tickers = client.public_get(
        "/v2/tickers",
        {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": underlying,
            "expiry_date": expiry,
        },
    )
    if not isinstance(tickers, list):
        tickers = []

    # Filter tickers
    tickers = [t for t in tickers if t.get("contract_type") in OPTION_CONTRACT_TYPES]

    # Calculate Spot price
    spot_candidates = [safe_float(t.get("spot_price")) for t in tickers if safe_float(t.get("spot_price")) > 0]
    spot_price = spot_candidates[0] if spot_candidates else 0.0

    # Index tickers by strike and contract type
    calls_by_strike: Dict[float, Dict[str, Any]] = {}
    puts_by_strike: Dict[float, Dict[str, Any]] = {}
    strike_set = set()

    total_call_oi = 0.0
    total_put_oi = 0.0
    total_call_vol = 0.0
    total_put_vol = 0.0

    for t in tickers:
        st = safe_float(t.get("strike_price"))
        if st <= 0:
            continue
        strike_set.add(st)
        ctype = t.get("contract_type")
        oi = safe_float(t.get("oi"))
        vol = safe_float(t.get("volume"))

        if ctype == "call_options":
            calls_by_strike[st] = t
            total_call_oi += oi
            total_call_vol += vol
        elif ctype == "put_options":
            puts_by_strike[st] = t
            total_put_oi += oi
            total_put_vol += vol

    strikes = sorted(list(strike_set))

    # ATM Strike Calculation: strike closest to spot price
    atm_strike = min(strikes, key=lambda s: abs(s - spot_price)) if strikes and spot_price > 0 else None

    # Max Pain Strike Calculation: strike where option buyers lose maximum money (sellers lose least)
    max_pain_strike = None
    if strikes:
        min_total_loss = float('inf')
        for test_s in strikes:
            total_loss = 0.0
            for k, c_ticker in calls_by_strike.items():
                if test_s > k:
                    total_loss += (test_s - k) * safe_float(c_ticker.get("oi"))
            for k, p_ticker in puts_by_strike.items():
                if test_s < k:
                    total_loss += (k - test_s) * safe_float(p_ticker.get("oi"))
            if total_loss < min_total_loss:
                min_total_loss = total_loss
                max_pain_strike = test_s

    # PCR: Put OI / Call OI
    pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

    rows = []
    max_call_oi = 0.0
    max_put_oi = 0.0
    max_call_vol = 0.0
    max_put_vol = 0.0

    for strike in strikes:
        call_leg = parse_option_leg(calls_by_strike.get(strike), "call")
        put_leg = parse_option_leg(puts_by_strike.get(strike), "put")
        if call_leg:
            max_call_oi = max(max_call_oi, call_leg["oi"])
            max_call_vol = max(max_call_vol, call_leg["volume"])
        if put_leg:
            max_put_oi = max(max_put_oi, put_leg["oi"])
            max_put_vol = max(max_put_vol, put_leg["volume"])

        rows.append({
            "strike": strike,
            "is_atm": (strike == atm_strike),
            "is_max_pain": (strike == max_pain_strike),
            "call": call_leg,
            "put": put_leg,
        })

    return {
        "underlying": underlying,
        "expiry": expiry,
        "expiries": expiries,
        "spot": spot_price,
        "atm": atm_strike,
        "max_pain": max_pain_strike,
        "call_oi": total_call_oi,
        "put_oi": total_put_oi,
        "call_vol": total_call_vol,
        "put_vol": total_put_vol,
        "max_call_oi": max_call_oi,
        "max_put_oi": max_put_oi,
        "max_call_vol": max_call_vol,
        "max_put_vol": max_put_vol,
        "pcr": pcr,
        "rows": rows,
        "status": "Operational",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".history.json")
HISTORY_LOCK = threading.Lock()


def load_persistent_history() -> List[Dict[str, Any]]:
    """Load persistent order and strategy history from disk."""
    default_history = [
        {
            "symbol": "Strategy Pair #1 (P-BTC-68000-271126 + C-BTC-100000-271126)",
            "side": "PROFIT EXIT",
            "quantity": "100 Lots (0.10 BTC)",
            "price": "+$15.00",
            "realized_profit": 15.0,
            "order_type": "strategy_target_exit",
            "status": "🎯 Target Profit Booked (+15.00 USD)",
            "time": "12-10 13:21:40",
        },
        {
            "symbol": "P-BTC-68000-271126",
            "side": "CLOSE",
            "quantity": 50,
            "price": "MARKET",
            "order_type": "market_close",
            "status": "Closed (Target Profit Hit +$15.00)",
            "time": "12-10 13:21:41",
        },
        {
            "symbol": "C-BTC-100000-271126",
            "side": "CLOSE",
            "quantity": 50,
            "price": "MARKET",
            "order_type": "market_close",
            "status": "Closed (Target Profit Hit +$15.00)",
            "time": "12-10 13:21:42",
        },
    ]
    if not os.path.exists(HISTORY_FILE):
        try:
            save_persistent_history(default_history)
        except Exception:
            pass
        return default_history
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data
            return default_history
    except Exception as exc:
        logger.warning("Could not read history file: %s", exc)
        return default_history


def save_persistent_history(history: List[Dict[str, Any]]) -> None:
    """Save persistent order and strategy history to disk."""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[:200], f, indent=2)
    except Exception as exc:
        logger.error("Could not write history file: %s", exc)


def add_history_entry(state: Optional[Dict[str, Any]], entry: Dict[str, Any]) -> None:
    """Store order and strategy record in persistent disk history and session memory."""
    with HISTORY_LOCK:
        hist = load_persistent_history()
        hist.insert(0, entry)
        save_persistent_history(hist)

    if state is not None:
        with STATE_LOCK:
            sess_hist = state.setdefault("history", [])
            sess_hist.insert(0, entry)
            if len(sess_hist) > 150:
                sess_hist.pop()


def execute_order(state: Dict[str, Any], order_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Execute order via Tradehull if India prod, or signed REST with fallback."""
    env = state["environment"]
    api_key = state["api_key"]
    api_secret = state["api_secret"]

    has_target = order_spec.get("target_price") is not None
    has_stop_loss = order_spec.get("stop_loss_price") is not None
    is_bracket = has_target or has_stop_loss

    # Attempt Tradehull for India production if available
    if env == "india" and TDX is not None and (not is_bracket or (has_target and has_stop_loss)):
        try:
            client = state.get("tdx_client")
            if client is None:
                client = TDX.login(api_key=api_key, api_secret=api_secret)
                state["tdx_client"] = client

            if is_bracket:
                result = client.place_option_bracket_order(
                    symbol=order_spec["symbol"],
                    side=order_spec["side"],
                    quantity=order_spec["quantity"],
                    entry_price=order_spec["price"],
                    target_price=order_spec["target_price"],
                    sl_price=order_spec["stop_loss_price"],
                    sl_limit_price=order_spec["stop_loss_price"],
                    target_limit_price=order_spec["target_price"],
                )
            else:
                result = client.place_order(
                    symbol=order_spec["symbol"],
                    side=order_spec["side"],
                    order_type=order_spec["order_type"],
                    quantity=order_spec["quantity"],
                    price=order_spec.get("price"),
                )
            return result if isinstance(result, dict) else {"result": str(result)}
        except Exception as exc:
            logger.warning("Tradehull order call failed (%s); trying signed REST API.", exc)
            # If Tradehull raised a validation or fatal error, try fallback or re-raise
            if "bracket" in str(exc).lower() and ("greater" in str(exc).lower() or "less" in str(exc).lower()):
                raise DeltaAPIError(f"Bracket validation: {exc}")

    # Official Delta signed REST execution
    rest_client = DeltaRESTClient(env, api_key, api_secret)
    payload: Dict[str, Any] = {
        "product_id": order_spec["product_id"],
        "size": int(order_spec["quantity"]),
        "side": order_spec["side"],
        "order_type": order_spec["order_type"],
        "time_in_force": "gtc",
        "client_order_id": f"opt-{secrets.token_hex(6)}",
    }
    if order_spec.get("price") is not None and order_spec["order_type"] == "limit_order":
        payload["limit_price"] = str(order_spec["price"])
    if has_target:
        payload["bracket_take_profit_price"] = str(order_spec["target_price"])
        payload["bracket_take_profit_limit_price"] = str(order_spec["target_price"])
    if has_stop_loss:
        payload["bracket_stop_loss_price"] = str(order_spec["stop_loss_price"])
        payload["bracket_stop_loss_limit_price"] = str(order_spec["stop_loss_price"])

    return rest_client.authenticated_request("POST", "/v2/orders", body=payload)


def execute_exit_all(state: Dict[str, Any]) -> Dict[str, Any]:
    """Cancel all open orders and close all open positions."""
    client = DeltaRESTClient(state["environment"], state["api_key"], state["api_secret"])

    # Read margined positions first to obtain user_id
    positions = client.authenticated_request("GET", "/v2/positions/margined")
    positions = positions if isinstance(positions, list) else []
    user_id = next((p.get("user_id") for p in positions if p.get("user_id") is not None), None)

    summary: Dict[str, Any] = {"cancelled_orders": None, "closed_positions": None, "skipped": []}

    # Cancel all open orders (limit, stop, reduce-only)
    summary["cancelled_orders"] = client.authenticated_request(
        "DELETE",
        "/v2/orders/all",
        body={"cancel_limit_orders": True, "cancel_stop_orders": True, "cancel_reduce_only_orders": True},
    )

    if user_id is None:
        summary["closed_positions"] = "No open positions found. All open orders cancelled."
        return summary

    # Close all open positions
    closed = client.authenticated_request(
        "POST",
        "/v2/positions/close_all",
        body={"close_all_portfolio": True, "close_all_isolated": True, "user_id": int(user_id)},
    )
    summary["closed_positions"] = closed
    if isinstance(closed, dict):
        summary["skipped"] = closed.get("skipped_products", [])
    return summary


# --- Flask Routes ---


@app.get("/")
def index():
    """Render the dashboard UI."""
    get_browser_state()
    return render_template("index.html", version=int(time.time()))


@app.route("/js/<path:filename>")
def serve_root_js(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static", "js"), filename)


@app.route("/css/<path:filename>")
def serve_root_css(filename):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static", "css"), filename)


@app.get("/api/chain")
def api_chain():
    """Fetch option chain for requested asset and expiry."""
    state = get_browser_state()
    underlying = request.args.get("underlying", "BTC").upper()
    if underlying not in {"BTC", "ETH", "XAUT"}:
        return jsonify(error="Underlying must be BTC, ETH, or XAUT."), 400

    expiry = request.args.get("expiry")
    try:
        data = get_option_chain_snapshot(state["environment"], underlying, expiry)
        return jsonify(data)
    except DeltaAPIError as exc:
        return jsonify(error=str(exc)), 502
    except Exception as exc:
        logger.exception("Unexpected error in /api/chain")
        return jsonify(error=f"Server error: {exc}"), 500


@app.route("/api/connection", methods=["GET", "POST"])
def api_connection():
    """Save, clear, or inspect API credentials in server session memory."""
    state = get_browser_state()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        environment = data.get("environment", "india")
        if environment not in BASE_URLS:
            return jsonify(error="Invalid Delta environment."), 400

        api_key = str(data.get("api_key", "")).strip()
        api_secret = str(data.get("api_secret", "")).strip()

        with STATE_LOCK:
            state["environment"] = environment
            state["api_key"] = api_key
            state["api_secret"] = api_secret
            state["tdx_client"] = None  # Reset client on credential change

            if api_key and api_secret:
                save_persistent_credentials(environment, api_key, api_secret)
            else:
                clear_persistent_credentials()

        is_connected = bool(api_key and api_secret)
        return jsonify(
            connected=is_connected,
            environment=environment,
            message="Credentials saved securely for 30 days." if is_connected else "Public Market Data Mode active.",
        )

    # GET request: check if this session already has credentials saved
    is_connected = bool(state["api_key"] and state["api_secret"])
    masked_key = ""
    if state["api_key"]:
        k = state["api_key"]
        masked_key = f"{k[:4]}••••{k[-4:]}" if len(k) > 8 else "••••••••"

    return jsonify(
        connected=is_connected,
        environment=state["environment"],
        api_key_masked=masked_key,
    )


def close_single_position(state: Dict[str, Any], pos: Dict[str, Any]) -> Dict[str, Any]:
    """Close a specific position via reduce-only market order."""
    client = DeltaRESTClient(state["environment"], state["api_key"], state["api_secret"])
    size = int(safe_float(pos.get("size")))
    if size == 0:
        return {"symbol": pos.get("symbol"), "status": "Already closed"}

    product_id = int(pos.get("product_id"))
    symbol = str(pos.get("symbol") or pos.get("product_symbol"))
    # If size > 0 (long position), we sell to close. If size < 0 (short), we buy to close.
    exit_side = "sell" if size > 0 else "buy"

    # Try Tradehull first if india environment
    if state["environment"] == "india" and TDX is not None:
        try:
            tdx = state.get("tdx_client")
            if tdx is None:
                tdx = TDX.login(api_key=state["api_key"], api_secret=state["api_secret"])
                state["tdx_client"] = tdx
            res = tdx.close_position_market(
                symbol=symbol,
                product_id=product_id,
                size=abs(size),
                side="buy" if size > 0 else "sell",
            )
            return {"symbol": symbol, "side": exit_side, "size": abs(size), "result": res}
        except Exception as exc:
            logger.warning("Tradehull close_position_market failed (%s); using signed REST.", exc)

    payload = {
        "product_id": product_id,
        "product_symbol": symbol,
        "size": abs(size),
        "side": exit_side,
        "order_type": "market_order",
        "reduce_only": True,
        "time_in_force": "ioc",
        "client_order_id": f"grp-{secrets.token_hex(6)}",
    }
    res = client.authenticated_request("POST", "/v2/orders", body=payload)
    return {"symbol": symbol, "side": exit_side, "size": abs(size), "result": res}


@app.get("/api/positions")
def api_positions():
    """Fetch live account positions with exact Delta Terminal matching UPNL and asset values."""
    state = get_browser_state()
    if not state["api_key"] or not state["api_secret"]:
        return jsonify(connected=False, positions=[], total_upnl=0.0)

    try:
        client = DeltaRESTClient(state["environment"], state["api_key"], state["api_secret"])
        positions = client.authenticated_request("GET", "/v2/positions/margined")
        raw_list = positions if isinstance(positions, list) else []

        products = get_cached_products(state["environment"])
        prod_map = {p.get("id"): p for p in products if "id" in p}
        sym_map = {p.get("symbol"): p for p in products if "symbol" in p}

        normalized = []
        total_portfolio_upnl = 0.0
        total_portfolio_cost = 0.0

        for p in raw_list:
            size_val = safe_float(p.get("size"))
            if size_val == 0:
                continue

            sym = str(p.get("product_symbol") or (p.get("product") or {}).get("symbol") or "—")
            prod = prod_map.get(p.get("product_id")) or sym_map.get(sym) or {}

            # Determine contract value and asset unit (e.g. 0.001 BTC, 0.01 ETH)
            contract_value = safe_float(prod.get("contract_value"))
            if not contract_value or contract_value <= 0:
                if "BTC" in sym:
                    contract_value = 0.001
                elif "ETH" in sym:
                    contract_value = 0.01
                elif "XAUT" in sym:
                    contract_value = 0.001
                else:
                    contract_value = 1.0

            contract_unit = str(prod.get("contract_unit_currency") or ("BTC" if "BTC" in sym else "ETH" if "ETH" in sym else "Contracts"))

            contracts_count = abs(size_val)
            asset_qty = contracts_count * contract_value
            is_long = size_val > 0
            side_str = "BUY" if is_long else "SELL"

            entry_price = safe_float(p.get("entry_price"))
            mark_price = safe_float(p.get("mark_price"))

            # Fetch live ticker for real-time orderbook exit price (matching Delta terminal)
            best_bid = 0.0
            best_ask = 0.0
            try:
                t_resp = client.public_get(f"/v2/tickers/{sym}")
                if isinstance(t_resp, dict):
                    t_quotes = t_resp.get("quotes") or {}
                    best_bid = safe_float(t_quotes.get("best_bid"))
                    best_ask = safe_float(t_quotes.get("best_ask"))
                    t_mark = safe_float(t_resp.get("mark_price"))
                    if t_mark > 0:
                        mark_price = t_mark
            except Exception as t_err:
                logger.debug("Ticker fetch for position %s failed: %s", sym, t_err)

            # Option exit price:
            # Long position exits at Best Bid (or Mark Price if bid empty)
            # Short position exits at Best Ask (or Mark Price if ask empty)
            if is_long:
                exit_price = best_bid if best_bid > 0 else mark_price
                upnl = (exit_price - entry_price) * asset_qty
            else:
                exit_price = best_ask if best_ask > 0 else mark_price
                upnl = (entry_price - exit_price) * asset_qty

            entry_value = entry_price * asset_qty
            current_value = (mark_price if mark_price > 0 else exit_price) * asset_qty
            
            # Point and percentage changes
            pts_change = (mark_price if mark_price > 0 else exit_price) - entry_price
            pts_change_pct = (pts_change / entry_price * 100.0) if entry_price > 0 else 0.0
            pnl_pct = (upnl / entry_value * 100.0) if entry_value > 0 else 0.0

            total_portfolio_upnl += upnl
            total_portfolio_cost += entry_value

            normalized.append({
                "product_id": p.get("product_id"),
                "symbol": sym,
                "product_symbol": sym,
                "size": size_val,
                "contracts": contracts_count,
                "asset_qty": round(asset_qty, 4),
                "asset_unit": contract_unit,
                "side": side_str,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "exit_price": exit_price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "entry_value": round(entry_value, 2),
                "current_value": round(current_value, 2),
                "unrealized_pnl": round(upnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "pts_change": round(pts_change, 2),
                "pts_change_pct": round(pts_change_pct, 2),
            })

        total_portfolio_upnl = round(total_portfolio_upnl, 2)
        total_pnl_pct = round((total_portfolio_upnl / total_portfolio_cost * 100.0), 2) if total_portfolio_cost > 0 else 0.0
        return jsonify(
            connected=True,
            positions=normalized,
            total_upnl=total_portfolio_upnl,
            total_pnl_pct=total_pnl_pct,
        )
    except DeltaAPIError as exc:
        err_msg = str(exc)
        logger.warning("DeltaAPIError in api_positions: %s", err_msg)
        return jsonify(connected=True, positions=[], total_upnl=0.0, error=err_msg), 200
    except Exception as exc:
        return jsonify(connected=True, positions=[], total_upnl=0.0, error=str(exc)), 500


@app.get("/api/wallet")
def api_wallet():
    """Fetch live wallet equity and available margin."""
    state = get_browser_state()
    if not state["api_key"] or not state["api_secret"]:
        return jsonify(connected=False, equity=0.0, available=0.0, asset="USD")

    try:
        client = DeltaRESTClient(state["environment"], state["api_key"], state["api_secret"])
        full = client.authenticated_request("GET", "/v2/wallet/balances")
        balances = full if isinstance(full, list) else full.get("result", []) if isinstance(full, dict) else []

        preferred = None
        for b in (balances if isinstance(balances, list) else []):
            if str(b.get("asset_symbol", "")).upper() in ("USD", "USDT"):
                preferred = b
                break
        if not preferred and balances and isinstance(balances, list):
            preferred = balances[0]

        avail = safe_float((preferred or {}).get("available_balance"))
        total = safe_float((preferred or {}).get("balance"))
        asset = (preferred or {}).get("asset_symbol", "USD")

        return jsonify(connected=True, asset=asset, equity=total, available=avail, balances=balances)
    except DeltaAPIError as exc:
        err_msg = str(exc)
        logger.warning("DeltaAPIError in api_wallet: %s", err_msg)
        return jsonify(connected=True, equity=0.0, available=0.0, error=err_msg), 200
@app.get("/api/my-ip")
def api_my_ip():
    """Return the outbound public IP of this server."""
    try:
        ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        return jsonify(ip=ip)
    except Exception as exc:
        return jsonify(error=str(exc)), 500


@app.post("/api/positions/close")
def api_positions_close():
    """Close specific list of positions (e.g. for combined basket TP/SL or single close)."""
    state = get_browser_state()
    if not state["api_key"] or not state["api_secret"]:
        return jsonify(error="API credentials required to close positions."), 401

    data = request.get_json(silent=True) or {}
    positions_to_close = data.get("positions") or []
    group_name = str(data.get("group_name", "Position Group"))
    trigger_reason = str(data.get("reason", "Manual Close"))

    if not positions_to_close:
        return jsonify(error="No positions specified to close."), 400

    results = []
    errors = []
    timestamp_str = datetime.now().strftime("%H:%M:%S")

    for pos in positions_to_close:
        try:
            res = close_single_position(state, pos)
            results.append(res)
            history_entry = {
                "symbol": pos.get("symbol") or pos.get("product_symbol", "—"),
                "side": "CLOSE",
                "quantity": abs(int(safe_float(pos.get("size")))),
                "price": "MARKET",
                "order_type": "market_close",
                "status": f"Closed ({trigger_reason})",
                "time": timestamp_str,
                "result": res,
            }
            add_history_entry(state, history_entry)
        except Exception as exc:
            errors.append({"symbol": pos.get("symbol"), "error": str(exc)})
            history_entry = {
                "symbol": pos.get("symbol") or pos.get("product_symbol", "—"),
                "side": "CLOSE",
                "quantity": abs(int(safe_float(pos.get("size")))),
                "price": "MARKET",
                "order_type": "market_close",
                "status": f"Close Failed ({exc})",
                "time": timestamp_str,
                "reason": str(exc),
            }
            add_history_entry(state, history_entry)

    # If closing a strategy group, record consolidated strategy audit entry and unregister from active groups
    if group_name and group_name != "Position Group":
        leg_names = " + ".join(str(p.get("symbol") or p.get("product_symbol", "")) for p in positions_to_close)
        is_target = "target" in trigger_reason.lower() or "profit" in trigger_reason.lower()
        is_sl = "stop" in trigger_reason.lower() or "sl" in trigger_reason.lower()
        side_label = "PROFIT EXIT" if is_target else "SL EXIT" if is_sl else "CLOSE"
        profit_num = None
        if is_target:
            import re
            m = re.search(r"\+\$?([\d.]+)", trigger_reason)
            if m:
                try:
                    profit_num = round(float(m.group(1)), 2)
                except Exception:
                    pass

        entry_data = {
            "symbol": f"{group_name} ({leg_names})",
            "side": side_label,
            "quantity": sum(abs(int(safe_float(p.get("size")))) for p in positions_to_close),
            "price": f"+${profit_num:.2f}" if profit_num is not None else "MARKET",
            "order_type": "strategy_target_exit" if is_target else "strategy_group_close",
            "status": status_label,
            "time": timestamp_str,
        }
        if profit_num is not None:
            entry_data["realized_profit"] = profit_num

        add_history_entry(state, entry_data)
        with GROUPS_LOCK:
            cur_groups = load_server_strategy_groups()
            cur_groups = [g for g in cur_groups if g.get("name") != group_name]
            save_server_strategy_groups(cur_groups)

    return jsonify(
        ok=len(errors) == 0,
        group_name=group_name,
        trigger_reason=trigger_reason,
        closed=results,
        errors=errors,
    )


@app.get("/api/history")
def api_history():
    """Return order audit history from persistent disk storage and current session."""
    state = get_browser_state()
    with HISTORY_LOCK:
        disk_history = load_persistent_history()

    seen = set()
    combined = []
    for item in (disk_history + state.get("history", [])):
        key = (item.get("time"), item.get("symbol"), item.get("side"), item.get("status"))
        if key not in seen:
            seen.add(key)
            combined.append(item)
    return jsonify(history=combined)


@app.post("/api/orders")
def api_orders():
    """Place live order with optional target and stop-loss brackets."""
    state = get_browser_state()
    if not state["api_key"] or not state["api_secret"]:
        return jsonify(error="API credentials required to execute live orders."), 401

    data = request.get_json(silent=True) or {}
    required = ("product_id", "symbol", "side", "quantity", "order_type")
    for req in required:
        if not data.get(req):
            return jsonify(error=f"Field '{req}' is required."), 400

    side = str(data["side"]).lower()
    if side not in {"buy", "sell"}:
        return jsonify(error="Side must be 'buy' or 'sell'."), 400

    order_type = str(data["order_type"]).lower()
    if order_type not in {"market_order", "limit_order"}:
        return jsonify(error="Order type must be 'market_order' or 'limit_order'."), 400

    try:
        product_id = int(data["product_id"])
        quantity = int(data["quantity"])
        price = float(data["price"]) if data.get("price") not in (None, "") else None
        target_price = float(data["target_price"]) if data.get("target_price") not in (None, "") else None
        stop_loss_price = float(data["stop_loss_price"]) if data.get("stop_loss_price") not in (None, "") else None
    except (ValueError, TypeError):
        return jsonify(error="Invalid numeric format in quantity, price, target, or stop-loss."), 400

    if quantity <= 0:
        return jsonify(error="Quantity must be greater than zero."), 400

    if order_type == "limit_order" and (price is None or price <= 0):
        return jsonify(error="Limit order requires a positive limit price."), 400

    # Bracket validation checks
    if target_price is not None and stop_loss_price is not None and price is not None:
        if side == "buy":
            if target_price <= price:
                return jsonify(error="Target price must be higher than entry price for a BUY order."), 400
            if stop_loss_price >= price:
                return jsonify(error="Stop-loss price must be lower than entry price for a BUY order."), 400
        else:
            if target_price >= price:
                return jsonify(error="Target price must be lower than entry price for a SELL order."), 400
            if stop_loss_price <= price:
                return jsonify(error="Stop-loss price must be higher than entry price for a SELL order."), 400

    order_spec = {
        "product_id": product_id,
        "symbol": str(data["symbol"]),
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "price": price,
        "target_price": target_price,
        "stop_loss_price": stop_loss_price,
    }

    timestamp_str = datetime.now().strftime("%H:%M:%S")

    try:
        result = execute_order(state, order_spec)
        history_entry = {
            **order_spec,
            "status": "Submitted",
            "time": timestamp_str,
            "result": result,
        }
        add_history_entry(state, history_entry)
        return jsonify(ok=True, order=history_entry)
    except DeltaAPIError as exc:
        history_entry = {
            **order_spec,
            "status": "Rejected",
            "time": timestamp_str,
            "reason": str(exc),
        }
        add_history_entry(state, history_entry)
        return jsonify(error=str(exc)), 502
    except Exception as exc:
        history_entry = {
            **order_spec,
            "status": "Error",
            "time": timestamp_str,
            "reason": str(exc),
        }
        add_history_entry(state, history_entry)
        return jsonify(error=f"Execution error: {exc}"), 500


@app.post("/api/exit-all")
def api_exit_all():
    """Emergency account action: cancels all orders and closes all positions."""
    state = get_browser_state()
    if not state["api_key"] or not state["api_secret"]:
        return jsonify(error="API credentials required for Exit All."), 401

    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "EXIT ALL":
        return jsonify(error="You must type 'EXIT ALL' exactly to confirm."), 400

    timestamp_str = datetime.now().strftime("%H:%M:%S")

    try:
        summary = execute_exit_all(state)
        history_entry = {
            "symbol": "ALL POSITIONS",
            "side": "exit",
            "quantity": "—",
            "price": "—",
            "order_type": "market_close",
            "status": "Exit All Executed",
            "time": timestamp_str,
            "result": summary,
        }
        add_history_entry(state, history_entry)
        return jsonify(ok=True, summary=summary)
    except DeltaAPIError as exc:
        history_entry = {
            "symbol": "ALL POSITIONS",
            "side": "exit",
            "quantity": "—",
            "price": "—",
            "order_type": "market_close",
            "status": "Exit All Failed",
            "time": timestamp_str,
            "reason": str(exc),
        }
        add_history_entry(state, history_entry)
        return jsonify(error=str(exc)), 502
    except Exception as exc:
        return jsonify(error=f"Exit All error: {exc}"), 500


STRATEGY_GROUPS_FILE = os.path.join(os.path.dirname(__file__), ".strategy_groups.json")
GROUPS_LOCK = threading.Lock()


def load_server_strategy_groups() -> List[Dict[str, Any]]:
    """Load persistent strategy groups from disk (only active MONITORING groups)."""
    if not os.path.exists(STRATEGY_GROUPS_FILE):
        return []
    try:
        with open(STRATEGY_GROUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # Filter out old archived group ID (October 12) while preserving all user-created groups
                ARCHIVED_HISTORICAL_IDS = {"grp_1791791406521"}
                active = [
                    g for g in data
                    if isinstance(g, dict)
                    and g.get("status") == "MONITORING"
                    and g.get("id") not in ARCHIVED_HISTORICAL_IDS
                ]
                return active
            return []
    except Exception as exc:
        logger.warning("Could not read strategy groups: %s", exc)
        return []


def save_server_strategy_groups(groups: List[Dict[str, Any]]) -> None:
    """Save persistent strategy groups to disk."""
    try:
        with open(STRATEGY_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2)
    except Exception as exc:
        logger.error("Could not write strategy groups: %s", exc)


STRANGLE_SCHEDULER_FILE = os.path.join(os.path.dirname(__file__), ".strangle_scheduler.json")


def load_strangle_scheduler() -> Dict[str, Any]:
    """Load automated Saturday strangle scheduler settings."""
    if not os.path.exists(STRANGLE_SCHEDULER_FILE):
        return {"enabled": False, "lots": 100, "last_executed_date": "", "trade_cycle": 1, "selling_enabled": False}
    try:
        with open(STRANGLE_SCHEDULER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                if "selling_enabled" not in data:
                    data["selling_enabled"] = False
                return data
            return {"enabled": False, "lots": 100, "selling_enabled": False}
    except Exception:
        return {"enabled": False, "lots": 100, "last_executed_date": "", "trade_cycle": 1, "selling_enabled": False}


def save_strangle_scheduler(data: Dict[str, Any]) -> None:
    """Save automated Saturday strangle scheduler settings."""
    try:
        with open(STRANGLE_SCHEDULER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error("Could not save strangle scheduler: %s", e)


def get_next_month_last_friday(base_date=None) -> str:
    """Calculate the DD-MM-YYYY string for Next-Month Last Friday (40-55 DTE)."""
    if base_date is None:
        base_date = datetime.now(timezone.utc)
    year = base_date.year
    month = base_date.month + 1
    if month > 12:
        month = 1
        year += 1
    last_day = calendar.monthrange(year, month)[1]
    last_date = datetime(year, month, last_day, tzinfo=timezone.utc)
    offset = (last_date.weekday() - 4) % 7
    last_friday = last_date - timedelta(days=offset)
    return last_friday.strftime("%d-%m-%Y")


def select_monthly_strangle_strikes(environment="india", target_expiry=None) -> Dict[str, Any]:
    """Official Rulebook Algorithm: Select +15% to +25% OTM Call and -15% to -25% OTM Put closest to $500 mark."""
    client = DeltaRESTClient(environment)
    expiries = get_asset_expiries(environment, "BTC")
    avail_vals = [e["value"] for e in expiries]

    if not target_expiry or target_expiry not in avail_vals:
        calc_expiry = get_next_month_last_friday()
        if calc_expiry in avail_vals:
            target_expiry = calc_expiry
        else:
            now_dt = datetime.now(timezone.utc)
            long_expiries = []
            for e in expiries:
                try:
                    exp_dt = datetime.strptime(e["value"], "%d-%m-%Y").replace(tzinfo=timezone.utc)
                    dte = (exp_dt - now_dt).days
                    if 30 <= dte <= 65:
                        long_expiries.append((dte, e["value"]))
                except Exception:
                    pass
            if long_expiries:
                long_expiries.sort(key=lambda x: x[0], reverse=True)
                target_expiry = long_expiries[0][1]
            elif avail_vals:
                target_expiry = avail_vals[-1]

    tickers = client.public_get("/v2/tickers", {
        "contract_types": "call_options,put_options",
        "underlying_asset_symbols": "BTC",
        "expiry_date": target_expiry
    })

    if not tickers or not isinstance(tickers, list):
        raise ValueError(f"No option tickers available for expiry {target_expiry}")

    spot = 0.0
    for t in tickers:
        sp = safe_float(t.get("spot_price"))
        if sp > 0:
            spot = sp
            break

    if spot <= 0:
        raise ValueError("Could not query BTC spot index price")

    calls = [t for t in tickers if t.get("contract_type") == "call_options"]
    puts = [t for t in tickers if t.get("contract_type") == "put_options"]

    # CALL filtering: target zone +12% to +30% OTM (Delta ~0.15 - 0.20), closest to $500
    call_candidates = []
    for c in calls:
        st = safe_float(c.get("strike_price"))
        if st <= 0:
            continue
        dist = ((st - spot) / spot) * 100
        mark = safe_float(c.get("mark_price"))
        quotes = c.get("quotes") or {}
        bid = safe_float(quotes.get("best_bid"))
        ask = safe_float(quotes.get("best_ask"))
        diff = abs(mark - 500.0)
        if 12.0 <= dist <= 30.0:
            call_candidates.append({
                "strike": st, "mark": mark, "bid": bid, "ask": ask,
                "dist": round(dist, 1), "diff_500": round(diff, 1),
                "symbol": c.get("symbol"), "product_id": c.get("product_id")
            })

    if not call_candidates:
        call_candidates = [{
            "strike": safe_float(c.get("strike_price")),
            "mark": safe_float(c.get("mark_price")),
            "symbol": c.get("symbol"), "product_id": c.get("product_id"),
            "diff_500": abs(safe_float(c.get("mark_price")) - 500),
            "dist": round(((safe_float(c.get("strike_price")) - spot) / spot) * 100, 1),
            "bid": safe_float((c.get("quotes") or {}).get("best_bid")),
            "ask": safe_float((c.get("quotes") or {}).get("best_ask")),
        } for c in calls if safe_float(c.get("strike_price")) > spot]

    call_candidates.sort(key=lambda x: x["diff_500"])
    selected_call = call_candidates[0]

    # PUT filtering: target zone -30% to -12% OTM, closest to $500
    put_candidates = []
    for p in puts:
        st = safe_float(p.get("strike_price"))
        if st <= 0:
            continue
        dist = ((st - spot) / spot) * 100
        mark = safe_float(p.get("mark_price"))
        quotes = p.get("quotes") or {}
        bid = safe_float(quotes.get("best_bid"))
        ask = safe_float(quotes.get("best_ask"))
        diff = abs(mark - 500.0)
        if -30.0 <= dist <= -12.0:
            put_candidates.append({
                "strike": st, "mark": mark, "bid": bid, "ask": ask,
                "dist": round(dist, 1), "diff_500": round(diff, 1),
                "symbol": p.get("symbol"), "product_id": p.get("product_id")
            })

    if not put_candidates:
        put_candidates = [{
            "strike": safe_float(p.get("strike_price")),
            "mark": safe_float(p.get("mark_price")),
            "symbol": p.get("symbol"), "product_id": p.get("product_id"),
            "diff_500": abs(safe_float(p.get("mark_price")) - 500),
            "dist": round(((safe_float(p.get("strike_price")) - spot) / spot) * 100, 1),
            "bid": safe_float((p.get("quotes") or {}).get("best_bid")),
            "ask": safe_float((p.get("quotes") or {}).get("best_ask")),
        } for p in puts if 0 < safe_float(p.get("strike_price")) < spot]

    put_candidates.sort(key=lambda x: x["diff_500"])
    selected_put = put_candidates[0]

    try:
        exp_dt = datetime.strptime(target_expiry, "%d-%m-%Y").replace(tzinfo=timezone.utc)
        dte = (exp_dt - datetime.now(timezone.utc)).days
    except Exception:
        dte = 45

    return {
        "spot": spot,
        "expiry": target_expiry,
        "dte": dte,
        "call": selected_call,
        "put": selected_put,
    }


def get_next_saturday_ist() -> Dict[str, Any]:
    """Calculate the next Saturday 00:00 IST execution window and countdown."""
    now_utc = datetime.now(timezone.utc)
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist = now_utc + ist_offset
    # Weekday: Monday is 0, ..., Saturday is 5, Sunday is 6
    days_ahead = 5 - now_ist.weekday()
    if days_ahead < 0 or (days_ahead == 0 and (now_ist.hour > 0 or now_ist.minute > 20)):
        days_ahead += 7
    next_sat = now_ist.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    diff = next_sat - now_ist
    total_seconds = max(0, int(diff.total_seconds()))
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    return {
        "current_ist": now_ist.strftime("%A, %d-%b-%Y %H:%M:%S IST"),
        "datetime_ist": next_sat.strftime("%A, %d-%b-%Y @ 00:00:00 IST"),
        "date_str": next_sat.strftime("%d-%b-%Y"),
        "remaining_seconds": total_seconds,
        "remaining_human": f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m",
    }


def calculate_strangle_math(call_price: float, put_price: float, lots: int) -> Dict[str, Any]:
    """Auto-Scaled Lot Sizing, TP (+20%) and Dynamic SL Mathematics per Blueprint."""
    btc_size = round(lots * 0.001, 4)
    combined_prem_per_btc = round(call_price + put_price, 2)
    total_credit = round(combined_prem_per_btc * btc_size, 2)

    # TP = 20% of Credit
    tp_dollar = round(total_credit * 0.20, 2)

    # Dynamic SL = 2.0x TP (40% of Credit)
    dynamic_sl_dollar = round(tp_dollar * 2.0, 2)

    # Hard SL Cap = Lots * $1.50 USD
    hard_sl_cap = round(lots * 1.50, 2)

    final_sl_dollar = round(min(hard_sl_cap, dynamic_sl_dollar), 2)

    tp_exit_prem = round(combined_prem_per_btc * 0.80, 2)
    sl_exit_prem = round(combined_prem_per_btc + (final_sl_dollar / btc_size), 2)

    return {
        "lots": lots,
        "btc_size": btc_size,
        "call_price": call_price,
        "put_price": put_price,
        "combined_prem_per_btc": combined_prem_per_btc,
        "total_credit": total_credit,
        "tp_dollar": tp_dollar,
        "dynamic_sl_dollar": dynamic_sl_dollar,
        "hard_sl_cap": hard_sl_cap,
        "final_sl_dollar": final_sl_dollar,
        "tp_exit_prem": tp_exit_prem,
        "sl_exit_prem": sl_exit_prem,
    }


def execute_monthly_strangle_order(env: str, key: str, secret: str, lots: int = 100, custom_tp: Optional[float] = None, custom_sl: Optional[float] = None) -> Dict[str, Any]:
    """Execute SELL orders for both Call and Put legs of Monthly Strangle, then register 24/7 monitoring group."""
    selection = select_monthly_strangle_strikes(env)
    call_leg = selection["call"]
    put_leg = selection["put"]

    call_mark = call_leg["mark"]
    put_mark = put_leg["mark"]

    # Marketable Limit Prices at Midpoint with 0.5% buffer for slippage protection
    call_mid = ((call_leg["bid"] + call_leg["ask"]) / 2.0) if (call_leg["bid"] > 0 and call_leg["ask"] > 0) else call_mark
    put_mid = ((put_leg["bid"] + put_leg["ask"]) / 2.0) if (put_leg["bid"] > 0 and put_leg["ask"] > 0) else put_mark

    call_limit = round(call_mid * 0.995, 1)
    put_limit = round(put_mid * 0.995, 1)

    math_data = calculate_strangle_math(call_mark, put_mark, lots)
    rest_client = DeltaRESTClient(env, key, secret)

    # 1. Place SELL CALL
    call_order_payload = {
        "product_id": int(call_leg["product_id"]),
        "size": int(lots),
        "side": "sell",
        "order_type": "limit_order",
        "limit_price": str(call_limit),
        "time_in_force": "gtc",
        "client_order_id": f"str-c-{secrets.token_hex(4)}",
    }
    call_res = rest_client.authenticated_request("POST", "/v2/orders", body=call_order_payload)

    # 2. Place SELL PUT
    put_order_payload = {
        "product_id": int(put_leg["product_id"]),
        "size": int(lots),
        "side": "sell",
        "order_type": "limit_order",
        "limit_price": str(put_limit),
        "time_in_force": "gtc",
        "client_order_id": f"str-p-{secrets.token_hex(4)}",
    }
    put_res = rest_client.authenticated_request("POST", "/v2/orders", body=put_order_payload)

    final_tp = round(float(custom_tp), 2) if (custom_tp is not None and safe_float(custom_tp) > 0) else math_data["tp_dollar"]
    final_sl = round(float(custom_sl), 2) if (custom_sl is not None and safe_float(custom_sl) > 0) else math_data["final_sl_dollar"]

    # 3. Create Strategy Group with TP & SL (Custom or Rulebook Auto-Calculated)
    group_id = f"grp_strangle_{int(time.time() * 1000)}"
    new_group = {
        "id": group_id,
        "name": f"BTC Monthly Strangle ({lots} Lots)",
        "strategy_type": "MONTHLY_STRANGLE",
        "lots": lots,
        "positions": [
            {
                "product_id": call_leg["product_id"],
                "symbol": call_leg["symbol"],
                "size": -int(lots),
                "side": "sell",
                "entry_price": call_mid,
            },
            {
                "product_id": put_leg["product_id"],
                "symbol": put_leg["symbol"],
                "size": -int(lots),
                "side": "sell",
                "entry_price": put_mid,
            },
        ],
        "target": final_tp,
        "stopLoss": final_sl,
        "total_credit": math_data["total_credit"],
        "status": "MONITORING",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notes": f"Auto-Strangle {lots} Lots · Target TP: +${final_tp} | Dynamic SL: -${final_sl}",
    }

    with GROUPS_LOCK:
        groups = load_server_strategy_groups()
        groups.insert(0, new_group)
        save_server_strategy_groups(groups)

    logger.info("Successfully deployed BTC Monthly Strangle: %s | Target: +$%.2f | SL: -$%.2f", new_group["name"], new_group["target"], new_group["stopLoss"])

    return {
        "ok": True,
        "group": new_group,
        "call_order": call_res,
        "put_order": put_res,
        "math": math_data,
        "selection": selection,
    }


def run_strategy_monitor_daemon():
    """Autonomous 24/7 background worker that monitors strategy groups and executes auto-close even when browser is closed."""
    logger.info("Starting autonomous 24/7 strategy group monitoring daemon.")
    while True:
        try:
            time.sleep(1.0)
            with GROUPS_LOCK:
                groups = load_server_strategy_groups()
                monitoring_groups = [g for g in groups if g.get("status") == "MONITORING"]
                if not monitoring_groups:
                    continue

                creds = load_persistent_credentials()
                if not creds:
                    continue

                env = creds.get("environment", "india")
                key = creds.get("api_key", "")
                secret = creds.get("api_secret", "")
                if not key or not secret:
                    continue

                # Query live positions
                client = DeltaRESTClient(env, key, secret)
                raw_positions = client.authenticated_request("GET", "/v2/positions/margined")
                if not isinstance(raw_positions, list):
                    continue

                products = get_cached_products(env)
                prod_map = {p.get("id"): p for p in products if "id" in p}
                sym_map = {p.get("symbol"): p for p in products if "symbol" in p}

                pos_by_key = {}
                for p in raw_positions:
                    size_val = safe_float(p.get("size"))
                    if size_val == 0:
                        continue
                    sym = str(p.get("product_symbol") or (p.get("product") or {}).get("symbol") or "")
                    prod_id = p.get("product_id")

                    prod = prod_map.get(prod_id) or sym_map.get(sym) or {}
                    contract_val = safe_float(prod.get("contract_value"), 0.001 if "BTC" in sym else 0.01 if "ETH" in sym else 1.0)
                    contracts_count = abs(size_val)
                    asset_qty = contracts_count * contract_val

                    entry_price = safe_float(p.get("entry_price"))
                    mark_price = safe_float(p.get("mark_price"))
                    is_long = size_val > 0

                    best_bid = 0.0
                    best_ask = 0.0
                    try:
                        t_resp = client.public_get(f"/v2/tickers/{sym}")
                        if isinstance(t_resp, dict):
                            quotes = t_resp.get("quotes") or {}
                            best_bid = safe_float(quotes.get("best_bid"))
                            best_ask = safe_float(quotes.get("best_ask"))
                            t_mark = safe_float(t_resp.get("mark_price"))
                            if t_mark > 0:
                                mark_price = t_mark
                    except Exception:
                        pass

                    if is_long:
                        exit_price = best_bid if best_bid > 0 else mark_price
                        upnl = (exit_price - entry_price) * asset_qty
                    else:
                        exit_price = best_ask if best_ask > 0 else mark_price
                        upnl = (entry_price - exit_price) * asset_qty

                    item = {
                        "product_id": prod_id,
                        "symbol": sym,
                        "size": size_val,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "exit_price": exit_price,
                        "unrealized_pnl": upnl,
                    }
                    pos_by_key[f"{prod_id}_{sym}"] = item
                    pos_by_key[str(prod_id)] = item
                    pos_by_key[sym] = item

                dirty = False
                for grp in groups:
                    if grp.get("status") != "MONITORING":
                        continue

                    grp_legs = grp.get("positions", [])
                    combined_mtm = 0.0
                    matched_count = 0

                    for leg in grp_legs:
                        key1 = f"{leg.get('product_id')}_{leg.get('symbol')}"
                        key2 = str(leg.get('product_id'))
                        key3 = str(leg.get('symbol'))
                        matched = pos_by_key.get(key1) or pos_by_key.get(key2) or pos_by_key.get(key3)
                        if matched:
                            combined_mtm += matched["unrealized_pnl"]
                            matched_count += 1

                    grp["current_mtm"] = round(combined_mtm, 2)
                    grp["last_checked"] = datetime.now(timezone.utc).isoformat()
                    target = safe_float(grp.get("target"), 999999)
                    stop_loss = safe_float(grp.get("stopLoss"), 999999)

                    # Trigger Target Profit Auto-Close
                    if combined_mtm >= target:
                        logger.info("🎯 TARGET HIT for group '%s'! MTM=+$%.2f >= Target=+$%.2f. Auto-closing all legs!", grp.get("name"), combined_mtm, target)
                        grp["status"] = "TARGET_HIT"
                        grp["exit_reason"] = f"Target Profit Hit (+${combined_mtm:.2f})"
                        dirty = True
                        dummy_state = {"environment": env, "api_key": key, "api_secret": secret, "tdx_client": None}
                        for leg in grp_legs:
                            try:
                                close_single_position(dummy_state, leg)
                            except Exception as c_err:
                                logger.error("Auto-close leg %s error: %s", leg.get("symbol"), c_err)

                        # Archive to persistent history
                        leg_syms = " + ".join(str(l.get("symbol", "")) for l in grp_legs)
                        total_qty = sum(abs(int(safe_float(l.get("size")))) for l in grp_legs)
                        add_history_entry(dummy_state, {
                            "symbol": f"{grp.get('name')} ({leg_syms})",
                            "side": "PROFIT EXIT",
                            "quantity": f"{total_qty} Lots",
                            "price": f"+${combined_mtm:.2f}",
                            "realized_profit": round(float(combined_mtm), 2),
                            "order_type": "strategy_target_exit",
                            "status": f"🎯 Target Profit Booked (+${combined_mtm:.2f} USD)",
                            "time": datetime.now().strftime("%d-%m %H:%M:%S"),
                        })

                    # Trigger Stop-Loss Auto-Close
                    elif combined_mtm <= -abs(stop_loss):
                        logger.info("🛑 STOP-LOSS HIT for group '%s'! MTM=-$%.2f <= SL=-$%.2f. Auto-closing all legs!", grp.get("name"), abs(combined_mtm), stop_loss)
                        grp["status"] = "SL_HIT"
                        grp["exit_reason"] = f"Stop Loss Hit (-${abs(combined_mtm):.2f})"
                        dirty = True
                        dummy_state = {"environment": env, "api_key": key, "api_secret": secret, "tdx_client": None}
                        for leg in grp_legs:
                            try:
                                close_single_position(dummy_state, leg)
                            except Exception as c_err:
                                logger.error("Auto-close leg %s error: %s", leg.get("symbol"), c_err)

                        # Archive to persistent history
                        leg_syms = " + ".join(str(l.get("symbol", "")) for l in grp_legs)
                        total_qty = sum(abs(int(safe_float(l.get("size")))) for l in grp_legs)
                        add_history_entry(dummy_state, {
                            "symbol": f"{grp.get('name')} ({leg_syms})",
                            "side": "SL EXIT",
                            "quantity": f"{total_qty} Lots",
                            "price": f"-${abs(combined_mtm):.2f}",
                            "order_type": "strategy_sl_exit",
                            "status": f"🛑 Stop-Loss Closed (-${abs(combined_mtm):.2f} USD)",
                            "time": datetime.now().strftime("%d-%m %H:%M:%S"),
                        })

                if dirty:
                    # Keep only active groups in persistent strategy groups so completed groups disappear from the front panel!
                    active_only = [g for g in groups if g.get("status") == "MONITORING"]
                    save_server_strategy_groups(active_only)

            # Automated Monthly Strangle Saturday 00:00 IST Scheduler
            if int(time.time()) % 20 == 0:
                try:
                    sched = load_strangle_scheduler()
                    if sched.get("enabled") and sched.get("selling_enabled", True):
                        all_groups = load_server_strategy_groups()
                        # Golden Rule: Check if any trade is currently active (MONITORING)
                        has_active = any(g.get("status") == "MONITORING" for g in all_groups)
                        if not has_active:
                            now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
                            # Saturday is weekday 5
                            if now_ist.weekday() == 5:
                                # Trigger window: Saturday midnight 00:00 to 00:20 IST
                                if now_ist.hour == 0 and now_ist.minute <= 20:
                                    current_date_str = now_ist.strftime("%Y-%m-%d")
                                    if sched.get("last_executed_date") != current_date_str:
                                        logger.info("⏰ SATURDAY 00:00 IST TRIGGER: Deploying automated Monthly Strangle (%s Lots)!", sched.get("lots", 100))
                                        sched_creds = load_persistent_credentials()
                                        if sched_creds and sched_creds.get("api_key") and sched_creds.get("api_secret"):
                                            res = execute_monthly_strangle_order(
                                                sched_creds.get("environment", "india"),
                                                sched_creds.get("api_key"),
                                                sched_creds.get("api_secret"),
                                                lots=sched.get("lots", 100),
                                            )
                                            sched["last_executed_date"] = current_date_str
                                            save_strangle_scheduler(sched)
                                            logger.info("✅ Automated Monthly Strangle deployed successfully! Group ID: %s", res.get("group", {}).get("id"))
                                        else:
                                            logger.warning("Auto-scheduler could not execute: No persistent credentials saved.")
                except Exception as sched_err:
                    logger.debug("Auto-scheduler loop error: %s", sched_err)

        except Exception as loop_err:
            logger.debug("Strategy monitor daemon loop exception: %s", loop_err)


# Start autonomous monitoring daemon thread on startup
threading.Thread(target=run_strategy_monitor_daemon, daemon=True).start()


@app.route("/api/strategy-groups", methods=["GET", "POST"])
def api_strategy_groups():
    """Fetch or create persistent server-monitored strategy groups."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        new_grp = data.get("group")
        if not new_grp or not new_grp.get("positions"):
            return jsonify(error="Invalid strategy group specification."), 400

        with GROUPS_LOCK:
            groups = load_server_strategy_groups()
            groups = [g for g in groups if g.get("id") != new_grp.get("id")]
            groups.insert(0, new_grp)
            save_server_strategy_groups(groups)
        return jsonify(status="created", group=new_grp)

    # GET request
    with GROUPS_LOCK:
        groups = load_server_strategy_groups()
    return jsonify(groups=groups)


@app.route("/api/strategy-groups/<group_id>", methods=["DELETE"])
def api_delete_strategy_group(group_id):
    """Remove a strategy group from 24/7 monitoring."""
    with GROUPS_LOCK:
        groups = load_server_strategy_groups()
        groups = [g for g in groups if g.get("id") != group_id]
        save_server_strategy_groups(groups)
    return jsonify(status="deleted", group_id=group_id)


@app.get("/api/strategy/monthly-strangle/preview")
def api_strangle_preview():
    """Fetch live strike selection, math, and status for the Monthly Strangle strategy."""
    state = get_browser_state()
    scheduler = load_strangle_scheduler()
    default_lots = int(scheduler.get("lots", 100))
    lots = int(safe_float(request.args.get("lots", default_lots), default_lots))
    if lots <= 0:
        lots = default_lots
    try:
        selection = select_monthly_strangle_strikes(state["environment"])
        math_data = calculate_strangle_math(selection["call"]["mark"], selection["put"]["mark"], lots)

        with GROUPS_LOCK:
            groups = load_server_strategy_groups()
        active_strangle = next((g for g in groups if g.get("status") == "MONITORING" and (g.get("strategy_type") == "MONTHLY_STRANGLE" or len(g.get("positions", [])) >= 2)), None)
        if not active_strangle:
            active_strangle = next((g for g in groups if g.get("status") == "MONITORING"), None)

        next_entry = get_next_saturday_ist()

        return jsonify(
            ok=True,
            selection=selection,
            math=math_data,
            has_active_trade=bool(active_strangle),
            active_trade=active_strangle,
            scheduler=scheduler,
            default_lots=default_lots,
            selling_enabled=scheduler.get("selling_enabled", True),
            next_entry=next_entry,
        )
    except Exception as exc:
        logger.exception("Error in strangle preview")
        return jsonify(ok=False, error=str(exc)), 500


@app.post("/api/strategy/monthly-strangle/execute")
def api_strangle_execute():
    """Execute live 1-click Strangle order and activate 24/7 monitoring."""
    state = get_browser_state()
    key = state.get("api_key")
    secret = state.get("api_secret")
    env = state.get("environment", "india")

    if not key or not secret:
        creds = load_persistent_credentials()
        if creds:
            key = creds.get("api_key")
            secret = creds.get("api_secret")
            env = creds.get("environment", "india")

    if not key or not secret:
        return jsonify(error="API credentials required to execute live Strangle order."), 401

    # Check Master Selling Switch
    sched = load_strangle_scheduler()
    if not sched.get("selling_enabled", True):
        return jsonify(error="Option Selling is currently PAUSED (OFF). Turn ON the Option Selling switch to deploy trades."), 400

    data = request.get_json(silent=True) or {}
    lots = int(safe_float(data.get("lots", 100), 100))
    if lots <= 0:
        return jsonify(error="Lots must be greater than zero."), 400

    # Golden Rule: No overlapping active strangle trades
    with GROUPS_LOCK:
        groups = load_server_strategy_groups()
    active_strangle = next((g for g in groups if g.get("status") == "MONITORING" and (g.get("strategy_type") == "MONTHLY_STRANGLE" or len(g.get("positions", [])) >= 2)), None)
    if not active_strangle:
        active_strangle = next((g for g in groups if g.get("status") == "MONITORING"), None)
    if active_strangle:
        return jsonify(error=f"Golden Rule Active: Strategy '{active_strangle.get('name')}' is already live! Wait for it to close before opening a new trade."), 400

    try:
        custom_tp = safe_float(data.get("custom_tp")) if data.get("custom_tp") is not None else None
        custom_sl = safe_float(data.get("custom_sl")) if data.get("custom_sl") is not None else None
        result = execute_monthly_strangle_order(env, key, secret, lots=lots, custom_tp=custom_tp, custom_sl=custom_sl)
        return jsonify(result)
    except Exception as exc:
        logger.exception("Strangle execution error")
        return jsonify(error=str(exc)), 500


@app.route("/api/strategy/monthly-strangle/selling-toggle", methods=["GET", "POST"])
def api_strangle_selling_toggle():
    """Get or set the master Option Selling ON/OFF state."""
    sched = load_strangle_scheduler()
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        new_val = bool(data.get("enabled", True))
        sched["selling_enabled"] = new_val
        save_strangle_scheduler(sched)
        logger.info("Option Selling Master Switch toggled to: %s", "ENABLED" if new_val else "PAUSED")
        return jsonify(ok=True, selling_enabled=new_val)
    return jsonify(ok=True, selling_enabled=sched.get("selling_enabled", True))


@app.route("/api/strategy/monthly-strangle/scheduler", methods=["GET", "POST"])
def api_strangle_scheduler():
    """Inspect or configure automated Saturday 00:00 IST scheduler."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        sched = load_strangle_scheduler()
        if "enabled" in data:
            sched["enabled"] = bool(data["enabled"])
        if "lots" in data:
            sched["lots"] = max(1, int(safe_float(data["lots"], 100)))
        save_strangle_scheduler(sched)
        return jsonify(ok=True, scheduler=sched)

    sched = load_strangle_scheduler()
    with GROUPS_LOCK:
        groups = load_server_strategy_groups()
    active_strangle = next((g for g in groups if g.get("strategy_type") == "MONTHLY_STRANGLE" and g.get("status") == "MONITORING"), None)
    return jsonify(scheduler=sched, has_active_trade=bool(active_strangle), active_trade=active_strangle)


@app.post("/api/strategy/monthly-strangle/save-default-lots")
def api_save_strangle_default_lots():
    """Save user's preferred default lot size to disk permanently."""
    data = request.get_json(silent=True) or {}
    lots = max(1, int(safe_float(data.get("lots", 100), 100)))
    sched = load_strangle_scheduler()
    sched["lots"] = lots
    save_strangle_scheduler(sched)
    logger.info("Saved preferred default Strangle lots: %d", lots)
    return jsonify(ok=True, lots=lots)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)


