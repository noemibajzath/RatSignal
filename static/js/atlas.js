/**
 * RatSignal ATLAS Dashboard — Tab routing + data fetching + rendering.
 * Polls the active ATLAS tab every 30s, renders via DOM manipulation + Canvas.
 */
var Atlas = (function () {
    'use strict';

    var _activeTab = 'trading';
    var _cache = {};
    var _POLL_INTERVAL = 30000;
    var _pollTimer = null;

    // --- Color constants ---
    var C = {
        cyan: '#00d4ff',
        gold: '#f5a623',
        orange: '#ff8c00',
        green: '#00e676',
        red: '#ff3366',
        purple: '#bb86fc',
        blue: '#448aff',
        text: '#e8eaf6',
        textSec: 'rgba(160,170,200,0.7)',
        textMuted: 'rgba(100,110,140,0.5)',
    };

    // --- Mood config ---
    var MOOD_MAP = {
        neutral: { icon: '😐', color: C.cyan },
        confident: { icon: '😎', color: C.green },
        cautious: { icon: '🤔', color: C.gold },
        defensive: { icon: '🛡', color: C.orange },
        alarmed: { icon: '🚨', color: C.red },
        creative: { icon: '🎨', color: C.purple },
        unknown: { icon: '❓', color: C.textSec },
    };

    // --- Tab switching ---
    function switchTab(tabName) {
        _activeTab = tabName;

        // Update nav tab styles
        document.querySelectorAll('.nav-tab').forEach(function (el) {
            el.classList.toggle('active', el.getAttribute('data-tab') === tabName);
        });

        // Show/hide tab content
        document.querySelectorAll('.tab-content').forEach(function (el) {
            el.classList.toggle('active', el.id === 'tab-' + tabName);
        });

        // Fetch ATLAS data for non-trading tabs
        if (tabName !== 'trading') {
            fetchAndRender(tabName);
        }

        // Update URL hash
        history.replaceState(null, '', '#' + tabName);
    }

    function fetchAndRender(tabName) {
        var endpoint = '/api/atlas/' + tabName;
        fetch(endpoint)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    renderError(tabName, data.error);
                    return;
                }
                _cache[tabName] = data;
                render(tabName, data);
            })
            .catch(function (err) {
                renderError(tabName, err.toString());
            });
    }

    function renderError(tabName, msg) {
        var el = document.getElementById('tab-' + tabName);
        if (el) {
            el.innerHTML = '<div class="atlas-error"><div class="atlas-error-icon">⚠</div>' +
                '<div class="atlas-error-msg">' + escHtml(msg) + '</div></div>';
        }
    }

    // --- Render dispatcher ---
    function render(tabName, data) {
        switch (tabName) {
            case 'brain': renderBrain(data); break;
            case 'timeline': renderTimeline(data); break;
            case 'strategy': renderStrategy(data); break;
            case 'market': renderMarket(data); break;
            case 'system': renderSystem(data); break;
        }
    }

    // =========================================================================
    // BRAIN TAB
    // =========================================================================
    function renderBrain(data) {
        var el = document.getElementById('tab-brain');
        var id = data.identity || {};
        var goals = data.goals || {};
        var budget = data.lane_budget || {};
        var kernel = data.kernel || {};
        var dream = data.dream || {};
        var cb = data.circuit_breaker || {};

        var mood = MOOD_MAP[id.mood] || MOOD_MAP.unknown;
        var confidence = id.confidence || 0;
        var streak = id.streak || {};

        var html = '';

        // Circuit breaker alert
        if (cb.active) {
            html += '<div class="atlas-alert alert-danger">' +
                '<span class="alert-icon">🚨</span> CIRCUIT BREAKER ACTIVE — ' +
                escHtml(cb.reason || 'Unknown reason') +
                (cb.level ? ' (Level: ' + escHtml(cb.level) + ')' : '') +
                '</div>';
        }

        // Identity card + Kernel status row
        html += '<div class="brain-top-row">';

        // Identity card
        html += '<div class="atlas-card brain-identity">';
        html += '<div class="card-section-title">Identity</div>';
        html += '<div class="identity-row">';
        html += '<div class="identity-mood" style="color:' + mood.color + '">' +
            '<span class="mood-icon">' + mood.icon + '</span>' +
            '<span class="mood-label">' + escHtml(id.mood || 'unknown') + '</span></div>';
        html += '<div class="identity-meta">' +
            '<span class="meta-item">' + escHtml(id.name || 'Atlas') + ' v' + escHtml(id.version || '?') + '</span>' +
            '<span class="meta-item">Focus: <strong>' + escHtml(id.current_focus || '-') + '</strong></span></div>';
        html += '</div>';

        // Confidence bar
        html += '<div class="confidence-section">';
        html += '<div class="confidence-header"><span>Confidence</span><span class="confidence-val">' +
            (confidence * 100).toFixed(0) + '%</span></div>';
        html += '<canvas class="confidence-bar" id="brain-confidence"></canvas>';
        html += '</div>';

        // Streak
        html += '<div class="streak-row">' +
            '<span class="streak-wins">W' + (streak.wins || 0) + '</span>' +
            '<span class="streak-losses">L' + (streak.losses || 0) + '</span>';
        var stats = id.lifetime_stats || {};
        if (stats.trades_total) {
            html += '<span class="streak-meta">' + stats.trades_total + ' trades</span>';
        }
        html += '</div>';
        html += '</div>'; // /brain-identity

        // Kernel status
        html += '<div class="atlas-card brain-kernel">';
        html += '<div class="card-section-title">Kernel</div>';
        html += '<div class="kernel-stats">';
        html += kernelStat('Lane', kernel.current_lane || '-');
        html += kernelStat('Cycle', '#' + (kernel.cycle_count || 0));
        html += kernelStat('Last Pulse', formatTimeAgo(kernel.last_pulse_at));
        html += kernelStat('Last Think', formatTimeAgo(kernel.last_think_at));
        html += kernelStat('Last Dream', formatTimeAgo(kernel.last_dream_at));
        html += kernelStat('Started', formatTimeAgo(kernel.started_at));
        html += '</div>';

        // Dream state
        if (dream && dream.status !== 'idle') {
            html += '<div class="dream-state">';
            html += '<span class="dream-badge">' + escHtml(dream.status || 'idle') + '</span>';
            if (dream.current_task) {
                html += '<span class="dream-task">' + escHtml(dream.current_task) + '</span>';
            }
            html += '</div>';
        }
        html += '</div>'; // /brain-kernel

        html += '</div>'; // /brain-top-row

        // Lane budget donut
        html += '<div class="brain-bottom-row">';

        html += '<div class="atlas-card brain-budget">';
        html += '<div class="card-section-title">Lane Budget</div>';
        html += '<div class="budget-content">';
        html += '<canvas id="brain-donut" class="budget-donut"></canvas>';
        html += '<div class="budget-legend" id="budget-legend"></div>';
        html += '</div>';
        html += '</div>';

        // Goals
        html += '<div class="atlas-card brain-goals">';
        html += '<div class="card-section-title">Goals</div>';
        html += '<div class="goals-grid" id="goals-grid"></div>';
        html += '</div>';

        html += '</div>'; // /brain-bottom-row

        el.innerHTML = html;

        // Draw confidence bar
        setTimeout(function () {
            var confCanvas = document.getElementById('brain-confidence');
            if (confCanvas) {
                var confColor = confidence >= 0.7 ? C.green : confidence >= 0.4 ? C.gold : C.red;
                RatCharts.drawProgressBar(confCanvas, confidence, 1, { color: confColor });
            }

            // Draw donut
            var donutCanvas = document.getElementById('brain-donut');
            if (donutCanvas && budget) {
                var laneColors = {
                    trading: C.cyan,
                    optimization: C.gold,
                    research: C.purple,
                    reliability: C.green,
                };
                var segments = [];
                var legendHtml = '';
                Object.keys(budget).forEach(function (lane) {
                    var val = budget[lane] || 0;
                    var color = laneColors[lane] || C.textSec;
                    segments.push({ value: val, color: color });
                    legendHtml += '<div class="legend-item">' +
                        '<span class="legend-dot" style="background:' + color + '"></span>' +
                        '<span class="legend-name">' + lane + '</span>' +
                        '<span class="legend-pct">' + (val * 100).toFixed(0) + '%</span></div>';
                });
                RatCharts.drawDonut(donutCanvas, segments, {
                    centerLabel: data.lane_priority || '-',
                    centerSub: 'priority',
                    lineWidth: 16,
                });
                var legendEl = document.getElementById('budget-legend');
                if (legendEl) legendEl.innerHTML = legendHtml;
            }

            // Render goals
            var goalsGrid = document.getElementById('goals-grid');
            if (goalsGrid && goals) {
                var goalsHtml = '';
                Object.keys(goals).forEach(function (key) {
                    var g = goals[key];
                    if (typeof g !== 'object') return;
                    var statusColor = { green: C.green, yellow: C.gold, red: C.red, pending: C.textMuted }[g.status] || C.textMuted;
                    goalsHtml += '<div class="goal-card">' +
                        '<div class="goal-header">' +
                        '<span class="goal-name">' + escHtml(key.replace(/_/g, ' ')) + '</span>' +
                        '<span class="goal-status" style="color:' + statusColor + '">' + escHtml(g.status || 'pending') + '</span></div>' +
                        '<div class="goal-value">' + escHtml(String(g.current != null ? g.current : '-')) +
                        ' <span class="goal-target">/ ' + escHtml(String(g.target || '?')) + '</span></div>' +
                        '</div>';
                });
                goalsGrid.innerHTML = goalsHtml;
            }
        }, 10);
    }

    function kernelStat(label, value) {
        return '<div class="kernel-stat"><span class="kernel-label">' + label +
            '</span><span class="kernel-value">' + escHtml(value) + '</span></div>';
    }

    // =========================================================================
    // TIMELINE TAB
    // =========================================================================
    function renderTimeline(data) {
        var el = document.getElementById('tab-timeline');
        var events = data.events || [];

        if (!events.length) {
            el.innerHTML = '<div class="no-data-msg">No events recorded yet.</div>';
            return;
        }

        var html = '<div class="timeline-header">' +
            '<span class="timeline-count">' + events.length + ' events</span></div>';
        html += '<div class="timeline-list">';

        var typeColors = {
            pulse_start: C.cyan,
            pulse_end: C.cyan,
            think_start: C.gold,
            think_end: C.gold,
            dream_start: C.purple,
            dream_end: C.purple,
            agent_dispatch: C.green,
            risk_change: C.orange,
            emergency_halt: C.red,
            lane_switch: C.blue,
            kernel_start: C.textSec,
            kernel_stop: C.textSec,
            kernel_error: C.red,
        };

        events.forEach(function (evt) {
            var ts = evt.ts || '';
            var type = evt.type || 'unknown';
            var detail = evt.detail || '';
            var color = typeColors[type] || C.textSec;
            var success = evt.success;
            var agent = evt.agent || '';

            html += '<div class="timeline-event">';
            html += '<div class="event-time">' + escHtml(ts.slice(11, 19) || '') + '</div>';
            html += '<div class="event-dot" style="background:' + color + '"></div>';
            html += '<div class="event-body">';
            html += '<span class="event-type" style="color:' + color + '">' + escHtml(type) + '</span>';
            if (agent) html += '<span class="event-agent">' + escHtml(agent) + '</span>';
            if (success === false) html += '<span class="event-fail">FAIL</span>';
            html += '<div class="event-detail">' + escHtml(detail) + '</div>';
            html += '</div></div>';
        });

        html += '</div>';
        el.innerHTML = html;
    }

    // =========================================================================
    // STRATEGY TAB
    // =========================================================================
    function renderStrategy(data) {
        var el = document.getElementById('tab-strategy');
        var decider = data.decider || {};
        var scorecard = data.scorecard || {};
        var hypotheses = data.hypotheses || [];

        var html = '';

        // Decider panel
        html += '<div class="card-section-title">Decider Decisions</div>';
        html += '<div class="decider-panel">';
        html += renderDeciderSide(decider.decisions ? decider.decisions.long : null, 'long');
        html += renderDeciderSide(decider.decisions ? decider.decisions.short : null, 'short');
        html += '</div>';

        if (decider.dry_run) {
            html += '<div class="decider-meta"><span class="dry-run-badge">DRY RUN</span>';
            if (decider.regime) html += '<span class="regime-badge">' + escHtml(decider.regime) + '</span>';
            html += '</div>';
        }

        // Scorecard
        html += '<div class="card-section-title" style="margin-top:24px">Strategy Scorecard</div>';
        html += renderScorecard(scorecard);

        // Hypotheses
        if (hypotheses.length > 0) {
            html += '<div class="card-section-title" style="margin-top:24px">Hypotheses <span class="count">' +
                hypotheses.length + '</span></div>';
            html += '<div class="hypotheses-grid">';
            hypotheses.forEach(function (h) {
                var dirClass = (h.direction || 'long') === 'long' ? 'dir-long' : 'dir-short';
                html += '<div class="hypothesis-card">';
                html += '<span class="hyp-dir ' + dirClass + '">' + (h.direction || '?').toUpperCase() + '</span>';
                html += '<span class="hyp-name">' + escHtml(h.name || h.combo_key || '?') + '</span>';
                if (h.indicators) html += '<div class="hyp-indicators">' + h.indicators.map(escHtml).join(' + ') + '</div>';
                if (h.logic) html += '<span class="hyp-logic">' + escHtml(h.logic) + '</span>';
                if (h.priority) html += '<span class="hyp-priority">P' + h.priority + '</span>';
                html += '</div>';
            });
            html += '</div>';
        }

        el.innerHTML = html;
    }

    function renderDeciderSide(decision, direction) {
        var dirClass = direction === 'long' ? 'dir-long' : 'dir-short';
        var html = '<div class="decider-side ' + dirClass + '">';
        html += '<div class="decider-dir-label ' + dirClass + '">' + direction.toUpperCase() + '</div>';

        if (!decision) {
            html += '<div class="no-data-msg">No decision</div>';
            html += '</div>';
            return html;
        }

        html += '<div class="decider-action">' + escHtml(decision.action || 'hold') + '</div>';
        if (decision.new_combo) {
            var nc = decision.new_combo;
            html += '<div class="decider-combo">';
            html += '<div class="decider-combo-key">' + escHtml(nc.combo_key || nc.name || '-') + '</div>';
            if (nc.indicators) {
                nc.indicators.forEach(function (ind) {
                    html += '<div class="decider-indicator">' + escHtml(ind) + '</div>';
                });
            }
            if (nc.pnl !== undefined) html += '<div class="decider-stat">PnL: ' + formatPnlVal(nc.pnl) + '</div>';
            if (nc.win_rate !== undefined) html += '<div class="decider-stat">WR: ' + nc.win_rate.toFixed(1) + '%</div>';
            html += '</div>';
        }
        if (decision.reason) {
            html += '<div class="decider-reason">' + escHtml(decision.reason) + '</div>';
        }

        html += '</div>';
        return html;
    }

    function renderScorecard(scorecard) {
        if (!scorecard || typeof scorecard !== 'object') {
            return '<div class="no-data-msg">No scorecard data.</div>';
        }

        var html = '<div class="scorecard-sections">';
        ['long', 'short'].forEach(function (dir) {
            var dirData = scorecard[dir];
            if (!dirData) return;
            var dirClass = dir === 'long' ? 'dir-long' : 'dir-short';

            html += '<div class="scorecard-dir">';
            html += '<div class="scorecard-dir-label ' + dirClass + '">' + dir.toUpperCase() + '</div>';

            ['live', 'shadow'].forEach(function (tier) {
                var tierData = dirData[tier];
                if (!tierData) return;
                var combos = tierData.per_combo || tierData;
                if (typeof combos !== 'object') return;

                html += '<div class="scorecard-tier-label">' + tier + '</div>';
                html += '<table class="data-table"><thead><tr>' +
                    '<th>Combo</th><th>PnL</th><th>WR</th><th>Trades</th>' +
                    '</tr></thead><tbody>';

                Object.keys(combos).forEach(function (key) {
                    var c = combos[key];
                    if (typeof c !== 'object') return;
                    var pnl = c.total_pnl || c.pnl || 0;
                    var pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
                    html += '<tr>' +
                        '<td>' + escHtml(key) + '</td>' +
                        '<td class="' + pnlClass + '">' + formatPnlVal(pnl) + '</td>' +
                        '<td>' + ((c.win_rate || 0)).toFixed(1) + '%</td>' +
                        '<td>' + (c.trades || c.trade_count || 0) + '</td>' +
                        '</tr>';
                });

                html += '</tbody></table>';
            });

            html += '</div>';
        });
        html += '</div>';
        return html;
    }

    // =========================================================================
    // MARKET TAB
    // =========================================================================
    function renderMarket(data) {
        var el = document.getElementById('tab-market');
        var html = '';

        // Fear & Greed gauge
        var fg = data.fear_greed || {};
        html += '<div class="market-top-row">';
        html += '<div class="atlas-card market-gauge-card">';
        html += '<div class="card-section-title">Fear & Greed Index</div>';
        html += '<canvas id="market-gauge" class="fear-greed-gauge"></canvas>';
        html += '<div class="fg-classification">' + escHtml(fg.classification || 'N/A') + '</div>';
        html += '</div>';

        // Funding extreme / OI spike alerts
        html += '<div class="atlas-card market-alerts-card">';
        html += '<div class="card-section-title">Market Signals</div>';
        html += '<div class="signal-grid">';
        html += signalItem('Funding Extreme', data.funding_extreme, data.funding_extreme ? 'EXTREME' : 'Normal');
        html += signalItem('OI Spike', data.oi_spike, data.oi_spike ? 'SPIKE' : 'Normal');

        // Sentiment
        var sentiment = data.sentiment || {};
        if (sentiment.overall) {
            html += signalItem('Sentiment', null, escHtml(sentiment.overall));
        }
        html += '</div>';

        // Kill switch
        var ks = data.kill_switch || {};
        if (ks.active) {
            html += '<div class="atlas-alert alert-danger">Kill Switch Active: ' + escHtml(ks.reason || '') + '</div>';
        }
        html += '</div>';
        html += '</div>'; // /market-top-row

        // Funding rates grid
        html += '<div class="card-section-title">Funding Rates</div>';
        var fundingRates = data.funding_rates || {};
        var fundingSignals = data.funding_signals || {};
        html += '<div class="funding-grid">';
        Object.keys(fundingRates).forEach(function (pair) {
            var rate = fundingRates[pair];
            var signal = fundingSignals[pair] || {};
            var rateStr = typeof rate === 'number' ? (rate * 100).toFixed(4) + '%' : escHtml(String(rate));
            var signalClass = (signal.signal || '') === 'extreme_negative' ? 'signal-negative' :
                (signal.signal || '') === 'extreme_positive' ? 'signal-positive' : 'signal-neutral';

            html += '<div class="funding-card">';
            html += '<div class="funding-pair">' + escHtml(pair) + '</div>';
            html += '<div class="funding-rate ' + signalClass + '">' + rateStr + '</div>';
            if (signal.signal) html += '<div class="funding-signal">' + escHtml(signal.signal) + '</div>';
            html += '</div>';
        });
        html += '</div>';

        // Long/Short Ratio + Top Trader + Taker Volume
        var lsRatio = data.long_short_ratio || {};
        var ttRatio = data.top_trader_ratio || {};
        var takerVol = data.taker_volume || {};
        var allSymbols = Object.keys(lsRatio);
        if (!allSymbols.length) allSymbols = Object.keys(ttRatio);
        if (!allSymbols.length) allSymbols = Object.keys(takerVol);

        if (allSymbols.length > 0) {
            html += '<div class="card-section-title" style="margin-top:20px">Positioning & Volume</div>';
            html += '<table class="data-table"><thead><tr>' +
                '<th>Pair</th><th>Crowd L/S</th><th>Top Trader L/S</th><th>Taker B/S</th>' +
                '</tr></thead><tbody>';
            allSymbols.forEach(function (sym) {
                var ls = lsRatio[sym] || {};
                var tt = ttRatio[sym] || {};
                var tv = takerVol[sym] || {};
                var lsVal = ls.long_short_ratio;
                var ttVal = tt.long_short_ratio;
                var tvVal = tv.buy_sell_ratio;

                var lsClass = lsVal > 1.5 ? 'pnl-positive' : lsVal < 0.7 ? 'pnl-negative' : '';
                var ttClass = ttVal > 1.3 ? 'pnl-positive' : ttVal < 0.7 ? 'pnl-negative' : '';
                var tvClass = tvVal > 1.2 ? 'pnl-positive' : tvVal < 0.8 ? 'pnl-negative' : '';

                html += '<tr>' +
                    '<td>' + escHtml(sym) + '</td>' +
                    '<td class="' + lsClass + '">' + (lsVal != null ? lsVal.toFixed(3) : '-') + '</td>' +
                    '<td class="' + ttClass + '">' + (ttVal != null ? ttVal.toFixed(3) : '-') + '</td>' +
                    '<td class="' + tvClass + '">' + (tvVal != null ? tvVal.toFixed(3) : '-') + '</td>' +
                    '</tr>';
            });
            html += '</tbody></table>';
        }

        // Regime per asset
        var regime = data.regime || {};
        var perAsset = regime.per_asset || {};
        if (Object.keys(perAsset).length > 0) {
            html += '<div class="card-section-title" style="margin-top:20px">Regime per Asset</div>';
            html += '<div class="regime-grid">';
            Object.keys(perAsset).forEach(function (asset) {
                var r = perAsset[asset];
                var regimeStr = typeof r === 'object' ? (r.regime || r.classification || 'unknown') : String(r);
                var regimeColor = regimeColorMap(regimeStr);
                html += '<div class="regime-card">';
                html += '<div class="regime-asset">' + escHtml(asset) + '</div>';
                html += '<div class="regime-badge" style="background:' + regimeColor + '">' + escHtml(regimeStr) + '</div>';
                html += '</div>';
            });
            html += '</div>';
        }

        // Web Intelligence (CoinGlass browser-scraped data)
        var webIntel = data.web_intel || {};
        if (webIntel && Object.keys(webIntel).length > 0 && webIntel.timestamp) {
            html += '<div class="card-section-title" style="margin-top:20px">Browser Intelligence (CoinGlass)</div>';

            // Web intel timestamp
            html += '<div class="atlas-timestamp" style="margin-bottom:10px">Scraped: ' + escHtml(webIntel.timestamp || 'N/A') + '</div>';

            // Signals summary
            var webSignals = webIntel.signals || {};

            // Funding divergence
            var fundDiv = webSignals.funding_divergence || {};
            var divKeys = Object.keys(fundDiv);
            if (divKeys.length > 0) {
                html += '<div class="atlas-card" style="margin-bottom:12px">';
                html += '<div class="card-section-title">Multi-Exchange Funding Divergence</div>';
                html += '<div class="signal-grid">';
                divKeys.forEach(function (sym) {
                    var info = fundDiv[sym] || {};
                    var isDivergent = info.divergent;
                    var stdVal = info.std || 0;
                    html += signalItem(
                        sym,
                        isDivergent,
                        isDivergent ? 'DIVERGENT (std=' + stdVal.toFixed(5) + ')' : 'Aligned'
                    );
                });
                html += '</div></div>';
            }

            // Liquidation imbalance
            var liqImb = webSignals.liq_imbalance || {};
            var liqKeys = Object.keys(liqImb);
            if (liqKeys.length > 0) {
                html += '<div class="atlas-card" style="margin-bottom:12px">';
                html += '<div class="card-section-title">Liquidation Imbalance</div>';
                html += '<div class="signal-grid">';
                liqKeys.forEach(function (sym) {
                    var info = liqImb[sym] || {};
                    var ratio = info.ratio;
                    var isExtreme = ratio != null && Math.abs(ratio) > 0.3;
                    var label = ratio != null ? (ratio > 0 ? 'LONG heavy' : 'SHORT heavy') + ' (' + ratio.toFixed(2) + ')' : 'Balanced';
                    html += signalItem(sym, isExtreme, label);
                });
                html += '</div></div>';
            }

            // OI concentration
            var oiConc = webSignals.oi_concentration || {};
            var oiKeys = Object.keys(oiConc);
            if (oiKeys.length > 0) {
                html += '<div class="atlas-card" style="margin-bottom:12px">';
                html += '<div class="card-section-title">OI Concentration by Exchange</div>';
                html += '<div class="signal-grid">';
                oiKeys.forEach(function (sym) {
                    var info = oiConc[sym] || {};
                    var concentrated = info.concentrated;
                    var topEx = info.top_exchange || 'N/A';
                    var topPct = info.top_pct || 0;
                    html += signalItem(
                        sym,
                        concentrated,
                        concentrated ? topEx + ' dominates (' + topPct.toFixed(0) + '%)' : 'Distributed'
                    );
                });
                html += '</div></div>';
            }

            // Raw funding data table
            var webFunding = webIntel.funding || {};
            var webFundKeys = Object.keys(webFunding);
            if (webFundKeys.length > 0) {
                html += '<div class="card-section-title" style="margin-top:12px">CoinGlass Funding Rates by Exchange</div>';
                html += '<table class="data-table"><thead><tr><th>Asset</th><th>Exchanges</th><th>Avg Rate</th></tr></thead><tbody>';
                webFundKeys.forEach(function (sym) {
                    var exchanges = webFunding[sym] || {};
                    var rates = Object.values(exchanges).filter(function(v) { return typeof v === 'number'; });
                    var avg = rates.length ? rates.reduce(function(a, b) { return a + b; }, 0) / rates.length : 0;
                    var exchStr = Object.keys(exchanges).join(', ');
                    var avgClass = avg > 0.0001 ? 'pnl-positive' : avg < -0.0001 ? 'pnl-negative' : '';
                    html += '<tr><td>' + escHtml(sym) + '</td>' +
                        '<td style="font-size:0.85em">' + escHtml(exchStr) + '</td>' +
                        '<td class="' + avgClass + '">' + (avg * 100).toFixed(4) + '%</td></tr>';
                });
                html += '</tbody></table>';
            }
        }

        if (data.updated_at) {
            html += '<div class="atlas-timestamp">Updated: ' + escHtml(data.updated_at) + '</div>';
        }

        el.innerHTML = html;

        // Draw fear & greed gauge
        setTimeout(function () {
            var gaugeCanvas = document.getElementById('market-gauge');
            if (gaugeCanvas) {
                RatCharts.drawGauge(gaugeCanvas, fg.value || 0, 100, { label: 'Fear & Greed' });
            }
        }, 10);
    }

    function signalItem(label, isActive, text) {
        var cls = isActive ? 'signal-active' : 'signal-normal';
        return '<div class="signal-item ' + cls + '">' +
            '<span class="signal-label">' + escHtml(label) + '</span>' +
            '<span class="signal-value">' + escHtml(text) + '</span></div>';
    }

    function regimeColorMap(regime) {
        var map = {
            strong_trend_up: C.green,
            strong_trend_down: C.red,
            volatile_breakout: C.orange,
            mean_reverting: C.blue,
            low_volatility: C.textMuted,
            choppy: C.gold,
            unknown: 'rgba(100,110,140,0.3)',
        };
        return map[regime] || map.unknown;
    }

    // =========================================================================
    // SYSTEM TAB
    // =========================================================================
    function renderSystem(data) {
        var el = document.getElementById('tab-system');
        var conductor = data.conductor || {};
        var agents = data.agents || [];
        var wisdom = data.wisdom || {};
        var html = '';

        // Conductor status
        html += '<div class="atlas-card system-conductor">';
        html += '<div class="card-section-title">Conductor</div>';
        html += '<div class="conductor-stats">';
        html += kernelStat('Cycle', '#' + (conductor.cycle_count || 0));
        html += kernelStat('Cycle ID', escHtml(conductor.cycle_id || '-'));
        html += kernelStat('Last Cycle', formatTimeAgo(conductor.last_cycle_at));
        html += '</div>';
        html += '</div>';

        // Agent health grid
        html += '<div class="card-section-title">Agent Health <span class="count">' + agents.length + '</span></div>';
        html += '<div class="agent-grid">';
        agents.forEach(function (agent) {
            var statusColor = agent.status === 'ok' ? C.green :
                agent.status === 'warning' ? C.gold : C.red;
            var statusIcon = agent.status === 'ok' ? '●' :
                agent.status === 'warning' ? '◐' : '✖';

            html += '<div class="agent-card">';
            html += '<div class="agent-header">';
            html += '<span class="agent-name">' + escHtml(agent.name) + '</span>';
            html += '<span class="agent-status" style="color:' + statusColor + '">' + statusIcon + '</span>';
            html += '</div>';
            html += '<div class="agent-meta">';
            html += '<span>Last: ' + formatTimeAgo(agent.last_run) + '</span>';
            if (agent.consecutive_failures > 0) {
                html += '<span class="agent-failures">' + agent.consecutive_failures + ' fails</span>';
            }
            html += '</div>';
            html += '</div>';
        });
        html += '</div>';

        // Wisdom rules
        var rules = wisdom.rules || [];
        if (rules.length > 0) {
            html += '<div class="card-section-title" style="margin-top:24px">Wisdom Rules ' +
                '<span class="count">' + (wisdom.rules_count || rules.length) + ' total, showing ' + rules.length + '</span></div>';
            html += '<table class="data-table"><thead><tr>' +
                '<th>ID</th><th>Rule</th><th>Confidence</th><th>Evidence</th>' +
                '</tr></thead><tbody>';
            rules.forEach(function (r) {
                var confPct = ((r.confidence || 0) * 100).toFixed(0);
                var confColor = r.confidence >= 0.7 ? C.green : r.confidence >= 0.4 ? C.gold : C.textMuted;
                html += '<tr>' +
                    '<td>' + escHtml(r.id || '-') + '</td>' +
                    '<td class="wisdom-text">' + escHtml(r.rule || r.text || '-') + '</td>' +
                    '<td><span class="wisdom-conf" style="color:' + confColor + '">' + confPct + '%</span></td>' +
                    '<td>' + (r.evidence_count || 0) + '</td>' +
                    '</tr>';
            });
            html += '</tbody></table>';
        }

        if (wisdom.updated_at) {
            html += '<div class="atlas-timestamp">Wisdom updated: ' + escHtml(wisdom.updated_at) + '</div>';
        }

        el.innerHTML = html;
    }

    // =========================================================================
    // Helpers
    // =========================================================================
    function escHtml(str) {
        if (str == null) return '';
        var div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    function formatPnlVal(val) {
        if (val == null) return '$0.00';
        var sign = val >= 0 ? '+' : '';
        return sign + '$' + val.toFixed(2);
    }

    function formatTimeAgo(isoStr) {
        if (!isoStr) return 'never';
        try {
            var dt = new Date(isoStr);
            var now = new Date();
            var diff = (now - dt) / 1000;
            if (diff < 60) return Math.floor(diff) + 's ago';
            if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
            if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
            return Math.floor(diff / 86400) + 'd ago';
        } catch (e) {
            return escHtml(isoStr);
        }
    }

    // --- Polling ---
    function startPolling() {
        if (_pollTimer) clearInterval(_pollTimer);
        _pollTimer = setInterval(function () {
            if (_activeTab !== 'trading') {
                fetchAndRender(_activeTab);
            }
        }, _POLL_INTERVAL);
    }

    // --- Init ---
    function init() {
        // Tab click handlers
        document.querySelectorAll('.nav-tab').forEach(function (tab) {
            tab.addEventListener('click', function (e) {
                e.preventDefault();
                switchTab(this.getAttribute('data-tab'));
            });
        });

        // Hash-based routing
        var hash = window.location.hash.replace('#', '');
        if (hash && document.getElementById('tab-' + hash)) {
            switchTab(hash);
        }

        window.addEventListener('hashchange', function () {
            var h = window.location.hash.replace('#', '');
            if (h && h !== _activeTab && document.getElementById('tab-' + h)) {
                switchTab(h);
            }
        });

        startPolling();
    }

    return {
        init: init,
        switchTab: switchTab,
    };
})();
