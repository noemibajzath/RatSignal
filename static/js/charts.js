/**
 * RatSignal Charts — Canvas primitives for ATLAS dashboard.
 * Donut chart, semi-circle gauge, horizontal bar.
 */

var RatCharts = (function () {

    // --- Donut Chart ---
    function drawDonut(canvas, segments, options) {
        if (!canvas || !segments || !segments.length) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        var w = rect.width;
        var h = rect.height;
        var opts = Object.assign({
            lineWidth: 18,
            gap: 0.03,
            centerLabel: '',
            centerSub: '',
        }, options || {});

        var cx = w / 2;
        var cy = h / 2;
        var radius = Math.min(cx, cy) - opts.lineWidth / 2 - 4;
        var total = segments.reduce(function (s, seg) { return s + seg.value; }, 0);
        if (total <= 0) return;

        ctx.clearRect(0, 0, w, h);

        // Background ring
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(255,255,255,0.04)';
        ctx.lineWidth = opts.lineWidth;
        ctx.stroke();

        // Segments
        var startAngle = -Math.PI / 2;
        segments.forEach(function (seg) {
            var sweep = (seg.value / total) * Math.PI * 2 - opts.gap;
            if (sweep <= 0) return;

            ctx.beginPath();
            ctx.arc(cx, cy, radius, startAngle, startAngle + sweep);
            ctx.strokeStyle = seg.color;
            ctx.lineWidth = opts.lineWidth;
            ctx.lineCap = 'round';
            ctx.stroke();

            startAngle += sweep + opts.gap;
        });

        // Center text
        if (opts.centerLabel) {
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = '#e8eaf6';
            ctx.font = '700 1.1rem Inter, sans-serif';
            ctx.fillText(opts.centerLabel, cx, cy - (opts.centerSub ? 8 : 0));
        }
        if (opts.centerSub) {
            ctx.fillStyle = 'rgba(160,170,200,0.7)';
            ctx.font = '400 0.65rem Inter, sans-serif';
            ctx.fillText(opts.centerSub, cx, cy + 14);
        }
    }

    // --- Semi-circle Gauge ---
    function drawGauge(canvas, value, maxValue, options) {
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        var w = rect.width;
        var h = rect.height;
        var opts = Object.assign({
            lineWidth: 14,
            label: '',
            colorStops: [
                { at: 0, color: '#ff3366' },
                { at: 0.25, color: '#ff8c00' },
                { at: 0.5, color: '#f5a623' },
                { at: 0.75, color: '#00e676' },
            ],
        }, options || {});

        var cx = w / 2;
        var cy = h - 10;
        var radius = Math.min(cx, cy) - opts.lineWidth / 2 - 4;
        var pct = Math.max(0, Math.min(1, value / maxValue));

        ctx.clearRect(0, 0, w, h);

        // Background arc (180 degrees)
        ctx.beginPath();
        ctx.arc(cx, cy, radius, Math.PI, 0);
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = opts.lineWidth;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Gradient arc
        var gradient = ctx.createLinearGradient(cx - radius, cy, cx + radius, cy);
        opts.colorStops.forEach(function (stop) {
            gradient.addColorStop(stop.at, stop.color);
        });

        ctx.beginPath();
        ctx.arc(cx, cy, radius, Math.PI, Math.PI + (Math.PI * pct));
        ctx.strokeStyle = gradient;
        ctx.lineWidth = opts.lineWidth;
        ctx.lineCap = 'round';
        ctx.stroke();

        // Needle dot
        var needleAngle = Math.PI + Math.PI * pct;
        var nx = cx + Math.cos(needleAngle) * radius;
        var ny = cy + Math.sin(needleAngle) * radius;
        ctx.beginPath();
        ctx.arc(nx, ny, opts.lineWidth / 2 + 2, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(nx, ny, opts.lineWidth / 2, 0, Math.PI * 2);
        ctx.fillStyle = '#070b14';
        ctx.fill();

        // Value text
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#e8eaf6';
        ctx.font = '800 1.6rem JetBrains Mono, monospace';
        ctx.fillText(Math.round(value).toString(), cx, cy - radius * 0.35);

        if (opts.label) {
            ctx.fillStyle = 'rgba(160,170,200,0.7)';
            ctx.font = '400 0.65rem Inter, sans-serif';
            ctx.fillText(opts.label, cx, cy - radius * 0.35 + 20);
        }
    }

    // --- Horizontal Progress Bar ---
    function drawProgressBar(canvas, value, maxValue, options) {
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        var w = rect.width;
        var h = rect.height;
        var opts = Object.assign({
            color: '#00d4ff',
            bgColor: 'rgba(255,255,255,0.06)',
            radius: 4,
        }, options || {});

        var pct = Math.max(0, Math.min(1, value / maxValue));

        ctx.clearRect(0, 0, w, h);

        // Background
        roundRect(ctx, 0, 0, w, h, opts.radius);
        ctx.fillStyle = opts.bgColor;
        ctx.fill();

        // Fill
        if (pct > 0) {
            var fillW = Math.max(opts.radius * 2, w * pct);
            roundRect(ctx, 0, 0, fillW, h, opts.radius);
            ctx.fillStyle = opts.color;
            ctx.fill();
        }
    }

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.quadraticCurveTo(x + w, y, x + w, y + r);
        ctx.lineTo(x + w, y + h - r);
        ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
        ctx.lineTo(x + r, y + h);
        ctx.quadraticCurveTo(x, y + h, x, y + h - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    // --- Equity Curve ---
    function renderEquityCurve(containerId, data) {
        var container = document.getElementById(containerId);
        if (!container || !data || data.length < 2) return;

        var canvas = document.createElement('canvas');
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        container.innerHTML = '';
        container.appendChild(canvas);

        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var rect = container.getBoundingClientRect();
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;
        ctx.scale(dpr, dpr);

        var w = rect.width;
        var h = rect.height;
        var padL = 60, padR = 16, padT = 16, padB = 28;
        var chartW = w - padL - padR;
        var chartH = h - padT - padB;

        var values = data.map(function (d) { return d.equity; });
        var minV = Math.min.apply(null, values);
        var maxV = Math.max.apply(null, values);
        var range = maxV - minV || 1;

        function xPos(i) { return padL + (i / (data.length - 1)) * chartW; }
        function yPos(v) { return padT + (1 - (v - minV) / range) * chartH; }

        // Grid lines
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        var gridSteps = 4;
        for (var g = 0; g <= gridSteps; g++) {
            var gy = padT + (g / gridSteps) * chartH;
            ctx.beginPath();
            ctx.moveTo(padL, gy);
            ctx.lineTo(w - padR, gy);
            ctx.stroke();
            // Y-axis label
            var gval = maxV - (g / gridSteps) * range;
            ctx.fillStyle = 'rgba(160,170,200,0.5)';
            ctx.font = '10px JetBrains Mono, monospace';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            ctx.fillText('$' + gval.toFixed(0), padL - 6, gy);
        }

        // Gradient fill below line
        var gradient = ctx.createLinearGradient(0, padT, 0, padT + chartH);
        gradient.addColorStop(0, 'rgba(0, 229, 195, 0.25)');
        gradient.addColorStop(1, 'rgba(0, 229, 195, 0.0)');

        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(values[0]));
        for (var i = 1; i < values.length; i++) {
            ctx.lineTo(xPos(i), yPos(values[i]));
        }
        ctx.lineTo(xPos(values.length - 1), padT + chartH);
        ctx.lineTo(xPos(0), padT + chartH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Line
        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(values[0]));
        for (var j = 1; j < values.length; j++) {
            ctx.lineTo(xPos(j), yPos(values[j]));
        }
        ctx.strokeStyle = '#00e5c3';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Tooltip on hover
        var _tooltipActive = false;
        canvas.addEventListener('mousemove', function (e) {
            var cr = canvas.getBoundingClientRect();
            var mx = e.clientX - cr.left;
            var my = e.clientY - cr.top;

            if (mx < padL || mx > w - padR || my < padT || my > padT + chartH) {
                if (_tooltipActive) {
                    _tooltipActive = false;
                    _redrawEquityCurve(ctx, data, values, w, h, padL, padR, padT, padB, chartW, chartH, minV, maxV, range, gradient);
                }
                return;
            }

            var idx = Math.round(((mx - padL) / chartW) * (data.length - 1));
            idx = Math.max(0, Math.min(data.length - 1, idx));

            _redrawEquityCurve(ctx, data, values, w, h, padL, padR, padT, padB, chartW, chartH, minV, maxV, range, gradient);

            // Crosshair
            var px = xPos(idx);
            var py = yPos(values[idx]);
            ctx.beginPath();
            ctx.arc(px, py, 4, 0, Math.PI * 2);
            ctx.fillStyle = '#00e5c3';
            ctx.fill();

            // Tooltip box
            var tText = data[idx].timestamp ? data[idx].timestamp.slice(0, 10) : '';
            var vText = '$' + values[idx].toFixed(2);
            ctx.font = '11px JetBrains Mono, monospace';
            var tw = Math.max(ctx.measureText(tText).width, ctx.measureText(vText).width) + 16;
            var tx = Math.min(px - tw / 2, w - padR - tw);
            tx = Math.max(tx, padL);
            var ty = py - 44;
            if (ty < padT) ty = py + 10;

            ctx.fillStyle = 'rgba(7, 11, 20, 0.9)';
            _roundRectPath(ctx, tx, ty, tw, 36, 4);
            ctx.fill();
            ctx.strokeStyle = 'rgba(0,229,195,0.3)';
            ctx.lineWidth = 1;
            ctx.stroke();

            ctx.fillStyle = 'rgba(160,170,200,0.8)';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText(tText, tx + tw / 2, ty + 4);
            ctx.fillStyle = '#00e5c3';
            ctx.fillText(vText, tx + tw / 2, ty + 18);

            _tooltipActive = true;
        });

        canvas.addEventListener('mouseleave', function () {
            if (_tooltipActive) {
                _tooltipActive = false;
                _redrawEquityCurve(ctx, data, values, w, h, padL, padR, padT, padB, chartW, chartH, minV, maxV, range, gradient);
            }
        });
    }

    function _redrawEquityCurve(ctx, data, values, w, h, padL, padR, padT, padB, chartW, chartH, minV, maxV, range, gradient) {
        function xPos(i) { return padL + (i / (data.length - 1)) * chartW; }
        function yPos(v) { return padT + (1 - (v - minV) / range) * chartH; }

        ctx.clearRect(0, 0, w * 2, h * 2);

        // Grid
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        var gridSteps = 4;
        for (var g = 0; g <= gridSteps; g++) {
            var gy = padT + (g / gridSteps) * chartH;
            ctx.beginPath();
            ctx.moveTo(padL, gy);
            ctx.lineTo(w - padR, gy);
            ctx.stroke();
            var gval = maxV - (g / gridSteps) * range;
            ctx.fillStyle = 'rgba(160,170,200,0.5)';
            ctx.font = '10px JetBrains Mono, monospace';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            ctx.fillText('$' + gval.toFixed(0), padL - 6, gy);
        }

        // Fill
        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(values[0]));
        for (var i = 1; i < values.length; i++) { ctx.lineTo(xPos(i), yPos(values[i])); }
        ctx.lineTo(xPos(values.length - 1), padT + chartH);
        ctx.lineTo(xPos(0), padT + chartH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Line
        ctx.beginPath();
        ctx.moveTo(xPos(0), yPos(values[0]));
        for (var j = 1; j < values.length; j++) { ctx.lineTo(xPos(j), yPos(values[j])); }
        ctx.strokeStyle = '#00e5c3';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    function _roundRectPath(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.quadraticCurveTo(x + w, y, x + w, y + r);
        ctx.lineTo(x + w, y + h - r);
        ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
        ctx.lineTo(x + r, y + h);
        ctx.quadraticCurveTo(x, y + h, x, y + h - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
    }

    // --- Win Rate Donut ---
    function renderWinRateDonut(containerId, wins, losses) {
        var container = document.getElementById(containerId);
        if (!container) return;

        var canvas = document.createElement('canvas');
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        container.innerHTML = '';
        container.appendChild(canvas);

        var total = wins + losses;
        var wrPct = total > 0 ? ((wins / total) * 100).toFixed(1) : '0.0';

        var segments = [];
        if (wins > 0) segments.push({ value: wins, color: '#00e676' });
        if (losses > 0) segments.push({ value: losses, color: '#ff3366' });

        if (segments.length === 0) {
            segments.push({ value: 1, color: 'rgba(255,255,255,0.06)' });
        }

        drawDonut(canvas, segments, {
            lineWidth: 14,
            gap: 0.04,
            centerLabel: wrPct + '%',
            centerSub: 'Win Rate',
        });
    }

    return {
        drawDonut: drawDonut,
        drawGauge: drawGauge,
        drawProgressBar: drawProgressBar,
        renderEquityCurve: renderEquityCurve,
        renderWinRateDonut: renderWinRateDonut,
    };
})();
