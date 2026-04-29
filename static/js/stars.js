/**
 * Canvas star particle background — lightweight space effect.
 * 200 stars, slow upward drift, subtle twinkle.
 */
(function () {
    const canvas = document.getElementById('stars-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let stars = [];
    const COUNT = 180;
    let w, h;
    let animId;
    let visible = true;

    function resize() {
        w = canvas.width = window.innerWidth;
        h = canvas.height = window.innerHeight;
    }

    function init() {
        resize();
        stars = [];
        for (let i = 0; i < COUNT; i++) {
            stars.push({
                x: Math.random() * w,
                y: Math.random() * h,
                r: Math.random() * 1.4 + 0.4,
                alpha: Math.random() * 0.6 + 0.2,
                speed: Math.random() * 0.15 + 0.03,
                twinkleSpeed: Math.random() * 0.008 + 0.002,
                twinklePhase: Math.random() * Math.PI * 2,
            });
        }
    }

    function draw(time) {
        if (!visible) { animId = requestAnimationFrame(draw); return; }

        ctx.clearRect(0, 0, w, h);

        for (const s of stars) {
            // Drift upward
            s.y -= s.speed;
            if (s.y < -2) { s.y = h + 2; s.x = Math.random() * w; }

            // Twinkle
            const twinkle = Math.sin(time * s.twinkleSpeed + s.twinklePhase);
            const alpha = s.alpha + twinkle * 0.15;

            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(200, 220, 255, ${Math.max(0.05, alpha)})`;
            ctx.fill();
        }

        animId = requestAnimationFrame(draw);
    }

    // Visibility API — pause when tab hidden
    document.addEventListener('visibilitychange', function () {
        visible = !document.hidden;
    });

    window.addEventListener('resize', resize);
    init();
    animId = requestAnimationFrame(draw);
})();
