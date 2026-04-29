/**
 * RatSignal Flow — Live Chart
 * Pine Script → JS port: Fisher Transform + WaveTrend + rsiMFI + Signal Detection
 * Custom HTML signal overlays matching TradingView look
 * Powered by Lightweight Charts + Binance WebSocket
 */
(function () {
  'use strict';

  const CONF = {
    symbol: 'BTCUSDT', interval: '15m', limit: 500,
    rest: 'https://api.binance.com/api/v3/klines',
    ws: 'wss://stream.binance.com:9443/ws/btcusdt@kline_15m',
    fisherLen: 10,
    wtCh: 9, wtAvg: 13, wtMA: 3,
    mfiLen: 60, rsiLen: 14,
    ob: 60, os: -60, cooldown: 30,
  };

  let candles = [];
  let charts = null;
  let signals = [];

  // ======================== MATH ========================

  function ema(data, period) {
    const out = new Array(data.length);
    const k = 2 / (period + 1);
    out[0] = data[0];
    for (let i = 1; i < data.length; i++)
      out[i] = data[i] * k + out[i - 1] * (1 - k);
    return out;
  }

  function sma(data, period) {
    const out = new Array(data.length).fill(null);
    for (let i = period - 1; i < data.length; i++) {
      let s = 0;
      for (let j = 0; j < period; j++) s += data[i - j];
      out[i] = s / period;
    }
    return out;
  }

  function highest(data, period) {
    const out = new Array(data.length);
    for (let i = 0; i < data.length; i++) {
      let mx = -Infinity;
      for (let j = Math.max(0, i - period + 1); j <= i; j++)
        if (data[j] > mx) mx = data[j];
      out[i] = mx;
    }
    return out;
  }

  function lowest(data, period) {
    const out = new Array(data.length);
    for (let i = 0; i < data.length; i++) {
      let mn = Infinity;
      for (let j = Math.max(0, i - period + 1); j <= i; j++)
        if (data[j] < mn) mn = data[j];
      out[i] = mn;
    }
    return out;
  }

  function rsi(closes, period) {
    const out = new Array(closes.length).fill(50);
    if (closes.length <= period) return out;
    let avgG = 0, avgL = 0;
    for (let i = 1; i <= period; i++) {
      const d = closes[i] - closes[i - 1];
      if (d > 0) avgG += d; else avgL -= d;
    }
    avgG /= period; avgL /= period;
    out[period] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
      avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
      out[i] = avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL);
    }
    return out;
  }

  // ======================== INDICATOR ========================

  function calcIndicators(bars) {
    const n = bars.length;
    const O = bars.map(c => c.open), H = bars.map(c => c.high);
    const L = bars.map(c => c.low), C = bars.map(c => c.close);
    const hlc3 = bars.map(c => (c.high + c.low + c.close) / 3);

    // Fisher Transform
    const hh = highest(H, CONF.fisherLen), ll = lowest(L, CONF.fisherLen);
    const fisherRaw = [], fisher = [];
    for (let i = 0; i < n; i++) {
      const rng = hh[i] - ll[i];
      const raw = rng > 0 ? 2 * ((C[i] - ll[i]) / rng) - 1 : 0;
      const cl = Math.max(-0.999, Math.min(0.999, raw));
      fisherRaw[i] = 0.5 * Math.log((1 + cl) / (1 - cl));
      fisher[i] = fisherRaw[i] * 25;
    }

    // WaveTrend
    const esaArr = ema(hlc3, CONF.wtCh);
    const devArr = hlc3.map((v, i) => Math.abs(v - esaArr[i]));
    const deArr = ema(devArr, CONF.wtCh);
    const ci = [];
    for (let i = 0; i < n; i++) {
      const raw = deArr[i] !== 0 ? (hlc3[i] - esaArr[i]) / (0.015 * deArr[i]) : 0;
      ci[i] = Math.max(-150, Math.min(150, raw));
    }
    const wt1 = ema(ci, CONF.wtAvg);
    const wt2 = sma(wt1, CONF.wtMA);

    // RSI + rsiMFI
    const rsiArr = rsi(C, CONF.rsiLen);
    const mfiRaw = bars.map((_, i) => {
      const hlr = H[i] - L[i];
      return hlr > 0 ? ((C[i] - O[i]) / hlr) * 150 : 0;
    });
    const rsiMFI = sma(mfiRaw, CONF.mfiLen);

    // EMA ribbon
    const e5 = ema(C, 5), e11 = ema(C, 11), e15 = ema(C, 15), e34 = ema(C, 34);

    // Signal detection — typed signals for custom HTML rendering
    const sigs = [];
    let lastLB = -999, lastSB = -999;

    for (let i = 1; i < n; i++) {
      if (wt2[i] == null) continue;
      const wtUp = wt1[i] > wt2[i] && wt1[i - 1] <= wt2[i - 1];
      const wtDn = wt1[i] < wt2[i] && wt1[i - 1] >= wt2[i - 1];
      const gd = e11[i] > e34[i] && e11[i - 1] <= e34[i - 1];
      const rx = e5[i] < e11[i] && e5[i - 1] >= e11[i - 1];
      const bt = e11[i] > e15[i] && e11[i - 1] <= e15[i - 1];
      const se = e34[i] > e11[i] && e34[i - 1] <= e11[i - 1];
      const bd = wtDn && rx;
      const yx = wtDn && wt2[i] < 45 && wt2[i] > -80
        && rsiArr[i] < 30 && rsiArr[i] > 15 && (rsiMFI[i] || 0) < -5;

      const fUp = fisherRaw[i] > 0 && fisherRaw[i - 1] <= 0;
      const fDn = fisherRaw[i] < 0 && fisherRaw[i - 1] >= 0;
      const rlr = gd || (fUp && wt1[i] > wt2[i] && wt2[i] < -20);
      const rsr = (se || bd) || (fDn && wt1[i] < wt2[i] && wt2[i] > 20);
      const rlc = rlr && gd;
      const rsc = rsr && rx;
      const rl = rlc && (i - lastLB >= CONF.cooldown);
      const rs = rsc && (i - lastSB >= CONF.cooldown);
      if (rl) lastLB = i;
      if (rs) lastSB = i;

      const t = bars[i].time;

      // Rat signals
      if (rl) sigs.push({ time: t, type: 'ratLong' });
      else if (gd) sigs.push({ time: t, type: 'greenDot' });
      if (rs) sigs.push({ time: t, type: 'ratShort' });
      else if (rx) sigs.push({ time: t, type: 'redX' });
      if (bt && !rl) sigs.push({ time: t, type: 'blueTriangle' });
      if (yx) sigs.push({ time: t, type: 'yellowX' });

      // WT cross dots (at wt2 value)
      if (wtUp) sigs.push({ time: t, type: 'wtUp', value: wt2[i] });
      if (wtDn) sigs.push({ time: t, type: 'wtDn', value: wt2[i] });
    }

    return { fisher, wt1, wt2, rsiMFI, signals: sigs };
  }

  // ======================== BINANCE API ========================

  function parseKline(k) {
    return {
      time: Math.floor(k[0] / 1000),
      open: parseFloat(k[1]), high: parseFloat(k[2]),
      low: parseFloat(k[3]), close: parseFloat(k[4]),
    };
  }

  async function fetchHistory() {
    const url = `${CONF.rest}?symbol=${CONF.symbol}&interval=${CONF.interval}&limit=${CONF.limit}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('Binance API error: ' + res.status);
    return (await res.json()).map(parseKline);
  }

  // ======================== CHART SETUP ========================

  function darkOpts(height) {
    return {
      height: height,
      autoSize: true,
      layout: { background: { color: '#0f1f35' }, textColor: '#8888a0', fontFamily: 'Inter, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: '#1a2d4a22' }, horzLines: { color: '#1a2d4a44' } },
      timeScale: { borderColor: '#1a2d4a', timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#1a2d4a' },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      handleScale: { mouseWheel: false },
      handleScroll: { mouseWheel: false },
    };
  }

  function initCharts() {
    const cEl = document.getElementById('rsChartCandle');
    const iEl = document.getElementById('rsChartIndicator');
    if (!cEl || !iEl) return null;

    const cChart = LightweightCharts.createChart(cEl, darkOpts(320));
    const iChart = LightweightCharts.createChart(iEl, darkOpts(220));

    const cSeries = cChart.addCandlestickSeries({
      upColor: '#00e676', downColor: '#ff4757',
      borderUpColor: '#00e676', borderDownColor: '#ff4757',
      wickUpColor: '#00e67688', wickDownColor: '#ff475788',
    });

    const fisherS = iChart.addLineSeries({ lineWidth: 2, priceScaleId: 'right', lastValueVisible: false, priceLineVisible: false });
    const wt1S = iChart.addLineSeries({ color: '#00BFFF', lineWidth: 2, priceScaleId: 'right', lastValueVisible: false, priceLineVisible: false });
    const wt2S = iChart.addLineSeries({ color: '#FF6600', lineWidth: 1, priceScaleId: 'right', lastValueVisible: false, priceLineVisible: false });
    const mfiS = iChart.addHistogramSeries({ priceScaleId: 'right', lastValueVisible: false, priceLineVisible: false });

    // Reference lines
    wt1S.createPriceLine({ price: CONF.ob, color: '#FF073A50', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    wt1S.createPriceLine({ price: CONF.os, color: '#39FF1450', lineWidth: 1, lineStyle: 2, axisLabelVisible: false });
    wt1S.createPriceLine({ price: 0, color: '#546E7A50', lineWidth: 1, lineStyle: 0, axisLabelVisible: false });

    // Signal overlay container (positioned over the indicator chart)
    iEl.style.position = 'relative';
    const overlay = document.createElement('div');
    overlay.id = 'rsSignalOverlay';
    overlay.style.cssText = 'position:absolute;top:0;left:0;right:0;bottom:0;pointer-events:none;overflow:hidden;z-index:5;';
    iEl.appendChild(overlay);

    // Enable mouse wheel zoom/scroll only when cursor moves over chart
    function enableMouseWheel(chart) {
      chart.applyOptions({ handleScale: { mouseWheel: true }, handleScroll: { mouseWheel: true } });
    }
    function disableMouseWheel(chart) {
      chart.applyOptions({ handleScale: { mouseWheel: false }, handleScroll: { mouseWheel: false } });
    }
    cEl.addEventListener('mousemove', function() { enableMouseWheel(cChart); });
    cEl.addEventListener('mouseleave', function() { disableMouseWheel(cChart); });
    iEl.addEventListener('mousemove', function() { enableMouseWheel(iChart); });
    iEl.addEventListener('mouseleave', function() { disableMouseWheel(iChart); });

    // Sync time scales
    let syncing = false;
    cChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      iChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
    });
    iChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      cChart.timeScale().setVisibleLogicalRange(range);
      syncing = false;
      renderSignalOverlays();
    });

    return { cChart, iChart, cSeries, fisherS, wt1S, wt2S, mfiS };
  }

  // ======================== SIGNAL OVERLAY RENDERING ========================

  function renderSignalOverlays() {
    const overlay = document.getElementById('rsSignalOverlay');
    if (!overlay || !charts || !signals.length) return;
    overlay.innerHTML = '';

    for (const sig of signals) {
      const x = charts.iChart.timeScale().timeToCoordinate(sig.time);
      if (x === null || x < 0) continue;

      const el = document.createElement('div');
      el.style.cssText = 'position:absolute;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;';
      el.style.left = x + 'px';

      switch (sig.type) {
        case 'ratLong':
          el.style.bottom = '28px';
          el.innerHTML =
            '<div style="background:#39FF14;padding:3px 6px;border-radius:4px 4px 0 0;font-size:14px;line-height:1;border-bottom:2px solid #39FF14;">\u{1F400}</div>' +
            '<div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-top:6px solid #39FF14;"></div>' +
            '<div style="color:#39FF14;font-size:10px;font-weight:700;margin-top:1px;text-shadow:0 0 4px #39FF1488;">LONG</div>';
          break;

        case 'ratShort':
          el.style.top = '2px';
          el.innerHTML =
            '<div style="color:#FF073A;font-size:10px;font-weight:700;margin-bottom:1px;text-shadow:0 0 4px #FF073A88;">SHORT</div>' +
            '<div style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:6px solid #FF073A;"></div>' +
            '<div style="background:#FF073A;padding:3px 6px;border-radius:0 0 4px 4px;font-size:14px;line-height:1;border-top:2px solid #FF073A;">\u{1F400}</div>';
          break;

        case 'greenDot':
          el.style.bottom = '28px';
          el.innerHTML = '<div style="width:8px;height:8px;border-radius:50%;background:#39FF14;box-shadow:0 0 4px #39FF14;"></div>';
          break;

        case 'redX':
          el.style.top = '5px';
          el.innerHTML = '<div style="color:#FF073A;font-size:14px;font-weight:900;text-shadow:0 0 4px #FF073A;">✕</div>';
          break;

        case 'blueTriangle':
          el.style.bottom = '28px';
          el.innerHTML = '<div style="width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-bottom:8px solid #00BFFF;filter:drop-shadow(0 0 2px #00BFFF);"></div>';
          break;

        case 'yellowX':
          el.style.bottom = '28px';
          el.innerHTML = '<div style="color:#FFE600;font-size:14px;font-weight:900;text-shadow:0 0 4px #FFE600;">✕</div>';
          break;

        case 'wtUp': {
          const y = charts.wt1S.priceToCoordinate(sig.value);
          if (y === null) continue;
          el.style.cssText = 'position:absolute;transform:translate(-50%,-50%);';
          el.style.left = x + 'px';
          el.style.top = y + 'px';
          el.innerHTML = '<div style="width:10px;height:10px;border-radius:50%;background:#39FF14;box-shadow:0 0 6px #39FF14;"></div>';
          break;
        }

        case 'wtDn': {
          const y = charts.wt1S.priceToCoordinate(sig.value);
          if (y === null) continue;
          el.style.cssText = 'position:absolute;transform:translate(-50%,-50%);';
          el.style.left = x + 'px';
          el.style.top = y + 'px';
          el.innerHTML = '<div style="width:10px;height:10px;border-radius:50%;background:#FF073A;box-shadow:0 0 6px #FF073A;"></div>';
          break;
        }

        default:
          continue;
      }

      overlay.appendChild(el);
    }
  }

  // ======================== RENDER ========================

  function renderAll() {
    if (!charts || candles.length < 40) return;
    const ind = calcIndicators(candles);
    signals = ind.signals;

    // Candles
    charts.cSeries.setData(candles);

    // Fisher (per-bar color)
    const fisherData = [];
    for (let i = 0; i < candles.length; i++) {
      if (ind.fisher[i] == null) continue;
      fisherData.push({ time: candles[i].time, value: ind.fisher[i], color: ind.fisher[i] >= 0 ? '#00FFFF' : '#FF6D00' });
    }
    charts.fisherS.setData(fisherData);

    // WT1 & WT2
    const wt1Data = [], wt2Data = [];
    for (let i = 0; i < candles.length; i++) {
      if (ind.wt1[i] == null || ind.wt2[i] == null) continue;
      wt1Data.push({ time: candles[i].time, value: ind.wt1[i] });
      wt2Data.push({ time: candles[i].time, value: ind.wt2[i] });
    }
    charts.wt1S.setData(wt1Data);
    charts.wt2S.setData(wt2Data);

    // rsiMFI histogram
    const mfiData = [];
    for (let i = 0; i < candles.length; i++) {
      if (ind.rsiMFI[i] == null) continue;
      const v = ind.rsiMFI[i];
      mfiData.push({ time: candles[i].time, value: v, color: v >= 0 ? '#39FF1466' : '#FF073A66' });
    }
    charts.mfiS.setData(mfiData);

    // Custom HTML signal overlays
    renderSignalOverlays();

    // Price display
    const last = candles[candles.length - 1];
    const priceEl = document.getElementById('rsChartPrice');
    if (priceEl && last) {
      priceEl.textContent = '$' + last.close.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      priceEl.style.color = last.close >= last.open ? '#00e676' : '#ff4757';
    }
  }

  function updateLive(bar) {
    if (!charts) return;
    const lastIdx = candles.length - 1;
    charts.cSeries.update(bar);

    const ind = calcIndicators(candles);
    const t = bar.time;

    if (ind.fisher[lastIdx] != null)
      charts.fisherS.update({ time: t, value: ind.fisher[lastIdx], color: ind.fisher[lastIdx] >= 0 ? '#00FFFF' : '#FF6D00' });
    if (ind.wt1[lastIdx] != null)
      charts.wt1S.update({ time: t, value: ind.wt1[lastIdx] });
    if (ind.wt2[lastIdx] != null)
      charts.wt2S.update({ time: t, value: ind.wt2[lastIdx] });
    if (ind.rsiMFI[lastIdx] != null) {
      const v = ind.rsiMFI[lastIdx];
      charts.mfiS.update({ time: t, value: v, color: v >= 0 ? '#39FF1466' : '#FF073A66' });
    }

    const priceEl = document.getElementById('rsChartPrice');
    if (priceEl) {
      priceEl.textContent = '$' + bar.close.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      priceEl.style.color = bar.close >= bar.open ? '#00e676' : '#ff4757';
    }
  }

  // ======================== WEBSOCKET ========================

  function connectWS() {
    let ws;
    function connect() {
      ws = new WebSocket(CONF.ws);
      ws.onmessage = function (evt) {
        const msg = JSON.parse(evt.data);
        const k = msg.k;
        if (!k) return;
        const bar = {
          time: Math.floor(k.t / 1000),
          open: parseFloat(k.o), high: parseFloat(k.h),
          low: parseFloat(k.l), close: parseFloat(k.c),
        };

        const last = candles[candles.length - 1];
        if (bar.time === last.time) {
          candles[candles.length - 1] = bar;
          updateLive(bar);
        } else if (bar.time > last.time) {
          candles.push(bar);
          if (candles.length > CONF.limit) candles.shift();
          renderAll();
        }
      };
      ws.onclose = function () { setTimeout(connect, 3000); };
      ws.onerror = function () { ws.close(); };
    }
    connect();
  }

  // ======================== INIT ========================

  async function init() {
    const wrapper = document.getElementById('rsChartWrapper');
    if (!wrapper) return;

    charts = initCharts();
    if (!charts) return;

    try {
      wrapper.classList.add('loading');
      candles = await fetchHistory();
      renderAll();
      wrapper.classList.remove('loading');
      connectWS();
    } catch (e) {
      console.error('RatSignal Chart error:', e);
      wrapper.classList.remove('loading');
      wrapper.classList.add('error');
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
