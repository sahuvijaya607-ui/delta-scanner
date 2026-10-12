/**
 * Delta Exchange Professional Option Chain Dashboard
 * Localhost frontend controller for real-time 1-second scanning and live execution.
 */

(() => {
  'use strict';

  // --- State Container ---
  const state = {
    asset: 'BTC',
    expiry: null,
    expiries: [],
    isScanning: false,
    isConnected: false,
    environment: 'india',
    scanTimer: null,
    scanCycles: 0,
    selectedContract: null,
    selectedSide: 'buy',
    soundEnabled: true,
    previousPrices: new Map(), // key: strike_call/put -> { last, bid, ask }

    // Multi-leg Strategy Groups state
    currentPositions: [],
    selectedPositionKeys: new Set(),
    strategyGroups: [],

    // Advanced Institutional Features state
    showGreeks: false,
    strikeFilterRange: '10',
    rawChainData: null,
    maxDepths: { maxCallOi: 1, maxPutOi: 1, maxCallVol: 1, maxPutVol: 1 },
    isFetchingChain: false,
    isFetchingPositions: false,
  };

  // --- Element Selectors ---
  const el = (id) => document.getElementById(id);

  const dom = {
    // Topbar & status
    clockText: el('clockText'),
    audioToggle: el('audioToggle'),
    audioIcon: el('audioIcon'),
    audioText: el('audioText'),
    walletPill: el('walletPill'),
    walletLabel: el('walletLabel'),
    connPill: el('connPill'),
    connBeacon: el('connBeacon'),
    connLabel: el('connLabel'),
    assetTabs: document.querySelectorAll('.asset-tab'),

    // Control bar
    envSelect: el('envSelect'),
    apiKey: el('apiKey'),
    apiSecret: el('apiSecret'),
    saveConnBtn: el('saveConnBtn'),
    clearConnBtn: el('clearConnBtn'),
    expirySelect: el('expirySelect'),
    startScanBtn: el('startScanBtn'),
    stopScanBtn: el('stopScanBtn'),
    feedBeacon: el('feedBeacon'),
    feedTitle: el('feedTitle'),
    feedDesc: el('feedDesc'),

    // Metrics
    valSpot: el('valSpot'),
    spotTag: el('spotTag'),
    valSpotDesc: el('valSpotDesc'),
    valAtm: el('valAtm'),
    valAtmDistance: el('valAtmDistance'),
    valMaxPain: el('valMaxPain'),
    valMaxPainDiff: el('valMaxPainDiff'),
    valPcr: el('valPcr'),
    pcrSentiment: el('pcrSentiment'),
    valCallOi: el('valCallOi'),
    valCallVol: el('valCallVol'),
    valPutOi: el('valPutOi'),
    valPutVol: el('valPutVol'),
    valLastUpdate: el('valLastUpdate'),
    valUpdateCount: el('valUpdateCount'),

    // Chain controls & table
    strikeFilter: el('strikeFilter'),
    greeksToggleBtn: el('greeksToggleBtn'),
    greeksBtnText: el('greeksBtnText'),
    exportCsvBtn: el('exportCsvBtn'),
    thCallsHeader: el('thCallsHeader'),
    thPutsHeader: el('thPutsHeader'),
    tableColumnHeaders: el('tableColumnHeaders'),
    chainTitle: el('chainTitle'),
    chainMeta: el('chainMeta'),
    chainBody: el('chainBody'),

    // Ticket
    ticketLockPill: el('ticketLockPill'),
    ticketSymbol: el('ticketSymbol'),
    ticketTag: el('ticketTag'),
    ticketQuotes: el('ticketQuotes'),
    orderSide: el('orderSide'),
    orderType: el('orderType'),
    orderQty: el('orderQty'),
    orderPrice: el('orderPrice'),
    priceInputGroup: el('priceInputGroup'),
    bracketDetails: el('bracketDetails'),
    targetPrice: el('targetPrice'),
    stopLossPrice: el('stopLossPrice'),
    liveCheckbox: el('liveCheckbox'),
    submitOrderBtn: el('submitOrderBtn'),

    // Positions & history
    portfolioUpnlBadge: el('portfolioUpnlBadge'),
    positionsBody: el('positionsBody'),
    refreshPositionsBtn: el('refreshPositionsBtn'),
    exitAllBtn: el('exitAllBtn'),
    historyBody: el('historyBody'),
    selectAllPositions: el('selectAllPositions'),
    batchActionBar: el('batchActionBar'),
    batchCount: el('batchCount'),
    batchPnl: el('batchPnl'),
    openGroupModalBtn: el('openGroupModalBtn'),

    // Strategy Groups
    strategyGroupsContainer: el('strategyGroupsContainer'),
    activeGroupsCount: el('activeGroupsCount'),
    emptyGroupsState: el('emptyGroupsState'),
    groupModal: el('groupModal'),
    groupModalCloseBtn: el('groupModalCloseBtn'),
    groupModalCancelBtn: el('groupModalCancelBtn'),
    groupModalSaveBtn: el('groupModalSaveBtn'),
    groupNameInput: el('groupNameInput'),
    selectedLegsList: el('selectedLegsList'),
    groupCurrentMtm: el('groupCurrentMtm'),
    groupTargetInput: el('groupTargetInput'),
    groupStopLossInput: el('groupStopLossInput'),

    // Modals
    orderModal: el('orderModal'),
    modalCloseBtn: el('modalCloseBtn'),
    modalCancelBtn: el('modalCancelBtn'),
    modalConfirmBtn: el('modalConfirmBtn'),
    mSymbol: el('mSymbol'),
    mAction: el('mAction'),
    mQty: el('mQty'),
    mPrice: el('mPrice'),
    mBracket: el('mBracket'),
    mBracketRow: el('mBracketRow'),

    // Exit All modal
    exitModal: el('exitModal'),
    exitModalCloseBtn: el('exitModalCloseBtn'),
    exitModalCancelBtn: el('exitModalCancelBtn'),
    exitModalConfirmBtn: el('exitModalConfirmBtn'),
    exitConfirmationInput: el('exitConfirmationInput'),

    // Toasts
    toastShelf: el('toastShelf'),
  };

  // --- Sound Synthesizer (Web Audio API) ---
  let audioCtx = null;

  function initAudio() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume();
    }
  }

  function playTone(freq, duration, type = 'sine', startTimeOffset = 0, gainVal = 0.08) {
    if (!state.soundEnabled || !audioCtx) return;
    try {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      const now = audioCtx.currentTime + startTimeOffset;

      osc.type = type;
      osc.frequency.setValueAtTime(freq, now);

      gain.gain.setValueAtTime(gainVal, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

      osc.connect(gain);
      gain.connect(audioCtx.destination);

      osc.start(now);
      osc.stop(now + duration);
    } catch (e) {
      console.warn('Audio playback error', e);
    }
  }

  function playSound(type) {
    if (!state.soundEnabled) return;
    initAudio();
    if (!audioCtx) return;

    if (type === 'order') {
      // Ascending pleasant terminal confirmation (C5 -> G5)
      playTone(523.25, 0.12, 'triangle', 0, 0.07);
      playTone(783.99, 0.22, 'sine', 0.09, 0.09);
    } else if (type === 'exit') {
      // Urgent distinct double chime (F4 -> A4 -> F4)
      playTone(349.23, 0.14, 'sawtooth', 0, 0.05);
      playTone(440.00, 0.14, 'square', 0.12, 0.05);
      playTone(349.23, 0.25, 'sawtooth', 0.24, 0.05);
    } else if (type === 'error') {
      // Low alert buzz
      playTone(130.81, 0.25, 'sawtooth', 0, 0.08);
    }
  }

  // --- Toast System ---
  function showToast(message, kind = 'info', duration = 4500) {
    const toast = document.createElement('div');
    toast.className = `toast-item ${kind}`;

    const icon = kind === 'success' ? '✅' : kind === 'error' ? '⚠️' : 'ℹ️';
    toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;

    dom.toastShelf.appendChild(toast);
    setTimeout(() => {
      toast.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // --- Number & Text Formatting ---
  function formatMoney(val, maxDecimals = 2) {
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    return num.toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxDecimals,
    });
  }

  function formatInt(val) {
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    return Math.round(num).toLocaleString('en-US');
  }

  function formatPct(val) {
    const num = Number(val);
    if (!Number.isFinite(num)) return '—';
    return `${num.toFixed(1)}%`;
  }

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // --- Network API Client ---
  async function apiFetch(url, options = {}) {
    const res = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || `HTTP ${res.status}: ${res.statusText}`);
    }
    return data;
  }

  // --- Live IST Clock ---
  function tickClock() {
    try {
      const now = new Date();
      const timeStr = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(now);
      dom.clockText.textContent = `${timeStr} IST`;
    } catch (_) {
      dom.clockText.textContent = new Date().toLocaleTimeString();
    }
  }

  // --- Scanner Feed State Management ---
  function updateFeedStateDisplay() {
    if (state.isScanning) {
      dom.feedBeacon.className = 'status-beacon green';
      dom.feedTitle.textContent = 'SCANNER ACTIVE';
      dom.feedDesc.textContent = `Streaming 1s · Cycle #${state.scanCycles}`;
      dom.startScanBtn.disabled = true;
      dom.stopScanBtn.disabled = false;
    } else {
      dom.feedBeacon.className = 'status-beacon muted';
      dom.feedTitle.textContent = 'SCANNER PAUSED';
      dom.feedDesc.textContent = 'Data updates frozen';
      dom.startScanBtn.disabled = false;
      dom.stopScanBtn.disabled = true;
    }
  }

  function startScanner(silent = false) {
    if (state.isScanning) return;
    initAudio();
    state.isScanning = true;
    updateFeedStateDisplay();
    fetchChainSnapshot(); // initial instant cycle
    if (state.isConnected) {
      refreshPositions();
    }

    // Set 1-second continuous scanning interval
    state.scanTimer = setInterval(() => {
      fetchChainSnapshot();
      // Continuously refresh account positions every second if connected
      if (state.isConnected) {
        refreshPositions();
      }
      // Sync multi-leg strategy groups across devices every 2 cycles (2s)
      if (state.scanCycles % 2 === 0) {
        syncStrategyGroups();
      }
    }, 1000);

    if (!silent) {
      showToast(`Scanner started: Streaming ${state.asset} options every second`, 'info', 2500);
    }
  }

  function stopScanner() {
    if (!state.isScanning) return;
    state.isScanning = false;
    clearInterval(state.scanTimer);
    state.scanTimer = null;
    updateFeedStateDisplay();
    showToast('Scanner paused. Updates frozen.', 'info', 2500);
  }

  // --- Option Chain Snapshot Fetching & Rendering ---
  async function fetchChainSnapshot() {
    if (state.isFetchingChain) return;
    state.isFetchingChain = true;

    try {
      const params = new URLSearchParams({ underlying: state.asset });
      if (state.expiry) {
        params.set('expiry', state.expiry);
      }

      const data = await apiFetch(`/api/chain?${params.toString()}`);
      state.scanCycles++;
      renderChainData(data);
      updateFeedStateDisplay();
    } catch (err) {
      console.error('Scan fetch error:', err);
      dom.chainMeta.textContent = `Error: ${err.message}`;
      if (!dom.chainBody.querySelector('tr[data-strike]')) {
        dom.chainBody.innerHTML = `
          <tr class="empty-row">
            <td colspan="13" class="empty-cell">
              <div class="empty-state">
                <div class="empty-icon">⚠️</div>
                <div class="empty-title">Feed Connectivity Issue</div>
                <div class="empty-sub">${escapeHtml(err.message)}</div>
              </div>
            </td>
          </tr>
        `;
      }
    } finally {
      state.isFetchingChain = false;
    }
  }

  function renderChainData(data) {
    state.rawChainData = data;
    state.maxDepths = {
      maxCallOi: data.max_call_oi || 1,
      maxPutOi: data.max_put_oi || 1,
      maxCallVol: data.max_call_vol || 1,
      maxPutVol: data.max_put_vol || 1,
    };

    // 1. Expiries Dropdown Maintenance
    state.expiries = data.expiries || [];
    const currentSelected = data.expiry || state.expiry;
    const existingVal = dom.expirySelect.value;

    if (state.expiries.length > 0) {
      const optionsHtml = state.expiries
        .map((e) => `<option value="${e.value}" ${e.value === currentSelected ? 'selected' : ''}>${e.label}</option>`)
        .join('');
      if (dom.expirySelect.innerHTML !== optionsHtml) {
        dom.expirySelect.innerHTML = optionsHtml;
      }
    } else {
      dom.expirySelect.innerHTML = '<option value="">No Expiries Listed</option>';
    }
    state.expiry = currentSelected;

    // 2. Metrics Bar Update
    dom.valSpot.textContent = formatMoney(data.spot, state.asset === 'XAUT' ? 2 : 1);
    dom.spotTag.textContent = `${data.underlying || state.asset} INDEX`;
    dom.valSpotDesc = `${data.underlying || state.asset} underlying index`;

    if (data.atm) {
      dom.valAtm.textContent = formatMoney(data.atm, 0);
      const diff = data.spot ? (data.atm - data.spot) : 0;
      const diffStr = diff >= 0 ? `+${diff.toFixed(1)}` : diff.toFixed(1);
      dom.valAtmDistance.textContent = `Diff: ${diffStr} from spot`;
    } else {
      dom.valAtm.textContent = '—';
      dom.valAtmDistance.textContent = 'Awaiting strike data';
    }

    // Max Pain Strike Display
    if (data.max_pain) {
      dom.valMaxPain.textContent = formatMoney(data.max_pain, 0);
      const mpDiff = data.spot ? (data.max_pain - data.spot) : 0;
      const mpDiffStr = mpDiff >= 0 ? `+${mpDiff.toFixed(1)}` : mpDiff.toFixed(1);
      dom.valMaxPainDiff.textContent = `Diff: ${mpDiffStr} from spot`;
    } else {
      dom.valMaxPain.textContent = '—';
      dom.valMaxPainDiff.textContent = 'Lowest writer payout';
    }

    // PCR Sentiment
    if (data.pcr != null && Number.isFinite(data.pcr)) {
      const pcrVal = Number(data.pcr);
      dom.valPcr.textContent = pcrVal.toFixed(2);
      if (pcrVal > 1.25) {
        dom.pcrSentiment.textContent = 'BULLISH';
        dom.pcrSentiment.className = 'metric-sentiment sentiment-bullish';
      } else if (pcrVal < 0.85) {
        dom.pcrSentiment.textContent = 'BEARISH';
        dom.pcrSentiment.className = 'metric-sentiment sentiment-bearish';
      } else {
        dom.pcrSentiment.textContent = 'NEUTRAL';
        dom.pcrSentiment.className = 'metric-sentiment sentiment-neutral';
      }
    } else {
      dom.valPcr.textContent = '—';
      dom.pcrSentiment.textContent = '—';
      dom.pcrSentiment.className = 'metric-sentiment';
    }

    dom.valCallOi.textContent = formatMoney(data.call_oi, 1);
    dom.valCallVol.textContent = `Vol: ${formatMoney(data.call_vol, 1)}`;
    dom.valPutOi.textContent = formatMoney(data.put_oi, 1);
    dom.valPutVol.textContent = `Vol: ${formatMoney(data.put_vol, 1)}`;

    const dateUpdated = data.updated_at ? new Date(data.updated_at) : new Date();
    dom.valLastUpdate.textContent = dateUpdated.toLocaleTimeString('en-IN', { hour12: false });
    dom.valUpdateCount.textContent = `${state.scanCycles} scan cycles`;

    // 3. Header title & meta
    dom.chainTitle.textContent = `${data.underlying || state.asset} OPTION CHAIN`;
    const expObj = state.expiries.find((e) => e.value === data.expiry);
    const expLabel = expObj ? expObj.label : data.expiry || '';
    dom.chainMeta.textContent = `Expiry: ${expLabel} · Spot: $${formatMoney(data.spot)} · Max Pain: $${formatMoney(data.max_pain || 0, 0)}`;

    // 4. Filter Rows by Strike Range
    const allRows = data.rows || [];
    if (!allRows.length) {
      dom.chainBody.innerHTML = `
        <tr class="empty-row">
          <td colspan="13" class="empty-cell">
            <div class="empty-state">
              <div class="empty-icon">📭</div>
              <div class="empty-title">No Contracts Available</div>
              <div class="empty-sub">No listed option strikes found for the selected expiry.</div>
            </div>
          </td>
        </tr>
      `;
      return;
    }

    const filteredRows = filterRowsByRange(allRows, data.atm);
    renderTableRowsSmooth(filteredRows, data.atm, data.max_pain);
  }

  function filterRowsByRange(rows, atmStrike) {
    const range = state.strikeFilterRange;
    if (range === 'all') return rows;

    const count = parseInt(range, 10);
    if (!count || count <= 0) return rows;

    let atmIdx = rows.findIndex((r) => r.strike === atmStrike);
    if (atmIdx === -1) {
      // Find closest strike index
      atmIdx = Math.floor(rows.length / 2);
    }

    const startIdx = Math.max(0, atmIdx - count);
    const endIdx = Math.min(rows.length, atmIdx + count + 1);
    return rows.slice(startIdx, endIdx);
  }

  function updateTableHeaderForViewMode() {
    if (state.showGreeks) {
      dom.tableColumnHeaders.innerHTML = `
        <!-- CALLS GREEKS -->
        <th class="col-delta" title="Call Delta">DELTA</th>
        <th class="col-theta" title="Call Theta">THETA</th>
        <th class="col-iv" title="Implied Volatility">IV %</th>
        <th class="col-bid" title="Best Bid Price">BID</th>
        <th class="col-ask" title="Best Ask Price">ASK</th>
        <th class="col-last-act" title="Last Traded Price & Action Buttons">LAST / ACTION</th>

        <!-- STRIKE COLUMN -->
        <th class="col-strike" title="Option Strike Price">STRIKE</th>

        <!-- PUTS GREEKS -->
        <th class="col-last-act" title="Last Traded Price & Action Buttons">LAST / ACTION</th>
        <th class="col-bid" title="Best Bid Price">BID</th>
        <th class="col-ask" title="Best Ask Price">ASK</th>
        <th class="col-iv" title="Implied Volatility">IV %</th>
        <th class="col-theta" title="Put Theta">THETA</th>
        <th class="col-delta" title="Put Delta">DELTA</th>
      `;
    } else {
      dom.tableColumnHeaders.innerHTML = `
        <!-- CALLS STANDARD -->
        <th class="col-oi" title="Open Interest">OI</th>
        <th class="col-vol" title="Trading Volume">VOL</th>
        <th class="col-iv" title="Implied Volatility">IV %</th>
        <th class="col-bid" title="Best Bid Price">BID</th>
        <th class="col-ask" title="Best Ask Price">ASK</th>
        <th class="col-last-act" title="Last Traded Price & Action Buttons">LAST / ACTION</th>

        <!-- STRIKE COLUMN -->
        <th class="col-strike" title="Option Strike Price">STRIKE</th>

        <!-- PUTS STANDARD -->
        <th class="col-last-act" title="Last Traded Price & Action Buttons">LAST / ACTION</th>
        <th class="col-bid" title="Best Bid Price">BID</th>
        <th class="col-ask" title="Best Ask Price">ASK</th>
        <th class="col-iv" title="Implied Volatility">IV %</th>
        <th class="col-vol" title="Trading Volume">VOL</th>
        <th class="col-oi" title="Open Interest">OI</th>
      `;
    }
  }

  // --- Smooth Table Updates with Price Tick Flashing & Visual Depth Bars ---
  function renderTableRowsSmooth(rows, atmStrike, maxPainStrike) {
    const existingRowElements = dom.chainBody.querySelectorAll('tr[data-strike]');

    // If row count or strikes don't match existing DOM, do a full initial rebuild
    let needsFullRebuild = existingRowElements.length !== rows.length;
    if (!needsFullRebuild) {
      for (let i = 0; i < rows.length; i++) {
        if (Number(existingRowElements[i].dataset.strike) !== rows[i].strike) {
          needsFullRebuild = true;
          break;
        }
      }
    }

    if (needsFullRebuild) {
      const fragment = document.createDocumentFragment();
      rows.forEach((row) => {
        const tr = document.createElement('tr');
        tr.dataset.strike = row.strike;
        const isAtm = row.strike === atmStrike;
        const isMaxPain = row.strike === maxPainStrike;
        tr.className = (isAtm ? 'row-atm' : '') + (isMaxPain ? ' row-maxpain' : '');
        tr.innerHTML = generateRowHtml(row, atmStrike, maxPainStrike);
        fragment.appendChild(tr);
      });
      dom.chainBody.innerHTML = '';
      dom.chainBody.appendChild(fragment);
      attachOrderActionListeners();
      return;
    }

    // In-place cell updates
    existingRowElements.forEach((tr, index) => {
      const row = rows[index];
      const isAtm = row.strike === atmStrike;
      const isMaxPain = row.strike === maxPainStrike;
      tr.className = (isAtm ? 'row-atm' : '') + (isMaxPain ? ' row-maxpain' : '');

      updateLegCells(tr, row.call, 'call', row.strike);
      updateLegCells(tr, row.put, 'put', row.strike);
    });
  }

  function generateRowHtml(row, atmStrike, maxPainStrike) {
    const isAtm = row.strike === atmStrike;
    const isMaxPain = row.strike === maxPainStrike;
    const atmBadge = isAtm ? '<span class="atm-pill">ATM</span>' : '';
    const maxPainBadge = isMaxPain ? '<span class="maxpain-pill" title="Max Pain Strike">MAX PAIN</span>' : '';

    return `
      ${generateLegHtml(row.call, 'call')}
      <td class="cell-strike">${formatMoney(row.strike, 0)}${atmBadge}${maxPainBadge}</td>
      ${generateLegHtml(row.put, 'put')}
    `;
  }

  function generateLegHtml(leg, type) {
    if (!leg) {
      return `
        <td>—</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>
      `;
    }

    const payloadStr = escapeHtml(JSON.stringify(leg));
    const chgVal = Number(leg.change_24h || 0);
    const chgSign = chgVal >= 0 ? '+' : '';
    const chgBadge = leg.change_24h != null
      ? `<span class="val-chg ${chgVal >= 0 ? 'pnl-positive' : 'pnl-negative'}" style="font-size: 10px; font-weight: 700; display: block; margin-top: 1px;">${chgSign}${chgVal.toFixed(1)}%</span>`
      : '';

    const actionCell = `
      <div class="cell-actions">
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-width: 48px;">
          <span class="val-last font-mono" data-val="last">${formatMoney(leg.last)}</span>
          ${chgBadge}
        </div>
        <button class="btn-chip chip-buy" data-action="buy" data-contract='${payloadStr}'>BUY</button>
        <button class="btn-chip chip-sell" data-action="sell" data-contract='${payloadStr}'>SELL</button>
      </div>
    `;

    if (state.showGreeks) {
      // Greeks View Mode: Delta, Theta, IV%, Bid, Ask, Last/Action
      const deltaVal = leg.delta != null ? Number(leg.delta).toFixed(3) : '—';
      const thetaVal = leg.theta != null ? Number(leg.theta).toFixed(1) : '—';

      if (type === 'call') {
        return `
          <td class="val-delta" data-val="delta">${deltaVal}</td>
          <td class="val-theta" data-val="theta">${thetaVal}</td>
          <td class="val-iv" data-val="iv">${formatPct(leg.iv)}</td>
          <td class="val-bid" data-val="bid">${formatMoney(leg.bid)}</td>
          <td class="val-ask" data-val="ask">${formatMoney(leg.ask)}</td>
          <td class="col-act">${actionCell}</td>
        `;
      } else {
        return `
          <td class="col-act">${actionCell}</td>
          <td class="val-bid" data-val="bid">${formatMoney(leg.bid)}</td>
          <td class="val-ask" data-val="ask">${formatMoney(leg.ask)}</td>
          <td class="val-iv" data-val="iv">${formatPct(leg.iv)}</td>
          <td class="val-theta" data-val="theta">${thetaVal}</td>
          <td class="val-delta" data-val="delta">${deltaVal}</td>
        `;
      }
    } else {
      // Standard View Mode with Sensibull Style Depth Bars
      const maxOi = type === 'call' ? state.maxDepths.maxCallOi : state.maxDepths.maxPutOi;
      const maxVol = type === 'call' ? state.maxDepths.maxCallVol : state.maxDepths.maxPutVol;

      const oiPct = maxOi > 0 ? Math.min(100, Math.round(((leg.oi || 0) / maxOi) * 100)) : 0;
      const volPct = maxVol > 0 ? Math.min(100, Math.round(((leg.volume || 0) / maxVol) * 100)) : 0;

      const oiBarStyle = type === 'call'
        ? `background: linear-gradient(to left, rgba(56, 189, 248, 0.16) ${oiPct}%, transparent ${oiPct}%);`
        : `background: linear-gradient(to right, rgba(255, 82, 99, 0.16) ${oiPct}%, transparent ${oiPct}%);`;

      const volBarStyle = type === 'call'
        ? `background: linear-gradient(to left, rgba(203, 213, 225, 0.10) ${volPct}%, transparent ${volPct}%);`
        : `background: linear-gradient(to right, rgba(203, 213, 225, 0.10) ${volPct}%, transparent ${volPct}%);`;

      if (type === 'call') {
        return `
          <td class="val-oi" data-val="oi" style="${oiBarStyle}">${formatMoney(leg.oi, 1)}</td>
          <td class="val-vol" data-val="vol" style="${volBarStyle}">${formatMoney(leg.volume, 1)}</td>
          <td class="val-iv" data-val="iv">${formatPct(leg.iv)}</td>
          <td class="val-bid" data-val="bid">${formatMoney(leg.bid)}</td>
          <td class="val-ask" data-val="ask">${formatMoney(leg.ask)}</td>
          <td class="col-act">${actionCell}</td>
        `;
      } else {
        return `
          <td class="col-act">${actionCell}</td>
          <td class="val-bid" data-val="bid">${formatMoney(leg.bid)}</td>
          <td class="val-ask" data-val="ask">${formatMoney(leg.ask)}</td>
          <td class="val-iv" data-val="iv">${formatPct(leg.iv)}</td>
          <td class="val-vol" data-val="vol" style="${volBarStyle}">${formatMoney(leg.volume, 1)}</td>
          <td class="val-oi" data-val="oi" style="${oiBarStyle}">${formatMoney(leg.oi, 1)}</td>
        `;
      }
    }
  }

  function updateLegCells(tr, leg, type, strike) {
    if (!leg) return;

    const cacheKey = `${strike}_${type}`;
    const prev = state.previousPrices.get(cacheKey) || {};

    const selectorPrefix = type === 'call' ? 'td:nth-child(-n+6)' : 'td:nth-child(n+8)';
    const cells = tr.querySelectorAll(selectorPrefix);

    cells.forEach((td) => {
      const valType = td.dataset.val;
      if (!valType) {
        // May contain the last price span
        const lastSpan = td.querySelector('.val-last');
        if (lastSpan && leg.last != null) {
          const formatted = formatMoney(leg.last);
          if (lastSpan.textContent !== formatted) {
            triggerFlash(lastSpan.parentElement, leg.last > (prev.last || 0));
            lastSpan.textContent = formatted;
          }
        }
        const chgSpan = td.querySelector('.val-chg');
        if (chgSpan && leg.change_24h != null) {
          const chgVal = Number(leg.change_24h || 0);
          const chgSign = chgVal >= 0 ? '+' : '';
          chgSpan.textContent = `${chgSign}${chgVal.toFixed(1)}%`;
          chgSpan.className = `val-chg ${chgVal >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
        }
        return;
      }

      let newVal = '';
      if (valType === 'oi') {
        newVal = formatMoney(leg.oi, 1);
        const maxOi = type === 'call' ? state.maxDepths.maxCallOi : state.maxDepths.maxPutOi;
        const oiPct = maxOi > 0 ? Math.min(100, Math.round(((leg.oi || 0) / maxOi) * 100)) : 0;
        td.style.background = type === 'call'
          ? `linear-gradient(to left, rgba(56, 189, 248, 0.16) ${oiPct}%, transparent ${oiPct}%)`
          : `linear-gradient(to right, rgba(255, 82, 99, 0.16) ${oiPct}%, transparent ${oiPct}%)`;
      } else if (valType === 'vol') {
        newVal = formatMoney(leg.volume, 1);
        const maxVol = type === 'call' ? state.maxDepths.maxCallVol : state.maxDepths.maxPutVol;
        const volPct = maxVol > 0 ? Math.min(100, Math.round(((leg.volume || 0) / maxVol) * 100)) : 0;
        td.style.background = type === 'call'
          ? `linear-gradient(to left, rgba(203, 213, 225, 0.10) ${volPct}%, transparent ${volPct}%)`
          : `linear-gradient(to right, rgba(203, 213, 225, 0.10) ${volPct}%, transparent ${volPct}%)`;
      } else if (valType === 'delta') {
        newVal = leg.delta != null ? Number(leg.delta).toFixed(3) : '—';
      } else if (valType === 'theta') {
        newVal = leg.theta != null ? Number(leg.theta).toFixed(1) : '—';
      } else if (valType === 'iv') {
        newVal = formatPct(leg.iv);
      } else if (valType === 'bid') {
        newVal = formatMoney(leg.bid);
        if (prev.bid != null && leg.bid !== prev.bid) triggerFlash(td, leg.bid > prev.bid);
      } else if (valType === 'ask') {
        newVal = formatMoney(leg.ask);
        if (prev.ask != null && leg.ask !== prev.ask) triggerFlash(td, leg.ask > prev.ask);
      }

      if (td.textContent !== newVal) {
        td.textContent = newVal;
      }
    });

    state.previousPrices.set(cacheKey, {
      last: leg.last,
      bid: leg.bid,
      ask: leg.ask,
    });
  }

  function triggerFlash(element, isUp) {
    const flashClass = isUp ? 'flash-up' : 'flash-down';
    element.classList.remove('flash-up', 'flash-down');
    void element.offsetWidth; // force DOM reflow
    element.classList.add(flashClass);
    setTimeout(() => element.classList.remove(flashClass), 800);
  }

  function attachOrderActionListeners() {
    dom.chainBody.querySelectorAll('button[data-contract]').forEach((btn) => {
      btn.addEventListener('click', onSelectContractClick);
    });
  }

  // --- Order Ticket Logic ---
  function onSelectContractClick(e) {
    e.stopPropagation();
    initAudio();
    const btn = e.currentTarget;
    const contract = JSON.parse(btn.dataset.contract);
    const action = btn.dataset.action; // 'buy' or 'sell'

    state.selectedContract = contract;
    state.selectedSide = action;

    // Populate order ticket
    dom.ticketSymbol.textContent = contract.symbol;
    dom.ticketTag.textContent = `${contract.side.toUpperCase()} · Strike $${formatMoney(contract.strike, 0)}`;
    dom.ticketQuotes.textContent = `Product #${contract.product_id} · Bid: $${formatMoney(contract.bid)} · Ask: $${formatMoney(contract.ask)} · Last: $${formatMoney(contract.last)}`;

    dom.orderSide.value = action;

    // Default limit price based on side
    if (action === 'buy') {
      dom.orderPrice.value = contract.ask > 0 ? contract.ask : (contract.last || '');
    } else {
      dom.orderPrice.value = contract.bid > 0 ? contract.bid : (contract.last || '');
    }

    // Default bracket targets (optional helper suggestions)
    const basePrice = Number(dom.orderPrice.value);
    if (basePrice > 0) {
      if (action === 'buy') {
        dom.targetPrice.placeholder = `e.g. ${(basePrice * 1.25).toFixed(1)}`;
        dom.stopLossPrice.placeholder = `e.g. ${(basePrice * 0.85).toFixed(1)}`;
      } else {
        dom.targetPrice.placeholder = `e.g. ${(basePrice * 0.75).toFixed(1)}`;
        dom.stopLossPrice.placeholder = `e.g. ${(basePrice * 1.15).toFixed(1)}`;
      }
    }

    updateTicketState();

    // Scroll smoothly to order ticket
    document.querySelector('.order-ticket-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    showToast(`Loaded ${action.toUpperCase()} ${contract.symbol} into ticket`, 'info', 2000);
  }

  function updateTicketState() {
    const isConfirmed = dom.liveCheckbox.checked;

    if (!state.isConnected) {
      dom.ticketLockPill.textContent = 'LOCKED · ADD API KEY';
      dom.ticketLockPill.className = 'lock-pill';
      dom.submitOrderBtn.disabled = true;
      dom.exitAllBtn.disabled = true;
      return;
    }

    dom.exitAllBtn.disabled = false;

    if (!state.selectedContract) {
      dom.ticketLockPill.textContent = 'SELECT CONTRACT';
      dom.ticketLockPill.className = 'lock-pill';
      dom.submitOrderBtn.disabled = true;
      return;
    }

    if (!isConfirmed) {
      dom.ticketLockPill.textContent = 'CONFIRM RISK';
      dom.ticketLockPill.className = 'lock-pill confirm-risk';
      dom.submitOrderBtn.disabled = true;
      return;
    }

    dom.ticketLockPill.textContent = 'READY TO EXECUTE';
    dom.ticketLockPill.className = 'lock-pill ready';
    dom.submitOrderBtn.disabled = false;
  }

  function onOrderTypeChange() {
    const isMarket = dom.orderType.value === 'market_order';
    if (isMarket) {
      dom.orderPrice.disabled = true;
      dom.orderPrice.placeholder = 'Market Execution';
    } else {
      dom.orderPrice.disabled = false;
      dom.orderPrice.placeholder = 'Entry limit price';
      if (state.selectedContract && !dom.orderPrice.value) {
        dom.orderPrice.value = state.selectedContract.last || '';
      }
    }
  }

  // --- Live Order Confirmation Modal ---
  function openOrderConfirmationModal() {
    if (!state.selectedContract || !state.isConnected || !dom.liveCheckbox.checked) return;

    const side = dom.orderSide.value.toUpperCase();
    const qty = dom.orderQty.value;
    const type = dom.orderType.value === 'market_order' ? 'MARKET' : 'LIMIT';
    const price = type === 'MARKET' ? 'MARKET BEST' : `$${dom.orderPrice.value}`;

    dom.mSymbol.textContent = state.selectedContract.symbol;
    dom.mAction.textContent = side;
    dom.mAction.style.color = side === 'BUY' ? '#34d399' : '#f87171';
    dom.mQty.textContent = `${qty} Contract(s)`;
    dom.mPrice.textContent = `${type} @ ${price}`;

    const tp = dom.targetPrice.value;
    const sl = dom.stopLossPrice.value;
    if (tp || sl) {
      dom.mBracketRow.style.display = 'flex';
      dom.mBracket.textContent = `TP: ${tp ? '$' + tp : 'None'} | SL: ${sl ? '$' + sl : 'None'}`;
    } else {
      dom.mBracketRow.style.display = 'none';
    }

    dom.orderModal.classList.add('show');
  }

  function closeOrderConfirmationModal() {
    dom.orderModal.classList.remove('show');
  }

  async function executeOrderSubmission() {
    closeOrderConfirmationModal();

    const orderType = dom.orderType.value;
    const payload = {
      product_id: state.selectedContract.product_id,
      symbol: state.selectedContract.symbol,
      side: dom.orderSide.value,
      quantity: Number(dom.orderQty.value),
      order_type: orderType,
      price: orderType === 'limit_order' ? Number(dom.orderPrice.value) : null,
      target_price: dom.targetPrice.value ? Number(dom.targetPrice.value) : null,
      stop_loss_price: dom.stopLossPrice.value ? Number(dom.stopLossPrice.value) : null,
    };

    dom.submitOrderBtn.disabled = true;
    dom.submitOrderBtn.textContent = 'TRANSMITTING ORDER…';

    try {
      const res = await apiFetch('/api/orders', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      playSound('order');
      showToast(`Order Placed: ${payload.side.toUpperCase()} ${payload.quantity} ${payload.symbol}`, 'success');
      await refreshHistory();
      await refreshPositions();
    } catch (err) {
      playSound('error');
      showToast(`Order Submission Failed: ${err.message}`, 'error', 6000);
      await refreshHistory();
    } finally {
      dom.submitOrderBtn.textContent = 'SUBMIT LIVE ORDER';
      updateTicketState();
    }
  }

  // --- Emergency Exit All ---
  function openExitModal() {
    if (!state.isConnected) return;
    dom.exitConfirmationInput.value = '';
    dom.exitModalConfirmBtn.disabled = true;
    dom.exitModal.classList.add('show');
    dom.exitConfirmationInput.focus();
  }

  function closeExitModal() {
    dom.exitModal.classList.remove('show');
  }

  async function executeExitAll() {
    closeExitModal();
    dom.exitAllBtn.disabled = true;
    dom.exitAllBtn.textContent = 'EXITING…';

    try {
      const res = await apiFetch('/api/exit-all', {
        method: 'POST',
        body: JSON.stringify({ confirmation: 'EXIT ALL' }),
      });

      playSound('exit');
      const skipped = (res.summary && res.summary.skipped) || [];
      if (skipped.length) {
        showToast(`Exit All partially executed. Skipped products: ${skipped.join(', ')}`, 'error', 6000);
      } else {
        showToast('Exit All Executed: All open orders cancelled & positions closed!', 'success', 5000);
      }
      await refreshHistory();
      await refreshPositions();
    } catch (err) {
      playSound('error');
      showToast(`Exit All Failed: ${err.message}`, 'error', 6000);
      await refreshHistory();
    } finally {
      dom.exitAllBtn.textContent = '🚨 EXIT ALL';
      updateTicketState();
    }
  }

  // --- Positions & Strategy Baskets ---
  async function refreshPositions() {
    if (!state.isConnected) {
      dom.positionsBody.innerHTML = `
        <tr class="empty-mini-row">
          <td colspan="9">Connect credentials to view open account positions.</td>
        </tr>
      `;
      state.currentPositions = [];
      updateBatchActionBar();
      if (dom.portfolioUpnlBadge) dom.portfolioUpnlBadge.style.display = 'none';
      return;
    }

    if (state.isFetchingPositions) return;
    state.isFetchingPositions = true;

    try {
      const data = await apiFetch('/api/positions');
      const positions = (data.positions || []).filter((p) => Number(p.size) !== 0);
      state.currentPositions = positions;

      // Update Portfolio Total UPNL badge
      if (dom.portfolioUpnlBadge) {
        if (data.connected && positions.length > 0) {
          dom.portfolioUpnlBadge.style.display = 'inline-block';
          const tot = Number(data.total_upnl != null ? data.total_upnl : 0);
          const totPct = Number(data.total_pnl_pct != null ? data.total_pnl_pct : 0);
          const sign = tot >= 0 ? '+$' : '-$';
          const pctSign = totPct >= 0 ? '+' : '';
          dom.portfolioUpnlBadge.textContent = `TOTAL UPNL: ${sign}${Math.abs(tot).toFixed(2)} (${pctSign}${totPct.toFixed(2)}%)`;
          dom.portfolioUpnlBadge.style.backgroundColor = tot >= 0 ? 'rgba(34, 197, 94, 0.18)' : 'rgba(239, 68, 68, 0.18)';
          dom.portfolioUpnlBadge.style.color = tot >= 0 ? '#4ade80' : '#f87171';
          dom.portfolioUpnlBadge.style.border = `1px solid ${tot >= 0 ? 'rgba(74, 222, 128, 0.35)' : 'rgba(248, 113, 113, 0.35)'}`;
        } else {
          dom.portfolioUpnlBadge.style.display = 'none';
        }
      }

      if (!positions.length) {
        dom.positionsBody.innerHTML = `
          <tr class="empty-mini-row">
            <td colspan="9">No open margined positions found.</td>
          </tr>
        `;
        state.selectedPositionKeys.clear();
        updateBatchActionBar();
        monitorStrategyGroups();
        return;
      }

      // Render positions with checkboxes, Mark, Value, and single close buttons
      dom.positionsBody.innerHTML = positions
        .map((p) => {
          const key = `${p.product_id}_${p.symbol}`;
          const isChecked = state.selectedPositionKeys.has(key);
          const sym = p.symbol || p.product_symbol || '—';
          const size = Number(p.size);
          const side = size >= 0 ? 'BUY' : 'SELL';
          const entry = formatMoney(p.entry_price);
          const markPrice = formatMoney(p.mark_price || p.exit_price);
          const entryVal = p.entry_value != null ? `$${Number(p.entry_value).toFixed(2)}` : '—';
          const pnlVal = Number(p.unrealized_pnl != null ? p.unrealized_pnl : p.realized_pnl || 0);
          const pnlClass = pnlVal >= 0 ? 'pnl-positive' : 'pnl-negative';
          const pnlStr = (pnlVal >= 0 ? '+$' : '-$') + Math.abs(pnlVal).toFixed(2);
          const posJson = escapeHtml(JSON.stringify(p));

          // Mark change display: points and % change from entry
          const chgPts = p.pts_change != null ? Number(p.pts_change) : null;
          const chgPct = p.pts_change_pct != null ? Number(p.pts_change_pct) : null;
          const markChangeSub = (chgPts != null && chgPct != null)
            ? `<div class="${chgPts >= 0 ? 'pnl-positive' : 'pnl-negative'}" style="font-size: 11px; margin-top: 1px;">${chgPts >= 0 ? '+' : ''}${chgPts.toFixed(1)} (${chgPct >= 0 ? '+' : ''}${chgPct.toFixed(1)}%)</div>`
            : '';

          // PnL percentage on margin
          const pnlPctVal = p.pnl_pct != null ? Number(p.pnl_pct) : null;
          const pnlPctStr = pnlPctVal != null
            ? `<div style="font-size: 11px; opacity: 0.9; margin-top: 1px;">(${pnlPctVal >= 0 ? '+' : ''}${pnlPctVal.toFixed(2)}%)</div>`
            : '';

          // Asset quantity display: e.g. -0.05 BTC (-50 ctrs)
          const assetSign = size >= 0 ? '+' : '-';
          const assetQtyText = p.asset_qty != null
            ? `${assetSign}${Math.abs(p.asset_qty)} ${p.asset_unit || ''}`
            : `${formatInt(size)}`;
          const contractsSub = p.contracts != null
            ? `<div style="font-size: 11px; opacity: 0.65; margin-top: 1px;">(${formatInt(size)} ctrs)</div>`
            : '';

          return `
            <tr data-pos-key="${escapeHtml(key)}">
              <td><input type="checkbox" class="pos-checkbox" data-pos='${posJson}' ${isChecked ? 'checked' : ''}></td>
              <td><strong>${escapeHtml(sym)}</strong></td>
              <td><span class="${side === 'BUY' ? 'pnl-positive' : 'pnl-negative'} font-semibold">${side}</span></td>
              <td>
                <span class="font-mono">${assetQtyText}</span>
                ${contractsSub}
              </td>
              <td class="font-mono">${entry}</td>
              <td class="font-mono">
                ${markPrice}
                ${markChangeSub}
              </td>
              <td class="font-mono">${entryVal}</td>
              <td class="${pnlClass} font-mono font-semibold">
                ${pnlStr}
                ${pnlPctStr}
              </td>
              <td style="text-align: right;">
                <button class="btn-close-single" data-close-pos='${posJson}'>CLOSE</button>
              </td>
            </tr>
          `;
        })
        .join('');

      // Attach checkbox listeners
      dom.positionsBody.querySelectorAll('.pos-checkbox').forEach((cb) => {
        cb.addEventListener('change', onPositionCheckboxChange);
      });

      // Attach single close buttons
      dom.positionsBody.querySelectorAll('.btn-close-single').forEach((btn) => {
        btn.addEventListener('click', onSingleCloseClick);
      });

      updateBatchActionBar();
      monitorStrategyGroups();
      fetchWalletBalance();
    } catch (err) {
      console.warn('Position refresh error:', err);
      dom.positionsBody.innerHTML = `
        <tr class="empty-mini-row">
          <td colspan="9">Position error: ${escapeHtml(err.message)}</td>
        </tr>
      `;
    } finally {
      state.isFetchingPositions = false;
    }
  }

  function onPositionCheckboxChange(e) {
    const cb = e.currentTarget;
    const pos = JSON.parse(cb.dataset.pos);
    const key = `${pos.product_id}_${pos.symbol}`;

    if (cb.checked) {
      state.selectedPositionKeys.add(key);
    } else {
      state.selectedPositionKeys.delete(key);
    }

    updateBatchActionBar();
  }

  function updateBatchActionBar() {
    const selectedPositions = state.currentPositions.filter((p) =>
      state.selectedPositionKeys.has(`${p.product_id}_${p.symbol}`)
    );

    const count = selectedPositions.length;
    if (count > 0) {
      dom.batchActionBar.style.display = 'flex';
      dom.batchCount.textContent = `${count} SELECTED`;

      const netPnl = selectedPositions.reduce((acc, p) => acc + Number(p.unrealized_pnl || 0), 0);
      const sign = netPnl >= 0 ? '+$' : '-$';
      dom.batchPnl.textContent = `MTM: ${sign}${Math.abs(netPnl).toFixed(2)}`;
      dom.batchPnl.className = `batch-pnl font-mono ${netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
    } else {
      dom.batchActionBar.style.display = 'none';
    }

    if (dom.selectAllPositions) {
      dom.selectAllPositions.checked = count > 0 && count === state.currentPositions.length;
    }
  }

  function onSelectAllPositionsChange(e) {
    const checked = e.currentTarget.checked;
    state.selectedPositionKeys.clear();
    if (checked) {
      state.currentPositions.forEach((p) => {
        state.selectedPositionKeys.add(`${p.product_id}_${p.symbol}`);
      });
    }
    dom.positionsBody.querySelectorAll('.pos-checkbox').forEach((cb) => {
      cb.checked = checked;
    });
    updateBatchActionBar();
  }

  async function onSingleCloseClick(e) {
    e.stopPropagation();
    initAudio();
    const pos = JSON.parse(e.currentTarget.dataset.closePos);
    const sym = pos.symbol || pos.product_symbol;

    if (!window.confirm(`CLOSE POSITION AT MARKET\n\nClose ${pos.size} of ${sym} immediately at market price?`)) {
      return;
    }

    try {
      const res = await apiFetch('/api/positions/close', {
        method: 'POST',
        body: JSON.stringify({
          positions: [pos],
          group_name: sym,
          reason: 'Manual Single Close',
        }),
      });

      playSound('order');
      showToast(`Position Closed: ${sym}`, 'success');
      await refreshPositions();
      await refreshHistory();
    } catch (err) {
      playSound('error');
      showToast(`Close failed: ${err.message}`, 'error', 5000);
    }
  }

  // --- Combined Strategy Group Modal & Management ---
  function openGroupModal() {
    const selectedPositions = state.currentPositions.filter((p) =>
      state.selectedPositionKeys.has(`${p.product_id}_${p.symbol}`)
    );

    if (selectedPositions.length < 2) {
      showToast('Please select at least 2 positions to combine into a group.', 'info');
      return;
    }

    dom.groupNameInput.value = `Strategy Pair #${state.strategyGroups.length + 1}`;
    dom.groupTargetInput.value = '';
    dom.groupStopLossInput.value = '';

    const netPnl = selectedPositions.reduce((acc, p) => acc + Number(p.unrealized_pnl || 0), 0);
    const sign = netPnl >= 0 ? '+$' : '-$';
    dom.groupCurrentMtm.textContent = `${sign}${Math.abs(netPnl).toFixed(2)}`;
    dom.groupCurrentMtm.className = `m-val font-mono ${netPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`;

    dom.selectedLegsList.innerHTML = selectedPositions
      .map((p) => {
        const side = Number(p.size) >= 0 ? 'BUY' : 'SELL';
        const pnl = Number(p.unrealized_pnl || 0);
          const sizeStr = p.asset_qty != null
            ? `${side === 'BUY' ? '+' : '-'}${p.asset_qty} ${p.asset_unit || ''}`
            : `Size: ${formatInt(p.size)}`;
          return `
            <div class="group-leg-pill">
              <span class="${side === 'BUY' ? 'group-leg-side-buy' : 'group-leg-side-sell'}">${side}</span>
              <strong>${escapeHtml(p.symbol)}</strong>
              <span>${sizeStr}</span>
              <span class="${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${pnl >= 0 ? '+$' : '-$'}${Math.abs(pnl).toFixed(2)}</span>
            </div>
          `;
      })
      .join('');

    dom.groupModal.classList.add('show');
  }

  function closeGroupModal() {
    dom.groupModal.classList.remove('show');
  }

  async function saveStrategyGroup() {
    const name = dom.groupNameInput.value.trim() || `Strategy Pair #${state.strategyGroups.length + 1}`;
    const target = Number(dom.groupTargetInput.value);
    const stopLoss = Number(dom.groupStopLossInput.value);

    if (!Number.isFinite(target) || target <= 0) {
      showToast('Please enter a positive Combined Target Profit ($).', 'error');
      return;
    }

    if (!Number.isFinite(stopLoss) || stopLoss <= 0) {
      showToast('Please enter a positive Combined Stop-Loss ($).', 'error');
      return;
    }

    const selectedPositions = state.currentPositions.filter((p) =>
      state.selectedPositionKeys.has(`${p.product_id}_${p.symbol}`)
    );

    if (selectedPositions.length < 2) {
      showToast('At least 2 positions are required.', 'error');
      return;
    }

    const newGroup = {
      id: 'grp_' + Date.now(),
      name: name,
      positions: selectedPositions.map((p) => ({
        product_id: p.product_id,
        symbol: p.symbol,
        size: p.size,
        side: Number(p.size) >= 0 ? 'buy' : 'sell',
        entry_price: p.entry_price,
      })),
      target: target,
      stopLoss: stopLoss,
      status: 'MONITORING', // 'MONITORING', 'TARGET_HIT', 'SL_HIT'
      created_at: new Date().toISOString(),
    };

    // Save to server so 24/7 background daemon monitors even if PC is off
    try {
      await apiFetch('/api/strategy-groups', {
        method: 'POST',
        body: JSON.stringify({ group: newGroup }),
      });
    } catch (e) {
      console.warn('Server group sync:', e);
    }

    state.strategyGroups = [newGroup, ...state.strategyGroups.filter((g) => g.id !== newGroup.id)];
    saveGroupsToStorage();

    // Clear selection
    state.selectedPositionKeys.clear();
    dom.positionsBody.querySelectorAll('.pos-checkbox').forEach((cb) => (cb.checked = false));
    updateBatchActionBar();

    closeGroupModal();
    renderStrategyGroups();
    playSound('order');
    showToast(`Activated ${name}: Combined TP: +$${target} | Combined SL: -$${stopLoss} (24/7 Cloud Monitored)`, 'success', 4000);
  }

  function saveGroupsToStorage(syncServerGroup = null) {
    try {
      localStorage.setItem('delta_strategy_groups', JSON.stringify(state.strategyGroups));
    } catch (_) {}
    if (syncServerGroup) {
      try {
        apiFetch('/api/strategy-groups', {
          method: 'POST',
          body: JSON.stringify({ group: syncServerGroup }),
        }).catch(() => {});
      } catch (_) {}
    }
  }

  function computeGroupMtm(group) {
    if (state.currentPositions && state.currentPositions.length) {
      let mtm = 0;
      let matched = false;
      for (const leg of (group.positions || [])) {
        const found = state.currentPositions.find(
          (p) => Number(p.product_id) === Number(leg.product_id) || String(p.symbol) === String(leg.symbol)
        );
        if (found) {
          mtm += Number(found.unrealized_pnl != null ? found.unrealized_pnl : (found.realized_pnl || 0));
          matched = true;
        }
      }
      if (matched) return mtm;
    }
    return Number(group.current_mtm != null ? group.current_mtm : 0);
  }

  function groupsChanged(current, incoming) {
    if (!Array.isArray(current) || !Array.isArray(incoming)) return true;
    if (current.length !== incoming.length) return true;
    for (let i = 0; i < current.length; i++) {
      if (current[i].id !== incoming[i].id) return true;
      if (current[i].status !== incoming[i].status) return true;
      if (current[i].target !== incoming[i].target) return true;
      if (current[i].stopLoss !== incoming[i].stopLoss) return true;
    }
    return false;
  }

  let isSyncingGroups = false;
  async function syncStrategyGroups() {
    if (isSyncingGroups) return;
    isSyncingGroups = true;
    try {
      const serverRes = await apiFetch('/api/strategy-groups');
      const serverGroups = (serverRes && Array.isArray(serverRes.groups)) ? serverRes.groups : [];

      // 1. If server has groups, server is our master source of truth across all devices (PC & Mobile)
      if (serverGroups.length > 0) {
        const hasStructuralChange = groupsChanged(state.strategyGroups, serverGroups);
        state.strategyGroups = serverGroups;
        try {
          localStorage.setItem('delta_strategy_groups', JSON.stringify(serverGroups));
        } catch (_) {}
        // Only rebuild DOM if groups structure/status changed or container is empty
        if (hasStructuralChange || !dom.strategyGroupsContainer.querySelector('.strategy-group-card')) {
          renderStrategyGroups();
        } else {
          monitorStrategyGroups();
        }
        return;
      }

      // 2. If server has NO groups, check if this browser device has groups in localStorage (e.g. PC before server setup)
      let localGroups = [];
      try {
        const localRaw = localStorage.getItem('delta_strategy_groups');
        if (localRaw) localGroups = JSON.parse(localRaw);
      } catch (_) {}

      if ((!localGroups || !localGroups.length) && state.strategyGroups && state.strategyGroups.length) {
        localGroups = state.strategyGroups;
      }

      if (Array.isArray(localGroups) && localGroups.length > 0) {
        const hasStructuralChange = groupsChanged(state.strategyGroups, localGroups);
        state.strategyGroups = localGroups;
        if (hasStructuralChange || !dom.strategyGroupsContainer.querySelector('.strategy-group-card')) {
          renderStrategyGroups();
        } else {
          monitorStrategyGroups();
        }
        // Immediately sync-upload each local group to server so Mobile & daemon get it!
        for (const grp of localGroups) {
          try {
            await apiFetch('/api/strategy-groups', {
              method: 'POST',
              body: JSON.stringify({ group: grp }),
            });
          } catch (e) {
            console.warn('Failed to push group to server:', e);
          }
        }
      } else {
        if (state.strategyGroups.length > 0) {
          state.strategyGroups = [];
          renderStrategyGroups();
        }
      }
    } catch (err) {
      console.warn('Strategy sync error:', err);
    } finally {
      isSyncingGroups = false;
    }
  }

  function renderStrategyGroups() {
    const groups = state.strategyGroups || [];
    dom.activeGroupsCount.textContent = `${groups.filter((g) => g.status === 'MONITORING').length} ACTIVE`;

    if (!groups.length) {
      dom.strategyGroupsContainer.innerHTML = `
        <div class="empty-groups-state" id="emptyGroupsState">
          <div class="empty-icon-sm">🎯</div>
          <div class="empty-groups-title">No Strategy Groups Created</div>
          <div class="empty-groups-sub">Select 2 or more positions in the table above and click <strong>"Combine into Group"</strong> to set a combined Target &amp; Stop-Loss!</div>
        </div>
      `;
      return;
    }

    dom.strategyGroupsContainer.innerHTML = groups
      .map((g) => {
        let badgeHtml = '<span class="group-card-badge badge-monitoring">🟢 LIVE MONITORING</span>';
        let cardClass = 'monitoring';

        if (g.status === 'TARGET_HIT') {
          badgeHtml = '<span class="group-card-badge badge-target">🎯 TARGET HIT (CLOSED)</span>';
          cardClass = 'target-hit';
        } else if (g.status === 'SL_HIT') {
          badgeHtml = '<span class="group-card-badge badge-sl">🛑 STOP-LOSS HIT (CLOSED)</span>';
          cardClass = 'sl-hit';
        }

        const legsHtml = g.positions
          .map((p) => {
            const side = String(p.side).toUpperCase();
            return `
              <div class="group-leg-pill">
                <span class="${side === 'BUY' ? 'group-leg-side-buy' : 'group-leg-side-sell'}">${side}</span>
                <strong>${escapeHtml(p.symbol)}</strong>
                <span>(${formatInt(p.size)})</span>
              </div>
            `;
          })
          .join('');

        const liveMtm = computeGroupMtm(g);
        const sign = liveMtm >= 0 ? '+$' : '-$';
        const colorClass = liveMtm >= 0 ? 'pnl-positive' : 'pnl-negative';

        return `
          <div class="strategy-group-card ${cardClass}" id="card_${g.id}">
            <div class="group-card-top">
              <span class="group-card-title">🧩 ${escapeHtml(g.name)}</span>
              ${badgeHtml}
            </div>

            <div class="group-legs-container">
              ${legsHtml}
            </div>

            <div class="group-metrics-row">
              <div class="group-mtm-box">
                <span class="group-mtm-label">COMBINED LIVE MTM:</span>
                <span class="group-mtm-val font-mono ${colorClass}" id="mtm_${g.id}">${sign}${Math.abs(liveMtm).toFixed(2)}</span>
              </div>
              <div class="group-thresholds-box">
                <span class="threshold-target">TARGET: +$${g.target.toFixed(2)}</span>
                <span class="threshold-sl">SL: -$${g.stopLoss.toFixed(2)}</span>
              </div>
            </div>

            <div class="group-card-actions">
              ${
                g.status === 'MONITORING'
                  ? `<button class="btn-group-close" data-close-grp="${g.id}">🚨 CLOSE GROUP NOW</button>`
                  : ''
              }
              <button class="btn-group-unlink" data-unlink-grp="${g.id}">✕ REMOVE</button>
            </div>
          </div>
        `;
      })
      .join('');

    // Attach group actions
    dom.strategyGroupsContainer.querySelectorAll('[data-close-grp]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const gid = e.currentTarget.dataset.closeGrp;
        const grp = state.strategyGroups.find((g) => g.id === gid);
        if (grp) closeGroupManual(grp);
      });
    });

    dom.strategyGroupsContainer.querySelectorAll('[data-unlink-grp]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const gid = e.currentTarget.dataset.unlinkGrp;
        unlinkGroup(gid);
      });
    });

    // Run in-place monitor immediately after render
    monitorStrategyGroups();
  }

  // Real-time 1-second Combined Strategy Monitor
  async function monitorStrategyGroups() {
    if (!state.strategyGroups || !state.strategyGroups.length) return;

    for (const group of state.strategyGroups) {
      // Find matching live positions for each leg in this group
      let combinedMtm = 0;
      let matchedCount = 0;

      for (const leg of group.positions) {
        const found = state.currentPositions.find(
          (p) => Number(p.product_id) === Number(leg.product_id) || String(p.symbol) === String(leg.symbol)
        );
        if (found) {
          combinedMtm += Number(found.unrealized_pnl != null ? found.unrealized_pnl : (found.realized_pnl || 0));
          matchedCount++;
        }
      }

      // If positions are loaded and we matched them, update the live element in-place smoothly
      if (state.currentPositions && state.currentPositions.length > 0) {
        group.current_mtm = combinedMtm;
        const mtmElem = el(`mtm_${group.id}`);
        if (mtmElem) {
          const sign = combinedMtm >= 0 ? '+$' : '-$';
          mtmElem.textContent = `${sign}${Math.abs(combinedMtm).toFixed(2)}`;
          mtmElem.className = `group-mtm-val font-mono ${combinedMtm >= 0 ? 'pnl-positive' : 'pnl-negative'}`;
        }
      }

      // Skip triggering if not in active MONITORING state
      if (group.status !== 'MONITORING') continue;

      // If positions are no longer in open positions (e.g. closed manually elsewhere)
      if (matchedCount === 0 && state.currentPositions.length > 0) {
        group.status = 'CLOSED_EXTERNALLY';
        saveGroupsToStorage(group);
        renderStrategyGroups();
        continue;
      }

      // 1. Check Combined Target Profit Trigger
      if (combinedMtm >= group.target) {
        group.status = 'TARGET_HIT';
        saveGroupsToStorage(group);
        renderStrategyGroups();
        playSound('order');
        showToast(
          `🎯 TARGET REACHED! ${group.name} hit combined target of +$${group.target.toFixed(2)} (Actual: +$${combinedMtm.toFixed(2)}). Auto-closing all legs!`,
          'success',
          7000
        );

        try {
          await apiFetch('/api/positions/close', {
            method: 'POST',
            body: JSON.stringify({
              positions: group.positions,
              group_name: group.name,
              reason: `Combined Target Hit (+$${combinedMtm.toFixed(2)})`,
            }),
          });
          await refreshPositions();
          await refreshHistory();
        } catch (err) {
          showToast(`Target auto-close failed: ${err.message}`, 'error', 6000);
        }
      }
      // 2. Check Combined Stop-Loss Trigger
      else if (combinedMtm <= -Math.abs(group.stopLoss)) {
        group.status = 'SL_HIT';
        saveGroupsToStorage(group);
        renderStrategyGroups();
        playSound('exit');
        showToast(
          `🛑 STOP-LOSS HIT! ${group.name} hit combined stop-loss of -$${group.stopLoss.toFixed(2)} (Actual: -$${Math.abs(combinedMtm).toFixed(2)}). Auto-closing all legs!`,
          'error',
          7000
        );

        try {
          await apiFetch('/api/positions/close', {
            method: 'POST',
            body: JSON.stringify({
              positions: group.positions,
              group_name: group.name,
              reason: `Combined Stop-Loss Hit (-$${Math.abs(combinedMtm).toFixed(2)})`,
            }),
          });
          await refreshPositions();
          await refreshHistory();
        } catch (err) {
          showToast(`Stop-loss auto-close failed: ${err.message}`, 'error', 6000);
        }
      }
    }
  }

  async function closeGroupManual(group) {
    if (!window.confirm(`CLOSE STRATEGY GROUP\n\nClose all legs of "${group.name}" immediately at market price?`)) {
      return;
    }

    try {
      await apiFetch('/api/positions/close', {
        method: 'POST',
        body: JSON.stringify({
          positions: group.positions,
          group_name: group.name,
          reason: 'Manual Group Close',
        }),
      });

      group.status = 'CLOSED_MANUALLY';
      saveGroupsToStorage(group);
      renderStrategyGroups();
      playSound('order');
      showToast(`Strategy Group closed: ${group.name}`, 'success');
      await refreshPositions();
      await refreshHistory();
    } catch (err) {
      playSound('error');
      showToast(`Group close failed: ${err.message}`, 'error', 5000);
    }
  }

  async function unlinkGroup(groupId) {
    state.strategyGroups = state.strategyGroups.filter((g) => g.id !== groupId);
    saveGroupsToStorage();
    renderStrategyGroups();
    try {
      await apiFetch(`/api/strategy-groups/${groupId}`, { method: 'DELETE' });
    } catch (_) {}
    showToast('Strategy Group removed.', 'info', 2000);
  }

  async function refreshHistory() {
    try {
      const data = await apiFetch('/api/history');
      const history = data.history || [];

      if (!history.length) {
        dom.historyBody.innerHTML = `
          <tr class="empty-mini-row">
            <td colspan="5">No order submissions logged in this session.</td>
          </tr>
        `;
        return;
      }

      dom.historyBody.innerHTML = history
        .map((item) => {
          let statusTag = 'status-submitted';
          if (String(item.status).toLowerCase().includes('reject') || String(item.status).toLowerCase().includes('fail')) {
            statusTag = 'status-rejected';
          } else if (String(item.status).toLowerCase().includes('exit')) {
            statusTag = 'status-exit';
          }

          const side = String(item.side || '—').toUpperCase();
          const sideColor = side === 'BUY' ? 'color:#34d399' : side === 'SELL' ? 'color:#f87171' : '';

          return `
            <tr>
              <td>${escapeHtml(item.time)}</td>
              <td><strong>${escapeHtml(item.symbol)}</strong></td>
              <td style="${sideColor}">${escapeHtml(side)}</td>
              <td>${escapeHtml(item.quantity)}</td>
              <td><span class="status-tag ${statusTag}">${escapeHtml(item.status)}</span></td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      console.warn('History refresh error:', err);
    }
  }

  // --- Credentials & Connection ---
  async function saveCredentials() {
    initAudio();
    const env = dom.envSelect.value;
    const key = dom.apiKey.value.trim();
    const secret = dom.apiSecret.value.trim();

    if (!key || !secret) {
      showToast('Please enter both API Key and API Secret.', 'error');
      return;
    }

    try {
      const data = await apiFetch('/api/connection', {
        method: 'POST',
        body: JSON.stringify({
          environment: env,
          api_key: key,
          api_secret: secret,
        }),
      });

      state.isConnected = data.connected;
      state.environment = data.environment;

      if (data.connected) {
        // Persist in localStorage so browser refresh (F5) keeps connection active
        try {
          localStorage.setItem('delta_api_credentials', JSON.stringify({
            environment: env,
            api_key: key,
            api_secret: secret,
          }));
        } catch (_) {}

        dom.connBeacon.className = 'status-beacon cyan';
        dom.connLabel.textContent = 'API CONNECTED · RAM SECURED';
        // Clear plain text values from DOM inputs for security
        dom.apiKey.value = '';
        dom.apiSecret.value = '';
        dom.apiKey.placeholder = '•••• Credentials active ••••';
        dom.apiSecret.placeholder = '•••• Credentials active ••••';
        showToast('API credentials connected & persistent across refresh.', 'success');
        await refreshPositions();
        await fetchWalletBalance();
      } else {
        try {
          localStorage.removeItem('delta_api_credentials');
        } catch (_) {}
        dom.connBeacon.className = 'status-beacon amber';
        dom.connLabel.textContent = 'PUBLIC DATA MODE';
        showToast('Public data mode active (no credentials stored).', 'info');
      }

      updateTicketState();
      if (state.isScanning) {
        fetchChainSnapshot();
      }
    } catch (err) {
      showToast(`Connection failed: ${err.message}`, 'error');
    }
  }

  async function clearCredentials() {
    dom.apiKey.value = '';
    dom.apiSecret.value = '';
    dom.apiKey.placeholder = 'Optional for public scan';
    dom.apiSecret.placeholder = 'Held in RAM only';

    try {
      localStorage.removeItem('delta_api_credentials');
    } catch (_) {}

    await apiFetch('/api/connection', {
      method: 'POST',
      body: JSON.stringify({
        environment: dom.envSelect.value,
        api_key: '',
        api_secret: '',
      }),
    });

    state.isConnected = false;
    dom.connBeacon.className = 'status-beacon amber';
    dom.connLabel.textContent = 'PUBLIC DATA MODE';
    updateTicketState();
    refreshPositions();
    showToast('Credentials cleared and removed.', 'info');
  }

  async function restoreSavedConnection() {
    try {
      let savedCreds = null;
      try {
        const raw = localStorage.getItem('delta_api_credentials');
        if (raw) savedCreds = JSON.parse(raw);
      } catch (_) {}

      if (savedCreds && savedCreds.api_key && savedCreds.api_secret) {
        dom.envSelect.value = savedCreds.environment || 'india';
        const data = await apiFetch('/api/connection', {
          method: 'POST',
          body: JSON.stringify({
            environment: savedCreds.environment || 'india',
            api_key: savedCreds.api_key,
            api_secret: savedCreds.api_secret,
          }),
        });

        state.isConnected = data.connected;
        state.environment = data.environment;
        if (data.connected) {
          dom.connBeacon.className = 'status-beacon cyan';
          dom.connLabel.textContent = 'API CONNECTED · RAM SECURED';
          dom.apiKey.placeholder = '•••• Credentials active ••••';
          dom.apiSecret.placeholder = '•••• Credentials active ••••';
          updateTicketState();
          await refreshPositions();
          await fetchWalletBalance();
          return;
        }
      }

      // Check server session if no localStorage
      const serverStatus = await apiFetch('/api/connection');
      if (serverStatus.connected) {
        state.isConnected = true;
        state.environment = serverStatus.environment;
        dom.envSelect.value = serverStatus.environment || 'india';
        dom.connBeacon.className = 'status-beacon cyan';
        dom.connLabel.textContent = 'API CONNECTED · RAM SECURED';
        dom.apiKey.placeholder = serverStatus.api_key_masked || '•••• Active in memory ••••';
        dom.apiSecret.placeholder = '•••• Active in memory ••••';
        updateTicketState();
        await refreshPositions();
        await fetchWalletBalance();
      }
    } catch (err) {
      console.warn('Error restoring connection on load:', err);
    }
  }

  // --- Sound Toggle ---
  function toggleAudio() {
    initAudio();
    state.soundEnabled = !state.soundEnabled;
    try {
      localStorage.setItem('delta_scanner_sound', state.soundEnabled ? '1' : '0');
    } catch (_) {}

    if (state.soundEnabled) {
      dom.audioToggle.classList.remove('muted');
      dom.audioIcon.textContent = '🔊';
      dom.audioText.textContent = 'SOUND ON';
      playSound('order');
    } else {
      dom.audioToggle.classList.add('muted');
      dom.audioIcon.textContent = '🔇';
      dom.audioText.textContent = 'SOUND MUTED';
    }
  }

  // --- Live Wallet Margin Fetcher ---
  async function fetchWalletBalance() {
    if (!state.isConnected) {
      dom.walletPill.style.display = 'none';
      return;
    }
    try {
      const data = await apiFetch('/api/wallet');
      if (data.connected) {
        dom.walletPill.style.display = 'flex';
        dom.walletLabel.textContent = `EQUITY: $${formatMoney(data.equity)} | AVAIL: $${formatMoney(data.available)}`;
      }
    } catch (_) {
      // Quietly continue if wallet balance call encounters API delay
    }
  }

  // --- CSV / Excel Exporter ---
  function exportCsv() {
    if (!state.rawChainData || !state.rawChainData.rows || !state.rawChainData.rows.length) {
      showToast('No option chain data available to export.', 'error');
      return;
    }

    const rows = state.rawChainData.rows;
    const asset = state.rawChainData.underlying || state.asset;
    const expiry = state.rawChainData.expiry || 'Expiry';

    const csvLines = [
      `DELTA OPTION CHAIN EXPORT - ${asset} (${expiry}) - Generated ${new Date().toISOString()}`,
      `Underlying Spot: $${state.rawChainData.spot} | ATM Strike: $${state.rawChainData.atm} | Max Pain Strike: $${state.rawChainData.max_pain} | PCR: ${state.rawChainData.pcr ? Number(state.rawChainData.pcr).toFixed(2) : 'N/A'}`,
      '',
      'CALL_SYMBOL,CALL_OI,CALL_VOL,CALL_IV_PCT,CALL_BID,CALL_ASK,CALL_LAST,CALL_DELTA,CALL_THETA,STRIKE,PUT_LAST,PUT_BID,PUT_ASK,PUT_IV_PCT,PUT_VOL,PUT_OI,PUT_DELTA,PUT_THETA,PUT_SYMBOL',
    ];

    rows.forEach((r) => {
      const c = r.call || {};
      const p = r.put || {};
      const line = [
        c.symbol || '',
        c.oi != null ? c.oi : '',
        c.volume != null ? c.volume : '',
        c.iv != null ? c.iv.toFixed(1) : '',
        c.bid != null ? c.bid : '',
        c.ask != null ? c.ask : '',
        c.last != null ? c.last : '',
        c.delta != null ? c.delta : '',
        c.theta != null ? c.theta : '',
        r.strike,
        p.last != null ? p.last : '',
        p.bid != null ? p.bid : '',
        p.ask != null ? p.ask : '',
        p.iv != null ? p.iv.toFixed(1) : '',
        p.volume != null ? p.volume : '',
        p.oi != null ? p.oi : '',
        p.delta != null ? p.delta : '',
        p.theta != null ? p.theta : '',
        p.symbol || '',
      ].join(',');
      csvLines.push(line);
    });

    const csvBlob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const blobUrl = URL.createObjectURL(csvBlob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = `Delta_${asset}_Options_${expiry}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);

    showToast(`Exported ${asset} Option Chain to CSV (${expiry})`, 'success');
  }

  // --- Event Bindings ---
  function initEvents() {
    // Underlying tabs
    dom.assetTabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        dom.assetTabs.forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        state.asset = tab.dataset.asset;
        state.expiry = null;
        state.previousPrices.clear();
        dom.expirySelect.innerHTML = '<option value="">Loading expiries…</option>';
        fetchChainSnapshot();
      });
    });

    // Expiry change
    dom.expirySelect.addEventListener('change', () => {
      state.expiry = dom.expirySelect.value;
      state.previousPrices.clear();
      fetchChainSnapshot();
    });

    // Scanner start / stop
    dom.startScanBtn.addEventListener('click', startScanner);
    dom.stopScanBtn.addEventListener('click', stopScanner);

    // Connection buttons
    dom.saveConnBtn.addEventListener('click', saveCredentials);
    dom.clearConnBtn.addEventListener('click', clearCredentials);

    // Audio toggle
    dom.audioToggle.addEventListener('click', toggleAudio);

    // Order ticket events
    dom.orderType.addEventListener('change', onOrderTypeChange);
    dom.liveCheckbox.addEventListener('change', updateTicketState);
    dom.submitOrderBtn.addEventListener('click', openOrderConfirmationModal);

    // Order modal events
    dom.modalCloseBtn.addEventListener('click', closeOrderConfirmationModal);
    dom.modalCancelBtn.addEventListener('click', closeOrderConfirmationModal);
    dom.modalConfirmBtn.addEventListener('click', executeOrderSubmission);

    // Emergency Exit All events
    dom.exitAllBtn.addEventListener('click', openExitModal);
    dom.exitModalCloseBtn.addEventListener('click', closeExitModal);
    dom.exitModalCancelBtn.addEventListener('click', closeExitModal);
    dom.exitConfirmationInput.addEventListener('input', () => {
      dom.exitModalConfirmBtn.disabled = dom.exitConfirmationInput.value.trim() !== 'EXIT ALL';
    });
    dom.exitModalConfirmBtn.addEventListener('click', executeExitAll);

    // Refresh buttons
    dom.refreshPositionsBtn.addEventListener('click', () => {
      refreshPositions();
      showToast('Positions refreshed.', 'info', 1500);
    });

    // Select All positions checkbox
    if (dom.selectAllPositions) {
      dom.selectAllPositions.addEventListener('change', onSelectAllPositionsChange);
    }

    // Strike range filter listener
    if (dom.strikeFilter) {
      dom.strikeFilter.addEventListener('change', () => {
        state.strikeFilterRange = dom.strikeFilter.value;
        if (state.rawChainData) {
          renderChainData(state.rawChainData);
        }
      });
    }

    // Greeks view mode toggle listener
    if (dom.greeksToggleBtn) {
      dom.greeksToggleBtn.addEventListener('click', () => {
        initAudio();
        state.showGreeks = !state.showGreeks;
        dom.greeksToggleBtn.classList.toggle('active', state.showGreeks);
        dom.greeksBtnText.textContent = state.showGreeks ? 'STANDARD VIEW' : 'SHOW GREEKS';
        updateTableHeaderForViewMode();
        if (state.rawChainData) {
          renderChainData(state.rawChainData);
        }
      });
    }

    // Export CSV listener
    if (dom.exportCsvBtn) {
      dom.exportCsvBtn.addEventListener('click', exportCsv);
    }

    // Multi-leg Strategy Group modal triggers
    if (dom.openGroupModalBtn) {
      dom.openGroupModalBtn.addEventListener('click', openGroupModal);
    }
    if (dom.groupModalCloseBtn) {
      dom.groupModalCloseBtn.addEventListener('click', closeGroupModal);
    }
    if (dom.groupModalCancelBtn) {
      dom.groupModalCancelBtn.addEventListener('click', closeGroupModal);
    }
    if (dom.groupModalSaveBtn) {
      dom.groupModalSaveBtn.addEventListener('click', saveStrategyGroup);
    }

    // Environment change hint
    dom.envSelect.addEventListener('change', () => {
      showToast('Click "Save Credentials" to apply new environment.', 'info');
    });

    // Close modals on escape key or clicking backdrop
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeOrderConfirmationModal();
        closeExitModal();
        closeGroupModal();
      }
    });

    [dom.orderModal, dom.exitModal, dom.groupModal].forEach((modal) => {
      if (modal) {
        modal.addEventListener('click', (e) => {
          if (e.target === modal) {
            closeOrderConfirmationModal();
            closeExitModal();
            closeGroupModal();
          }
        });
      }
    });
  }

  // --- Initializer ---
  async function init() {
    tickClock();
    setInterval(tickClock, 1000);

    // Load saved sound preference
    try {
      const savedSound = localStorage.getItem('delta_scanner_sound');
      if (savedSound === '0') {
        state.soundEnabled = false;
        dom.audioToggle.classList.add('muted');
        dom.audioIcon.textContent = '🔇';
        dom.audioText.textContent = 'SOUND MUTED';
      }
    } catch (_) {}

    await syncStrategyGroups();

    initEvents();
    updateFeedStateDisplay();
    updateTicketState();
    refreshHistory();

    // Auto-restore saved connection if present (survives browser refresh!)
    await restoreSavedConnection();

    // Auto-start live 1-second streaming scanner on load
    startScanner(true);
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
