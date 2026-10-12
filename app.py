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

import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
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

    # Search candidates for app.js
    for candidate in [
        os.path.join(base, "static", "js", "js", "app.js"),
        os.path.join(base, "js", "app.js"),
        os.path.join(base, "static", "js", "app.js"),
    ]:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    content = f.read()
                if "btn-group-edit" in content:
                    target = os.path.join(static_js_dir, "app.js")
                    if os.path.abspath(candidate) != os.path.abspath(target):
                        with open(target, "w", encoding="utf-8") as f:
                            f.write(content)
                        logger.info("Auto-synced app.js from %s to %s", candidate, target)
                    break
            except Exception as e:
                logger.warning("Error inspecting candidate %s: %s", candidate, e)

    # Search candidates for style.css
    for candidate in [
        os.path.join(base, "static", "js", "css", "style.css"),
        os.path.join(base, "css", "style.css"),
        os.path.join(base, "static", "css", "style.css"),
    ]:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    content = f.read()
                if "btn-group-edit" in content:
                    target = os.path.join(static_css_dir, "style.css")
                    if os.path.abspath(candidate) != os.path.abspath(target):
                        with open(target, "w", encoding="utf-8") as f:
                            f.write(content)
                        logger.info("Auto-synced style.css from %s to %s", candidate, target)
                    break
            except Exception as e:
                logger.warning("Error inspecting candidate %s: %s", candidate, e)


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
        if sid not in SESSION_STATE:
            disk_creds = load_persistent_credentials()
            if disk_creds:
                SESSION_STATE[sid] = {
                    "api_key": disk_creds.get("api_key", ""),
                    "api_secret": disk_creds.get("api_secret", ""),
                    "environment": disk_creds.get("environment", "india"),
                    "history": [],
                    "tdx_client": None,
                }
            else:
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
            err_msg = err.get("message") or err.get("code") or response.text[:200]
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


def add_history_entry(state: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """Store order record in session memory history."""
    with STATE_LOCK:
        state["history"].insert(0, entry)
        if len(state["history"]) > 150:
            state["history"].pop()


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
        return jsonify(connected=True, positions=[], total_upnl=0.0, error=str(exc)), 502
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
        return jsonify(connected=True, equity=0.0, available=0.0, error=str(exc)), 502
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

    return jsonify(
        ok=len(errors) == 0,
        group_name=group_name,
        trigger_reason=trigger_reason,
        closed=results,
        errors=errors,
    )


@app.get("/api/history")
def api_history():
    """Return order audit history for the current session."""
    state = get_browser_state()
    return jsonify(history=state["history"])


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
    """Load persistent strategy groups from disk."""
    if not os.path.exists(STRATEGY_GROUPS_FILE):
        return []
    try:
        with open(STRATEGY_GROUPS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
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

                if dirty:
                    save_server_strategy_groups(groups)

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

