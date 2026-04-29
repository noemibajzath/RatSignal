/**
 * Vanilla canvas sparkline renderer.
 * drawSparkline(canvas, data, options)
 */
function drawSparkline(canvas, data, options) {
    if (!canvas || !data || data.length < 2) return;

    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    // HiDPI canvas
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const pad = 2;

    const opts = Object.assign({
        positiveColor: '#00e676',
        negativeColor: '#ff3366',
        lineWidth: 1.5,
        fillOpacity: 0.12,
    }, options || {});

    ctx.clearRect(0, 0, w, h);

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const final = data[data.length - 1];
    const color = final >= 0 ? opts.positiveColor : opts.negativeColor;

    // Build path
    const points = data.map(function (val, i) {
        return {
            x: pad + (i / (data.length - 1)) * (w - pad * 2),
            y: pad + (1 - (val - min) / range) * (h - pad * 2),
        };
    });

    // Draw line
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = opts.lineWidth;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';

    points.forEach(function (p, i) {
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();

    // Fill gradient below
    ctx.lineTo(points[points.length - 1].x, h);
    ctx.lineTo(points[0].x, h);
    ctx.closePath();

    var gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, color.replace(')', ', ' + opts.fillOpacity + ')').replace('rgb', 'rgba').replace('##', '#'));
    // Simpler approach: parse hex to rgba
    var r = parseInt(color.slice(1, 3), 16) || 0;
    var g = parseInt(color.slice(3, 5), 16) || 0;
    var b = parseInt(color.slice(5, 7), 16) || 0;
    gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, 'rgba(' + r + ',' + g + ',' + b + ',' + opts.fillOpacity + ')');
    gradient.addColorStop(1, 'rgba(' + r + ',' + g + ',' + b + ',0)');
    ctx.fillStyle = gradient;
    ctx.fill();

    // Zero line if data crosses zero
    if (min < 0 && max > 0) {
        var zeroY = pad + (1 - (0 - min) / range) * (h - pad * 2);
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(255,255,255,0.08)';
        ctx.lineWidth = 0.5;
        ctx.setLineDash([3, 3]);
        ctx.moveTo(pad, zeroY);
        ctx.lineTo(w - pad, zeroY);
        ctx.stroke();
        ctx.setLineDash([]);
    }
}

/**
 * Initialize all sparklines on the page.
 * Looks for canvas elements with data-sparkline attribute (JSON array).
 */
function initSparklines() {
    document.querySelectorAll('canvas[data-sparkline]').forEach(function (canvas) {
        try {
            var data = JSON.parse(canvas.getAttribute('data-sparkline'));
            if (data && data.length > 1) {
                drawSparkline(canvas, data);
            }
        } catch (e) {
            // skip invalid data
        }
    });
}
