/**
 * RatSignal — Dot Grid Canvas with Cursor Reveal Effect
 * Inspired by 21st.dev Hero Highlight component.
 * Draws a subtle dot grid; dots near cursor glow in brand colors.
 */
(function () {
    'use strict';

    var DOT_SPACING = 20;
    var DOT_RADIUS = 1;
    var REVEAL_RADIUS = 180;
    var BASE_COLOR = 'rgba(255,255,255,0.05)';
    var BRAND_COLORS = [
        { r: 230, g: 57, b: 70 },   // rat-red
        { r: 0, g: 212, b: 255 },    // lightning cyan
        { r: 255, g: 213, b: 79 },   // gold (rare)
    ];

    function initDotGrid(canvasId) {
        var canvas = document.getElementById(canvasId);
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var dpr = window.devicePixelRatio || 1;
        var mx = -9999, my = -9999;
        var raf;
        var cols, rows;

        // Pre-compute a seeded color for each dot
        var dotColors = [];

        function resize() {
            var rect = canvas.parentElement.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            canvas.style.width = rect.width + 'px';
            canvas.style.height = rect.height + 'px';
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            cols = Math.ceil(rect.width / DOT_SPACING) + 1;
            rows = Math.ceil(rect.height / DOT_SPACING) + 1;

            // Assign colors
            dotColors = [];
            for (var i = 0; i < cols * rows; i++) {
                // Mostly red and cyan, occasionally gold
                var rand = pseudoRandom(i);
                var ci = rand < 0.45 ? 0 : rand < 0.9 ? 1 : 2;
                dotColors.push(BRAND_COLORS[ci]);
            }
        }

        function pseudoRandom(seed) {
            var x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
            return x - Math.floor(x);
        }

        function draw() {
            var w = canvas.width / dpr;
            var h = canvas.height / dpr;
            ctx.clearRect(0, 0, w, h);

            for (var row = 0; row < rows; row++) {
                for (var col = 0; col < cols; col++) {
                    var x = col * DOT_SPACING;
                    var y = row * DOT_SPACING;
                    var dx = x - mx;
                    var dy = y - my;
                    var dist = Math.sqrt(dx * dx + dy * dy);

                    ctx.beginPath();
                    ctx.arc(x, y, DOT_RADIUS, 0, Math.PI * 2);

                    if (dist < REVEAL_RADIUS) {
                        var t = 1 - dist / REVEAL_RADIUS;
                        t = t * t; // quadratic falloff
                        var idx = row * cols + col;
                        var c = dotColors[idx] || BRAND_COLORS[0];
                        var alpha = 0.05 + t * 0.55;
                        var radius = DOT_RADIUS + t * 1.2;

                        ctx.arc(x, y, radius, 0, Math.PI * 2);
                        ctx.fillStyle = 'rgba(' + c.r + ',' + c.g + ',' + c.b + ',' + alpha + ')';
                    } else {
                        ctx.fillStyle = BASE_COLOR;
                    }
                    ctx.fill();
                }
            }

            raf = requestAnimationFrame(draw);
        }

        function onMouseMove(e) {
            var rect = canvas.getBoundingClientRect();
            mx = e.clientX - rect.left;
            my = e.clientY - rect.top;
        }

        function onMouseLeave() {
            mx = -9999;
            my = -9999;
        }

        // Disable on touch devices
        if ('ontouchstart' in window) {
            canvas.style.display = 'none';
            return;
        }

        resize();
        draw();

        canvas.parentElement.addEventListener('mousemove', onMouseMove);
        canvas.parentElement.addEventListener('mouseleave', onMouseLeave);
        window.addEventListener('resize', function () {
            resize();
        });
    }

    // Expose globally
    window.initDotGrid = initDotGrid;

    // Auto-init on DOM ready
    function autoInit() {
        var canvases = document.querySelectorAll('canvas[data-dotgrid]');
        canvases.forEach(function (c) {
            initDotGrid(c.id);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInit);
    } else {
        autoInit();
    }
})();
