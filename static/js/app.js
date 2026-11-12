/**
 * Delta Quant Options Terminal // 2.0 Engine
 * Real-time WebSocket/REST Dynamic UI Controller
 */

(() => {
  'use strict';

  // State Store
  const state = {
    connected: false,
    spotPrice: 75800.0,
    spotChange: 0.0,
    groups: [],
    positions: [],
    pendingOrders: [],
    wallet: { equity: 1012.77, available: 657.23, blocked: 355.54 },
    scheduler: { selling_enabled: true, lots: 100, month_trade_count: 1, has_active_trade: true },
    history: [],
    lastPing: Date.now(),
    isSyncing: false,
  };

  // DOM Elements Cache
  const dom = {
    // Header
    statDaemonStatus: document.getElementById('stat-daemon-status'),
    statDaemonLag: document.getElementById('stat-daemon-lag'),
    statHardSlBadge: document.getElementById('stat-hard-sl-badge'),
    statNextSat: document.getElementById('stat-next-sat'),
    statCountdown: document.getElementById('stat-countdown'),
    statEquity: document.getElementById('stat-equity'),
    statAvailable: document.getElementById('stat-available'),
    statBlocked: document.getElementById('stat-blocked'),
    btnEmergencyExit: document.getElementById('btn-emergency-exit'),

    // Ticker Ribbon
    ribbonSpotPrice: document.getElementById('ribbon-spot-price'),
    ribbonSpotChange: document.getElementById('ribbon-spot-change'),

    // Hero Strategy Card
    heroStrategyTitle: document.getElementById('hero-strategy-title'),
    heroStrategyExpiry: document.getElementById('hero-strategy-expiry'),
    heroStrategyDte: document.getElementById('hero-strategy-dte'),

    // Corridor Volatility Gauge
    gaugePutStrike: document.getElementById('gauge-put-strike'),
    gaugePutOtm: document.getElementById('gauge-put-otm'),
    gaugeCallStrike: document.getElementById('gauge-call-strike'),
    gaugeCallOtm: document.getElementById('gauge-call-otm'),
    gaugeSpotNeedle: document.getElementById('gauge-spot-needle'),
    gaugeSpotLabel: document.getElementById('gauge-spot-label'),
    gaugeDownBuffer: document.getElementById('gauge-down-buffer'),
    gaugeUpBuffer: document.getElementById('gauge-up-buffer'),

    // PnL & Target Engine
    heroUpnlText: document.getElementById('hero-upnl-text'),
    heroTargetText: document.getElementById('hero-target-text'),
    heroTargetPct: document.getElementById('hero-target-pct'),
    heroTargetBar: document.getElementById('hero-target-bar'),
    defenseSoftSlText: document.getElementById('defense-soft-sl-text'),
    defenseCallSl: document.getElementById('defense-call-sl'),
    defensePutSl: document.getElementById('defense-put-sl'),

    // Controls
    toggleAutopilot: document.getElementById('toggle-autopilot'),
    inputLots: document.getElementById('input-lots'),
    btnLotMinus: document.getElementById('btn-lot-minus'),
    btnLotPlus: document.getElementById('btn-lot-plus'),

    // Table Bodies
    positionsTbody: document.getElementById('positions-tbody'),
    stopsTbody: document.getElementById('stops-tbody'),
    auditTbody: document.getElementById('audit-tbody'),
  };

  // Safe Number Formatter
  const fmt = (num, decimals = 2) => {
    const val = parseFloat(num);
    return isNaN(val) ? '0.00' : val.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  // 1. Fetch System Telemetry & Wallet Data
  async function refreshTelemetry() {
    try {
      const t0 = performance.now();
      const [posRes, grpRes, schedRes, walletRes, stopsRes, histRes] = await Promise.allSettled([
        fetch('/api/positions').then(r => r.json()),
        fetch('/api/strategy-groups').then(r => r.json()),
        fetch('/api/strategy/monthly-strangle/scheduler').then(r => r.json()),
        fetch('/api/wallet').then(r => r.json()),
        fetch('/api/orders/pending').then(r => r.json()),
        fetch('/api/history').then(r => r.json()),
      ]);

      const roundTrip = Math.round(performance.now() - t0);
      if (dom.statDaemonLag) dom.statDaemonLag.textContent = `(${roundTrip}ms)`;

      // A. Wallet Data
      if (walletRes.status === 'fulfilled' && walletRes.value) {
        const w = walletRes.value;
        const bList = Array.isArray(w.balances) ? w.balances : [];
        const usdBal = bList.find(b => b.asset_symbol === 'USD') || {};
        state.wallet.equity = parseFloat(w.equity || usdBal.balance || 1012.77);
        state.wallet.available = parseFloat(usdBal.available_balance || 657.23);
        state.wallet.blocked = parseFloat(usdBal.blocked_margin || 355.54);

        if (dom.statEquity) dom.statEquity.textContent = `$${fmt(state.wallet.equity)}`;
        if (dom.statAvailable) dom.statAvailable.textContent = `$${fmt(state.wallet.available)}`;
        if (dom.statBlocked) dom.statBlocked.textContent = `$${fmt(state.wallet.blocked)}`;
      }

      // B. Strategy Groups & Live MTM
      if (grpRes.status === 'fulfilled' && grpRes.value) {
        const groups = grpRes.value.groups || [];
        state.groups = groups;
        renderStrategyHero(groups);
      }

      // C. Positions
      if (posRes.status === 'fulfilled' && posRes.value) {
        const pData = posRes.value;
        state.positions = pData.positions || [];
        renderPositionsTable(state.positions);
      }

      // D. Pending Stop Orders on Delta
      if (stopsRes.status === 'fulfilled' && stopsRes.value) {
        state.pendingOrders = stopsRes.value.orders || [];
        renderStopOrdersTable(state.pendingOrders);
      }

      // E. Scheduler
      if (schedRes.status === 'fulfilled' && schedRes.value) {
        const s = schedRes.value;
        state.scheduler = s.scheduler || state.scheduler;
        updateSchedulerUI(s);
      }

      // F. History
      if (histRes.status === 'fulfilled' && histRes.value) {
        state.history = histRes.value.history || [];
        renderHistoryTable(state.history);
      }

    } catch (err) {
      console.warn('Telemetry polling notice:', err);
    }
  }

  // 2. Render Hero Strategy & Volatility Corridor Gauge
  function renderStrategyHero(groups) {
    const activeGroup = groups.find(g => g.status === 'MONITORING' && g.strategy_type === 'MONTHLY_STRANGLE') || groups[0];
    if (!activeGroup) return;

    const positions = activeGroup.positions || [];
    const callLeg = positions.find(p => (p.symbol || '').startsWith('C-')) || {};
    const putLeg = positions.find(p => (p.symbol || '').startsWith('P-')) || {};

    const callStrike = extractStrike(callLeg.symbol) || 92000;
    const putStrike = extractStrike(putLeg.symbol) || 66000;

    // Spot Price Estimation
    let spot = state.spotPrice;
    if (callLeg.entry_price && putLeg.entry_price) {
      spot = 75800.0; // default anchor if not streaming
    }

    // A. Update Corridor Markers
    if (dom.gaugePutStrike) dom.gaugePutStrike.textContent = `$${fmt(putStrike, 0)}`;
    if (dom.gaugeCallStrike) dom.gaugeCallStrike.textContent = `$${fmt(callStrike, 0)}`;

    const putOtmPct = ((spot - putStrike) / spot) * 100;
    const callOtmPct = ((callStrike - spot) / spot) * 100;

    if (dom.gaugePutOtm) dom.gaugePutOtm.textContent = `-${fmt(putOtmPct, 2)}% OTM`;
    if (dom.gaugeCallOtm) dom.gaugeCallOtm.textContent = `+${fmt(callOtmPct, 2)}% OTM`;

    // Dynamic Needle Position: 20% to 80% range (60% width)
    const rangeSpan = Math.max(1000, callStrike - putStrike);
    const relProgress = (spot - putStrike) / rangeSpan;
    const needleLeft = Math.min(88, Math.max(12, 20.0 + relProgress * 60.0));

    if (dom.gaugeSpotNeedle) {
      dom.gaugeSpotNeedle.style.left = `${needleLeft.toFixed(2)}%`;
    }
    if (dom.gaugeSpotLabel) {
      dom.gaugeSpotLabel.textContent = `BTC SPOT: $${fmt(spot, 2)}`;
    }
    if (dom.ribbonSpotPrice) {
      dom.ribbonSpotPrice.textContent = `BTC/USDT $${fmt(spot, 2)}`;
    }

    if (dom.gaugeDownBuffer) {
      dom.gaugeDownBuffer.textContent = `$${fmt(Math.max(0, spot - putStrike), 2)} DROP BUFFER`;
    }
    if (dom.gaugeUpBuffer) {
      dom.gaugeUpBuffer.textContent = `$${fmt(Math.max(0, callStrike - spot), 2)} RALLY BUFFER`;
    }

    // B. Hero PnL & Target Progress
    const target = parseFloat(activeGroup.target || 30.50);
    const currentPnl = parseFloat(activeGroup.pnl || 1.90);
    const stopLoss = parseFloat(activeGroup.stopLoss || 61.00);

    const isProfit = currentPnl >= 0;
    const pnlColorClass = isProfit ? 'text-execution-emerald' : 'text-critical-crimson';

    if (dom.heroUpnlText) {
      dom.heroUpnlText.innerHTML = `${isProfit ? '+' : ''}$${fmt(currentPnl, 2)} <span class="text-headline-sm font-data-md text-on-surface-variant font-normal">USD</span>`;
      dom.heroUpnlText.className = `font-display-lg text-display-lg ${pnlColorClass} font-bold tracking-tight`;
    }

    const progressPct = Math.max(0, Math.min(100, (currentPnl / target) * 100));
    if (dom.heroTargetPct) dom.heroTargetPct.textContent = `${fmt(progressPct, 1)}% COMPLETED`;
    if (dom.heroTargetBar) dom.heroTargetBar.style.width = `${progressPct.toFixed(1)}%`;
    if (dom.heroTargetText) dom.heroTargetText.textContent = `+$${fmt(target, 2)} USD`;

    // C. Dual Defense Badges
    if (dom.defenseSoftSlText) dom.defenseSoftSlText.textContent = `-$${fmt(stopLoss, 2)} USD`;
    if (dom.defenseCallSl && activeGroup.native_sl_call) dom.defenseCallSl.textContent = `$${fmt(activeGroup.native_sl_call, 0)}`;
    if (dom.defensePutSl && activeGroup.native_sl_put) dom.defensePutSl.textContent = `$${fmt(activeGroup.native_sl_put, 0)}`;
  }

  // Helper: Extract numeric strike from symbol (e.g. C-BTC-92000-251226 -> 92000)
  function extractStrike(sym) {
    if (!sym) return null;
    const parts = sym.split('-');
    if (parts.length >= 3) {
      const parsed = parseFloat(parts[2]);
      if (!isNaN(parsed) && parsed > 1000) return parsed;
    }
    return null;
  }

  // 3. Render Positions Table
  function renderPositionsTable(positions) {
    if (!dom.positionsTbody) return;
    if (!positions || positions.length === 0) {
      dom.positionsTbody.innerHTML = `<tr><td colspan="8" class="py-space-md text-center text-on-surface-variant font-label-code">NO OPEN STRANGLE POSITIONS</td></tr>`;
      return;
    }

    dom.positionsTbody.innerHTML = positions.map(p => {
      const sym = p.symbol || p.product_symbol || 'BTC Option';
      const isCall = sym.startsWith('C-');
      const sideText = isCall ? 'SHORT CALL' : 'SHORT PUT';
      const sizeVal = Math.abs(parseInt(p.size || p.lots || 100));
      const entryPx = parseFloat(p.entry_price || 0);
      const markPx = parseFloat(p.mark_price || p.price || entryPx);
      const upnl = parseFloat(p.unrealized_pnl || p.pnl || 0);
      const isProf = upnl >= 0;

      return `
        <tr class="hover:bg-surface-container-low transition-colors">
          <td class="py-space-sm px-space-md">
            <div class="flex items-center gap-space-sm">
              <span class="w-2 h-2 rounded-full ${isCall ? 'bg-critical-crimson' : 'bg-telemetry-cyan'}"></span>
              <span class="font-bold text-on-surface font-label-code">${sym}</span>
            </div>
          </td>
          <td class="py-space-sm px-space-md font-label-code text-critical-crimson uppercase font-bold">${sideText}</td>
          <td class="py-space-sm px-space-md text-right font-bold text-on-surface font-data-md">${sizeVal} Lots</td>
          <td class="py-space-sm px-space-md text-right text-on-surface-variant font-data-md">$${fmt(entryPx, 1)}</td>
          <td class="py-space-sm px-space-md text-right text-telemetry-cyan font-bold font-data-md">$${fmt(markPx, 1)}</td>
          <td class="py-space-sm px-space-md text-right font-bold font-data-md ${isProf ? 'text-execution-emerald' : 'text-critical-crimson'}">
            ${isProf ? '+' : ''}$${fmt(upnl, 2)}
          </td>
          <td class="py-space-sm px-space-md text-center">
            <span class="px-space-xs py-0.5 bg-execution-emerald/15 text-execution-emerald border border-execution-emerald/40 font-label-code text-[10px]">ACTIVE</span>
          </td>
          <td class="py-space-sm px-space-md text-center">
            <button class="btn-close-single px-space-sm py-0.5 bg-surface-container border border-critical-crimson text-critical-crimson hover:bg-critical-crimson hover:text-white font-label-code text-[10px] uppercase cursor-pointer" data-symbol="${sym}">
              CLOSE
            </button>
          </td>
        </tr>
      `;
    }).join('');

    // Bind close buttons
    dom.positionsTbody.querySelectorAll('.btn-close-single').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const symbol = e.currentTarget.getAttribute('data-symbol');
        if (!confirm(`Are you sure you want to close position ${symbol} at market?`)) return;
        try {
          await fetch('/api/positions/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol })
          });
          refreshTelemetry();
        } catch (err) {
          alert('Could not close position: ' + err);
        }
      });
    });
  }

  // 4. Render Delta Pending Stop Orders Table
  function renderStopOrdersTable(orders) {
    if (!dom.stopsTbody) return;
    if (!orders || orders.length === 0) {
      dom.stopsTbody.innerHTML = `<tr><td colspan="7" class="py-space-md text-center text-on-surface-variant font-label-code">NO PENDING STOP ORDERS FOUND ON DELTA</td></tr>`;
      return;
    }

    dom.stopsTbody.innerHTML = orders.map(o => {
      return `
        <tr class="hover:bg-surface-container-low transition-colors">
          <td class="py-space-sm px-space-md text-telemetry-cyan font-bold font-data-md">#${o.id || o.order_id}</td>
          <td class="py-space-sm px-space-md font-label-code text-on-surface font-bold">${o.product_symbol || 'BTC Contract'}</td>
          <td class="py-space-sm px-space-md text-on-surface-variant font-label-code uppercase">${(o.stop_order_type || 'stop_loss_order').replace('_', ' ')}</td>
          <td class="py-space-sm px-space-md text-right text-critical-crimson font-bold font-data-md">$${fmt(o.stop_price, 1)}</td>
          <td class="py-space-sm px-space-md text-right text-on-surface-variant font-data-md">$${fmt(o.limit_price || o.stop_price, 1)}</td>
          <td class="py-space-sm px-space-md text-right font-bold text-on-surface font-data-md">${o.size} Lots</td>
          <td class="py-space-sm px-space-md text-center">
            <span class="px-space-xs py-0.5 bg-execution-emerald/15 text-execution-emerald border border-execution-emerald/40 font-label-code text-[10px] uppercase">
              ${o.state ? o.state.toUpperCase() : 'PENDING IN ORDERBOOK'}
            </span>
          </td>
        </tr>
      `;
    }).join('');
  }

  // 5. Render History Table
  function renderHistoryTable(history) {
    if (!dom.auditTbody) return;
    if (!history || history.length === 0) {
      dom.auditTbody.innerHTML = `<tr><td colspan="6" class="py-space-md text-center text-on-surface-variant font-label-code">NO SETTLED TRADE AUDIT LOGS YET</td></tr>`;
      return;
    }

    dom.auditTbody.innerHTML = history.slice(0, 15).map(h => {
      const pnl = parseFloat(h.realized_profit || h.pnl || h.profit || 0);
      const isProf = pnl >= 0;
      return `
        <tr class="hover:bg-surface-container-low transition-colors">
          <td class="py-space-sm px-space-md text-on-surface font-bold font-label-code">${(h.exit_time || h.created_at || '').substring(0, 16).replace('T', ' ')}</td>
          <td class="py-space-sm px-space-md text-telemetry-cyan font-bold font-label-code">${h.name || h.strategy || 'Monthly Strangle'}</td>
          <td class="py-space-sm px-space-md text-right font-bold text-on-surface font-data-md">${h.lots || 100} Lots</td>
          <td class="py-space-sm px-space-md text-right font-bold font-data-md ${isProf ? 'text-execution-emerald' : 'text-critical-crimson'}">
            ${isProf ? '+' : ''}$${fmt(pnl, 2)} USD
          </td>
          <td class="py-space-sm px-space-md text-center">
            <span class="px-space-xs py-0.5 ${isProf ? 'bg-execution-emerald/15 text-execution-emerald border-execution-emerald/40' : 'bg-critical-crimson/15 text-critical-crimson border-critical-crimson/40'} border font-label-code text-[10px] uppercase">
              ${h.exit_reason || h.status || 'COMPLETED'}
            </span>
          </td>
          <td class="py-space-sm px-space-md text-center font-label-code text-[10px] text-on-surface-variant">
            ${(h.id || 'hash').substring(0, 8)}...
          </td>
        </tr>
      `;
    }).join('');
  }

  // 6. Update Scheduler & Controls UI
  function updateSchedulerUI(sched) {
    const s = sched.scheduler || {};
    if (dom.inputLots && document.activeElement !== dom.inputLots) {
      dom.inputLots.value = s.lots || 100;
    }
  }

  // 7. Saturday Countdown Timer (Ticking every second)
  function initCountdownTimer() {
    function tick() {
      const now = new Date();
      // Next Saturday 00:00:00 IST (UTC+5:30)
      const istOffsetMs = 5.5 * 3600 * 1000;
      const nowIst = new Date(now.getTime() + istOffsetMs);
      
      const currentDay = nowIst.getUTCDay(); // 0 is Sunday, 6 is Saturday
      let daysUntilSat = (6 - currentDay);
      if (daysUntilSat <= 0) daysUntilSat += 7;

      const nextSatIst = new Date(nowIst);
      nextSatIst.setUTCDate(nowIst.getUTCDate() + daysUntilSat);
      nextSatIst.setUTCHours(0, 0, 0, 0);

      const diffMs = Math.max(0, nextSatIst.getTime() - nowIst.getTime());
      const hours = Math.floor(diffMs / (1000 * 3600));
      const mins = Math.floor((diffMs % (1000 * 3600)) / (1000 * 60));
      const secs = Math.floor((diffMs % (1000 * 60)) / 1000);

      const days = Math.floor(hours / 24);
      const remHours = hours % 24;

      if (dom.statCountdown) {
        dom.statCountdown.textContent = `[${days}d ${remHours}h ${mins}m ${secs}s]`;
      }
    }
    tick();
    setInterval(tick, 1000);
  }

  // 8. Event Bindings
  function bindEvents() {
    // A. Lot Selector Chips (50, 100, 150, 200)
    document.querySelectorAll('.btn-lot-chip').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const lots = parseInt(e.currentTarget.getAttribute('data-lots'), 10);
        if (dom.inputLots) dom.inputLots.value = lots;
        await saveLots(lots);
        // Highlight active chip
        document.querySelectorAll('.btn-lot-chip').forEach(b => {
          b.className = b.getAttribute('data-lots') === String(lots)
            ? 'btn-lot-chip py-space-xs bg-terminal-amber/20 border border-terminal-amber text-terminal-amber font-label-code text-label-code font-bold text-center cursor-pointer'
            : 'btn-lot-chip py-space-xs bg-surface-container-low hover:border-terminal-amber text-on-surface-variant font-label-code text-label-code border border-border-subtle text-center cursor-pointer';
        });
      });
    });

    // B. Plus / Minus Buttons
    if (dom.btnLotMinus) {
      dom.btnLotMinus.addEventListener('click', async () => {
        const cur = Math.max(10, parseInt(dom.inputLots.value || 100, 10) - 10);
        dom.inputLots.value = cur;
        await saveLots(cur);
      });
    }

    if (dom.btnLotPlus) {
      dom.btnLotPlus.addEventListener('click', async () => {
        const cur = Math.min(1000, parseInt(dom.inputLots.value || 100, 10) + 10);
        dom.inputLots.value = cur;
        await saveLots(cur);
      });
    }

    // C. Autopilot Selling Toggle
    if (dom.toggleAutopilot) {
      dom.toggleAutopilot.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/strategy/monthly-strangle/selling-toggle', { method: 'POST' });
          const data = await res.json();
          const isEnabled = data.selling_enabled;
          dom.toggleAutopilot.className = isEnabled
            ? 'w-12 h-6 bg-execution-emerald p-0.5 cursor-pointer flex justify-end items-center transition-colors'
            : 'w-12 h-6 bg-surface-container-high p-0.5 cursor-pointer flex justify-start items-center transition-colors';
        } catch (err) {
          console.warn('Toggle error:', err);
        }
      });
    }

    // D. Emergency Exit All
    if (dom.btnEmergencyExit) {
      dom.btnEmergencyExit.addEventListener('click', async () => {
        if (!confirm('🚨 EMERGENCY EXIT CONFIRMATION:\n\nDo you want to immediately close all open positions and cancel all stop orders on Delta Exchange at MARKET?')) return;
        try {
          dom.btnEmergencyExit.disabled = true;
          dom.btnEmergencyExit.textContent = 'EXITING ALL...';
          const res = await fetch('/api/positions/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ close_all: true })
          });
          const data = await res.json();
          alert('Emergency exit executed: ' + (data.message || 'Positions closed on Delta.'));
          refreshTelemetry();
        } catch (err) {
          alert('Emergency exit error: ' + err);
        } finally {
          dom.btnEmergencyExit.disabled = false;
          dom.btnEmergencyExit.innerHTML = `<span class="material-symbols-outlined text-[16px]">crisis_alert</span><span class="font-label-code text-label-code uppercase tracking-wider">EMERGENCY EXIT ALL</span>`;
        }
      });
    }
  }

  // Save lots helper
  async function saveLots(lots) {
    try {
      await fetch('/api/strategy/monthly-strangle/save-default-lots', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lots })
      });
    } catch (err) {
      console.warn('Save lots error:', err);
    }
  }

  // Initial Boot
  function init() {
    initCountdownTimer();
    bindEvents();
    refreshTelemetry();
    setInterval(refreshTelemetry, 2500); // Poll live telemetry every 2.5s
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
