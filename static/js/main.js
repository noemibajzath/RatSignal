/**
 * RatSignal Dashboard — main interaction logic.
 * Auto-refresh, account popup overlay, equity chart.
 */
(function () {
    var REFRESH_INTERVAL = 30;
    var countdown = REFRESH_INTERVAL;
    var activeDetail = null;

    // --- Auto-refresh ---
    function refreshData() {
        fetch('/api/accounts')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.accounts) return;
                data.accounts.forEach(function (acct) {
                    var ticker = document.getElementById('ticker-' + acct.id);
                    if (!ticker) return;
                    var pnlEl = document.getElementById('pnl-' + acct.id);
                    if (pnlEl) {
                        pnlEl.textContent = fmtPct(acct.pnl_pct);
                        pnlEl.className = 'ticker-pnl ' + (acct.pnl_pct >= 0 ? 'positive' : 'negative');
                    }
                    ticker.className = 'ticker-card ' + (acct.total_pnl >= 0 ? 'active-green' : 'active-red');
                    var statusEl = ticker.querySelector('.ticker-status');
                    if (statusEl) {
                        if (acct.status === 'live') {
                            statusEl.className = 'ticker-status live';
                            statusEl.innerHTML = '<span class="status-dot"></span> ACTIVE';
                        } else {
                            statusEl.className = 'ticker-status stopped';
                            statusEl.innerHTML = '<span class="status-dot"></span> PAUSED';
                        }
                    }
                });
                countdown = REFRESH_INTERVAL;
            })
            .catch(function () { countdown = REFRESH_INTERVAL; });
    }

    function tickCountdown() {
        countdown--;
        if (countdown <= 0) refreshData();
    }

    // --- Account popup ---
    function toggleDetail(accountId) {
        var overlay = document.getElementById('account-overlay');
        var popup = document.getElementById('account-popup');
        if (!overlay || !popup) return;

        if (activeDetail === accountId) {
            closeDetail();
            return;
        }

        activeDetail = accountId;
        popup.innerHTML = '<div style="padding:40px;text-align:center;color:#8888a0;">Loading...</div>';
        overlay.classList.add('active');
        document.body.style.overflow = 'hidden';

        fetch('/api/account/' + accountId)
            .then(function (r) { return r.json(); })
            .then(function (data) { renderPopup(popup, data); })
            .catch(function (err) {
                popup.innerHTML = '<div style="padding:40px;text-align:center;color:#ff4757;">Error: ' + err + '</div>';
            });
    }

    function closeDetail() {
        var overlay = document.getElementById('account-overlay');
        if (overlay) overlay.classList.remove('active');
        document.body.style.overflow = '';
        activeDetail = null;
    }

    function renderPopup(popup, data) {
        var stats = data.stats || {};
        var winRate = stats.win_rate || 0;
        var totalTrades = stats.total_trades || 0;
        var sharpe = stats.sharpe || 0;
        var avgBars = stats.avg_bars_held || 0;
        var maxDDPct = stats.max_drawdown_pct || 0;
        var eqPct = stats.equity_curve_pct || [];
        var pnlPct = (data.pnl_pct !== undefined && data.pnl_pct !== null) ? data.pnl_pct : (eqPct.length ? eqPct[eqPct.length - 1] : 0);

        var h = '';
        h += '<button class="popup-close" onclick="closeDetail()">&times;</button>';
        h += '<div class="popup-title">#' + data.id + ' ' + data.display_name + '</div>';
        h += '<div class="popup-desc">' + (data.description || '') + '</div>';
        if (data.days_live !== undefined) {
            h += '<div class="popup-desc" style="color:#06B6D4;margin-top:-12px;margin-bottom:16px;">';
            h += data.days_live + ' napja eles';
            h += '</div>';
        }

        // Top KPIs: PnL %, Trades, Win Rate
        h += '<div class="popup-stats" style="grid-template-columns:1fr 1fr 1fr;">';
        var pnlSign = pnlPct >= 0 ? '+' : '';
        h += pStat('PnL %', pnlSign + pnlPct.toFixed(2) + '%', pnlPct);
        h += pStat('Trades', totalTrades, null);
        h += pStat('Win Rate', winRate.toFixed(1) + '%', winRate >= 50 ? 1 : -1);
        h += '</div>';

        // Equity / PnL curve in PERCENT
        if (eqPct.length > 2) {
            h += '<div class="popup-section">Kumulativ PnL (%)</div>';
            h += '<canvas class="popup-equity" id="popup-equity-chart"></canvas>';
        }

        // Bottom row: Avg/Day PnL %, Avg/Day Trades, Max DD %
        var daysLive = (data.days_live && data.days_live > 0) ? data.days_live : 1;
        var avgDailyPnl = pnlPct / daysLive;
        var avgDailyTrades = totalTrades / daysLive;
        var avgPnlSign = avgDailyPnl >= 0 ? '+' : '';
        h += '<div class="popup-stats" style="grid-template-columns:1fr 1fr 1fr;">';
        h += pStat('Avg / nap PnL', avgPnlSign + avgDailyPnl.toFixed(2) + '%', avgDailyPnl);
        h += pStat('Avg / nap Trade', avgDailyTrades.toFixed(1), null);
        h += pStat('Max Drawdown', '-' + maxDDPct.toFixed(2) + '%', -1);
        h += '</div>';

        popup.innerHTML = h;

        if (eqPct.length > 2) {
            var canvas = document.getElementById('popup-equity-chart');
            if (canvas) drawEquity(canvas, eqPct, '%');
        }
    }

    function pStat(label, value, pnlVal) {
        var cls = 'neutral';
        if (pnlVal !== null && pnlVal !== undefined) cls = pnlVal >= 0 ? 'positive' : 'negative';
        return '<div class="popup-stat"><div class="popup-stat-label">' + label + '</div><div class="popup-stat-value ' + cls + '">' + value + '</div></div>';
    }

    // --- Equity chart ---
    function drawEquity(canvas, data, unit) { unit = unit || '';
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        var w = rect.width, ht = rect.height;
        var pad = 8;

        var min = Math.min.apply(null, data);
        var max = Math.max.apply(null, data);
        if (min === max) { min -= 1; max += 1; }
        var range = max - min;

        function x(i) { return pad + (i / (data.length - 1)) * (w - pad * 2); }
        function y(v) { return pad + (ht - pad * 2) - ((v - min) / range) * (ht - pad * 2); }

        // Zero line
        if (min < 0 && max > 0) {
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(136, 136, 160, 0.25)';
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.moveTo(pad, y(0));
            ctx.lineTo(w - pad, y(0));
            ctx.stroke();
            ctx.setLineDash([]);
        }

        var lastVal = data[data.length - 1];
        var isPos = lastVal >= 0;
        var rgb = isPos ? '0, 230, 118' : '255, 71, 87';

        // Fill
        var grad = ctx.createLinearGradient(0, pad, 0, ht - pad);
        grad.addColorStop(0, 'rgba(' + rgb + ', 0.18)');
        grad.addColorStop(1, 'rgba(' + rgb + ', 0.02)');
        ctx.beginPath();
        ctx.moveTo(x(0), y(data[0]));
        for (var i = 1; i < data.length; i++) ctx.lineTo(x(i), y(data[i]));
        ctx.lineTo(x(data.length - 1), ht - pad);
        ctx.lineTo(x(0), ht - pad);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        // Line
        ctx.beginPath();
        ctx.moveTo(x(0), y(data[0]));
        for (var j = 1; j < data.length; j++) ctx.lineTo(x(j), y(data[j]));
        ctx.strokeStyle = isPos ? '#00e676' : '#ff4757';
        ctx.lineWidth = 2;
        ctx.stroke();

        // End dot
        ctx.beginPath();
        ctx.arc(x(data.length - 1), y(lastVal), 3.5, 0, Math.PI * 2);
        ctx.fillStyle = isPos ? '#00e676' : '#ff4757';
        ctx.fill();
    }

    // --- Helpers ---
    // Strip trailing zeros: 1.20 → 1.2, 84250.0000 → 84250
    function fmtN(v, d) { return parseFloat(v.toFixed(d)).toString(); }

    function fmtPct(v) { return (v >= 0 ? '+' : '') + fmtN(v, 2) + '%'; }
    function fmtPnl(v) {
        if (v === undefined || v === null) return '$0.00';
        return v >= 0 ? '+$' + fmtN(v, 2) : '-$' + fmtN(Math.abs(v), 2);
    }
    function fmtNum(v) { return v >= 0 ? '+' + fmtN(v, 2) : fmtN(v, 2); }

    // --- Keyboard ---
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeDetail();
    });

    // --- Globals ---
    window.toggleDetail = toggleDetail;
    window.closeDetail = closeDetail;

    // --- Init ---
    document.addEventListener('DOMContentLoaded', function () {
        setInterval(tickCountdown, 1000);
        setTimeout(refreshData, REFRESH_INTERVAL * 1000);
        if (typeof Atlas !== 'undefined') Atlas.init();
    });
})();
