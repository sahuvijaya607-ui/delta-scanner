# Delta Exchange Professional Option Chain Scanner

A localhost high-frequency option-chain terminal and execution dashboard built with **Python Flask**, **Tradehull-Delta-Exchange**, and vanilla HTML5, CSS3, and JavaScript.

![Delta Option Scanner](https://img.shields.io/badge/Delta_Exchange-Institutional_Terminal-00e5b3?style=for-the-badge)
![Status](https://img.shields.io/badge/Status-Operational-10b981?style=for-the-badge)

---

## ⚡ Key Features

- **Public Option Chain Scanner**:
  - Full support for **BTC**, **ETH**, and **XAUT** (Tether Gold) options.
  - Live Expiry Date selector with automatic calendar sorting.
  - Spot Price, At-The-Money (ATM) strike calculation and visual highlighting.
  - Put-Call Ratio (PCR by Open Interest) with dynamic sentiment tags (Bullish / Neutral / Bearish).
  - Strike-by-strike breakdown: Call/Put Open Interest (OI), Volume, Implied Volatility (IV%), Best Bid, Best Ask, Last Price / Mark Price, and Action Buttons.

- **1-Second Real-Time Scanner**:
  - **Start Scanner**: Streams fresh quotes every second (1000ms).
  - **Stop Scanner**: Freezes updates instantly without losing state.
  - Smooth in-place DOM updates preventing table jitter, scroll jumps, or selection resets.
  - Visual green/red price tick flashes on bid/ask/last price changes.

- **Live Order Tickets & Bracket Execution**:
  - One-click contract loading: click `BUY` or `SELL` on any option row to load the contract into the order ticket.
  - Order types: **Market** and **Limit**.
  - **Bracket Orders**: Target (Take-Profit) and Stop-Loss (SL) protection.
  - Integration with **Tradehull-Delta-Exchange** (`TDX.login`, `place_order`, `place_option_bracket_order`) for Delta India, with automatic fallback to signed official HMAC-SHA256 REST API.
  - Two-step safety validation: risk acknowledgment checkbox + live order confirmation modal.

- **Account Positions & Emergency Exit All**:
  - Live margined open positions monitoring with real-time unrealized P&L color coding.
  - **Exit All**: Emergency panic button requiring explicit typing of `EXIT ALL` to cancel all open orders and close all portfolio and isolated positions.
  - Session-only in-memory order audit log.

- **Institutional Dark Terminal UI**:
  - High-contrast Bloomberg / TradingView dark theme with JetBrains Mono tabular numbers.
  - Built-in Web Audio API synthesizer for order confirmation chimes and alerts (no external audio files required, toggleable sound on/off).
  - Digital clock displaying Indian Standard Time (IST) and UTC.

---

## 🔐 Security & Memory Architecture

- **RAM-Only Credential Storage**: API Key and Secret are stored solely in volatile server process memory isolated per browser session ID.
- **Zero Disk / Cookie Leaks**: Credentials are never written to disk, never logged, and never stored in cookies or client storage.
- **Safe Public Mode**: Public option-chain quotes load immediately without providing any API credentials.

---

## 🚀 Quick Start Guide

### 1. Activate the Virtual Environment

Open PowerShell in this directory (`OPTION SCANNER`):

```powershell
.\.venv\Scripts\Activate.ps1
```

*(If execution policy restricts scripts, you can run directly with `.\.venv\Scripts\python.exe app.py`)*

### 2. Run the Dashboard

```powershell
.\.venv\Scripts\python.exe app.py
```

### 3. Open in Browser

Navigate to:
```
http://127.0.0.1:5000
```

1. Click **▶ START SCANNER** to begin streaming live options data immediately.
2. Switch between **BTC**, **ETH**, and **XAUT** tabs.
3. Select an expiry date from the dropdown.
4. (Optional) Enter your Delta API Key & Secret and click **Save Credentials** to enable live order placement and open position tracking.

---

## 📚 API References & Integrations

- **[Tradehull Delta Exchange (PyPI)](https://pypi.org/project/Tradehull-Delta-Exchange/)**: Used for Delta India option bracket orders and account management.
- **[Delta Exchange Python REST Client](https://github.com/delta-exchange/python-rest-client/blob/master/README.md)**: HMAC-SHA256 authentication conventions.
- **[Official Delta Exchange API Documentation](https://docs.delta.exchange/)**:
  - `GET /v2/products`
  - `GET /v2/tickers`
  - `POST /v2/orders`
  - `DELETE /v2/orders/all`
  - `POST /v2/positions/close_all`
  - `GET /v2/positions/margined`

---

## ⚠️ Risk Disclosure

Trading cryptocurrency derivatives and options involves substantial risk of financial loss. Ensure you verify order parameters, contract specifications, and position sizing before executing live trades.
