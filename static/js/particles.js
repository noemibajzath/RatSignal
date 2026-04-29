/**
 * RatSignal — Premium canvas particle system.
 * Tiny drifting dots with mouse repulsion and faint connection lines.
 */
(function () {
    var canvas = document.getElementById('stars-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');

    var PARTICLE_COUNT = 80;
    var CONNECT_DIST = 120;
    var REPEL_DIST = 100;
    var REPEL_FORCE = 0.5;

    var particles = [];
    var w, h;
    var mouseX = -1000;
    var mouseY = -1000;
    var visible = true;
    var animId;

    // Color palette
    var COLORS = [
        { r: 255, g: 255, b: 255, a: 0.3, weight: 0.7 },   // white
        { r: 0,   g: 212, b: 255, a: 0.4, weight: 0.2 },   // cyan
        { r: 245, g: 166, b: 35,  a: 0.3, weight: 0.1 }    // gold
    ];

    function pickColor() {
        var roll = Math.random();
        var cumulative = 0;
        for (var i = 0; i < COLORS.length; i++) {
            cumulative += COLORS[i].weight;
            if (roll <= cumulative) return COLORS[i];
        }
        return COLORS[0];
    }

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
    }

    function createParticle() {
        var color = pickColor();
        var angle = Math.random() * Math.PI * 2;
        var speed = Math.random() * 0.2 + 0.1;
        return {
            x: Math.random() * w,
            y: Math.random() * h,
            r: Math.random() * 1.0 + 1.0,
            color: color,
            dx: Math.cos(angle) * speed,
            dy: Math.sin(angle) * speed,
            vx: 0,
            vy: 0
        };
    }

    function init() {
        resize();
        particles = [];
        for (var i = 0; i < PARTICLE_COUNT; i++) {
            particles.push(createParticle());
        }
    }

    function draw() {
        if (!visible) {
            animId = requestAnimationFrame(draw);
            return;
        }

        ctx.clearRect(0, 0, w, h);

        // Update and draw particles
        for (var i = 0; i < particles.length; i++) {
            var p = particles[i];

            // Drift
            p.x += p.dx + p.vx;
            p.y += p.dy + p.vy;

            // Dampen velocity from repulsion
            p.vx *= 0.95;
            p.vy *= 0.95;

            // Wrap around edges
            if (p.x < -10) p.x = w + 10;
            if (p.x > w + 10) p.x = -10;
            if (p.y < -10) p.y = h + 10;
            if (p.y > h + 10) p.y = -10;

            // Mouse repulsion
            var mdx = p.x - mouseX;
            var mdy = p.y - mouseY;
            var mdist = Math.sqrt(mdx * mdx + mdy * mdy);
            if (mdist < REPEL_DIST && mdist > 0) {
                var force = (1 - mdist / REPEL_DIST) * REPEL_FORCE;
                p.vx += (mdx / mdist) * force;
                p.vy += (mdy / mdist) * force;
            }

            // Skip off-screen particles
            if (p.x < -20 || p.x > w + 20 || p.y < -20 || p.y > h + 20) continue;

            // Draw dot
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(' + p.color.r + ',' + p.color.g + ',' + p.color.b + ',' + p.color.a + ')';
            ctx.fill();
        }

        // Connection lines between nearby particles
        for (var i = 0; i < particles.length; i++) {
            for (var j = i + 1; j < particles.length; j++) {
                var a = particles[i];
                var b = particles[j];
                var dx = a.x - b.x;
                var dy = a.y - b.y;
                var dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < CONNECT_DIST) {
                    var opacity = 0.05 * (1 - dist / CONNECT_DIST);
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.strokeStyle = 'rgba(255,255,255,' + opacity + ')';
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }

        animId = requestAnimationFrame(draw);
    }

    // Mouse tracking (throttled)
    var mouseThrottle = false;
    document.addEventListener('mousemove', function (e) {
        if (mouseThrottle) return;
        mouseThrottle = true;
        mouseX = e.clientX;
        mouseY = e.clientY;
        setTimeout(function () { mouseThrottle = false; }, 16);
    });

    document.addEventListener('mouseleave', function () {
        mouseX = -1000;
        mouseY = -1000;
    });

    // Visibility API — pause when tab hidden
    document.addEventListener('visibilitychange', function () {
        visible = !document.hidden;
    });

    window.addEventListener('resize', resize);
    init();
    animId = requestAnimationFrame(draw);
})();
