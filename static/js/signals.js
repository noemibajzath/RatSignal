/**
 * RatSignal Signals — signal card carousel with glassmorphism styling.
 * Auto-cycling, responsive, slide-in animations.
 */
var RatSignals = (function () {

    var _carouselTimer = null;
    var _currentOffset = 0;
    var _signals = [];
    var _containerId = '';

    function renderSignalCards(containerId, signals) {
        _containerId = containerId;
        _signals = signals || [];
        var container = document.getElementById(containerId);
        if (!container || !_signals.length) {
            if (container) container.innerHTML = '<div class="signal-empty">No recent signals</div>';
            return;
        }

        _currentOffset = 0;

        // Build carousel wrapper
        var html = '<div class="signal-carousel-wrapper">';
        html += '<button class="signal-nav signal-nav-left" onclick="RatSignals.prev()">&lsaquo;</button>';
        html += '<div class="signal-carousel-viewport">';
        html += '<div class="signal-carousel-track" id="signal-track">';

        _signals.forEach(function (sig, idx) {
            html += _buildCard(sig, idx);
        });

        html += '</div></div>';
        html += '<button class="signal-nav signal-nav-right" onclick="RatSignals.next()">&rsaquo;</button>';
        html += '</div>';

        container.innerHTML = html;

        // Start auto-cycle
        _startAutoCycle();

        // Intersection observer for slide-in animation
        _observeCards();
    }

    function _buildCard(sig, idx) {
        var isWin = sig.result === 'win';
        var isLong = sig.direction === 'long';
        var glowClass = isWin ? 'signal-glow-green' : 'signal-glow-red';
        var dirClass = isLong ? 'signal-dir-long' : 'signal-dir-short';

        var html = '<div class="signal-card ' + glowClass + '" data-signal-idx="' + idx + '">';

        // Header: pair + direction badge
        html += '<div class="signal-card-header">';
        html += '<span class="signal-pair">' + sig.pair + '</span>';
        html += '<span class="signal-badge ' + dirClass + '">' + sig.direction.toUpperCase() + '</span>';
        html += '</div>';

        // PnL
        var pnlClass = isWin ? 'signal-pnl-win' : 'signal-pnl-loss';
        html += '<div class="signal-pnl ' + pnlClass + '">';
        html += (sig.pnl >= 0 ? '+' : '') + '$' + parseFloat(sig.pnl.toFixed(2));
        if (sig.pnl_pct) {
            html += ' <span class="signal-pnl-pct">(' + (sig.pnl_pct >= 0 ? '+' : '') + sig.pnl_pct.toFixed(2) + '%)</span>';
        }
        html += '</div>';

        // Entry/Exit
        html += '<div class="signal-prices">';
        html += '<div class="signal-price-row"><span class="signal-label">Entry</span><span class="signal-value">' + _formatPrice(sig.entry_price) + '</span></div>';
        html += '<div class="signal-price-row"><span class="signal-label">Exit</span><span class="signal-value">' + _formatPrice(sig.exit_price) + '</span></div>';
        html += '</div>';

        // Risk stars
        html += '<div class="signal-risk">';
        for (var i = 0; i < 5; i++) {
            html += '<span class="signal-star' + (i < sig.risk_score ? ' signal-star-filled' : '') + '">&#9733;</span>';
        }
        html += '</div>';

        // Footer: timestamp + bars
        var timeStr = (sig.timestamp || '').replace('T', ' ').slice(0, 16);
        html += '<div class="signal-footer">';
        html += '<span class="signal-time">' + timeStr + '</span>';
        if (sig.bars_held) {
            html += '<span class="signal-bars">' + sig.bars_held + ' bars</span>';
        }
        html += '</div>';

        html += '</div>';
        return html;
    }

    function _formatPrice(val) {
        if (!val) return '-';
        if (val > 1000) return val.toFixed(0);
        if (val > 1) return parseFloat(val.toFixed(2)).toString();
        return parseFloat(val.toFixed(4)).toString();
    }

    function _getVisibleCount() {
        var w = window.innerWidth;
        if (w < 640) return 1;
        if (w < 960) return 2;
        return 3;
    }

    function _maxOffset() {
        var visible = _getVisibleCount();
        return Math.max(0, _signals.length - visible);
    }

    function _slideToOffset() {
        var track = document.getElementById('signal-track');
        if (!track) return;
        var visible = _getVisibleCount();
        // Each card is 100/visible percent width
        var pct = (_currentOffset / visible) * 100;
        track.style.transform = 'translateX(-' + pct + '%)';
    }

    function next() {
        _currentOffset++;
        if (_currentOffset > _maxOffset()) _currentOffset = 0;
        _slideToOffset();
        _restartAutoCycle();
    }

    function prev() {
        _currentOffset--;
        if (_currentOffset < 0) _currentOffset = _maxOffset();
        _slideToOffset();
        _restartAutoCycle();
    }

    function _startAutoCycle() {
        if (_carouselTimer) clearInterval(_carouselTimer);
        _carouselTimer = setInterval(function () {
            next();
        }, 5000);
    }

    function _restartAutoCycle() {
        _startAutoCycle();
    }

    function _observeCards() {
        if (!('IntersectionObserver' in window)) return;
        var cards = document.querySelectorAll('.signal-card');
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('signal-card-visible');
                    observer.unobserve(entry.target);
                }
            });
        }, { threshold: 0.1 });
        cards.forEach(function (card) { observer.observe(card); });
    }

    // Fetch and render
    function fetchAndRender(containerId, limit) {
        limit = limit || 10;
        fetch('/api/signals/recent?limit=' + limit)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (Array.isArray(data)) {
                    renderSignalCards(containerId, data);
                }
            })
            .catch(function (err) {
                console.warn('Signal fetch failed:', err);
            });
    }

    return {
        renderSignalCards: renderSignalCards,
        fetchAndRender: fetchAndRender,
        next: next,
        prev: prev,
    };
})();
