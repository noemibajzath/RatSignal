/**
 * RatSignal — Scroll animations, counters, hamburger, smooth scroll, auto-refresh countdown, tabs.
 */
(function () {

    // --- 1. Intersection Observer for scroll reveals ---
    var revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                revealObserver.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.15,
        rootMargin: '0px 0px -40px 0px'
    });

    function initRevealAnimations() {
        var revealEls = document.querySelectorAll('.reveal, .reveal-left, .reveal-right');
        for (var i = 0; i < revealEls.length; i++) {
            revealEls[i].classList.add('reveal-ready');
            revealObserver.observe(revealEls[i]);
        }
    }

    // --- 2. Animated counters ---
    function animateCounter(el) {
        var target = parseFloat(el.getAttribute('data-target'));
        var suffix = el.getAttribute('data-suffix') || '';
        var prefix = el.getAttribute('data-prefix') || '';
        var decimals = el.getAttribute('data-decimals') ? parseInt(el.getAttribute('data-decimals')) : 0;
        var duration = 2000;
        var startTime = null;

        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            var eased = 1 - Math.pow(1 - progress, 3);
            var current = eased * target;

            el.textContent = prefix + current.toFixed(decimals) + suffix;

            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                el.textContent = prefix + target.toFixed(decimals) + suffix;
            }
        }

        requestAnimationFrame(step);
    }

    var counterObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
            if (entry.isIntersecting) {
                animateCounter(entry.target);
                counterObserver.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.5
    });

    function initCounters() {
        var counters = document.querySelectorAll('.counter-value[data-target]');
        for (var i = 0; i < counters.length; i++) {
            counterObserver.observe(counters[i]);
        }
    }

    // --- 3. Hamburger menu ---
    function initHamburger() {
        var hamburger = document.getElementById('hamburger-btn');
        var overlay = document.getElementById('mobile-nav-overlay');
        if (!hamburger || !overlay) return;

        hamburger.addEventListener('click', function () {
            hamburger.classList.toggle('active');
            overlay.classList.toggle('active');
            document.body.style.overflow = overlay.classList.contains('active') ? 'hidden' : '';
        });

        var links = overlay.querySelectorAll('a');
        for (var i = 0; i < links.length; i++) {
            links[i].addEventListener('click', function () {
                hamburger.classList.remove('active');
                overlay.classList.remove('active');
                document.body.style.overflow = '';
            });
        }
    }

    // --- 4. Smooth scroll for anchor links ---
    function initSmoothScroll() {
        var anchors = document.querySelectorAll('a[href^="#"]');
        for (var i = 0; i < anchors.length; i++) {
            anchors[i].addEventListener('click', function (e) {
                var href = this.getAttribute('href');
                if (href === '#') return;
                var target = document.querySelector(href);
                if (!target) return;
                e.preventDefault();
                var navHeight = 70;
                var top = target.getBoundingClientRect().top + window.pageYOffset - navHeight;
                window.scrollTo({ top: top, behavior: 'smooth' });
            });
        }
    }

    // --- 5. Auto-refresh countdown ---
    function initRefreshCountdown() {
        var countdownEl = document.getElementById('refresh-countdown');
        if (!countdownEl) return;

        var seconds = 30;
        var interval = setInterval(function () {
            seconds--;
            countdownEl.textContent = seconds + 's';

            if (seconds <= 0) {
                seconds = 30;
                countdownEl.textContent = '30s';
                fetchAndUpdate();
            }
        }, 1000);

        function fetchAndUpdate() {
            fetch('/api/accounts')
                .then(function (res) { return res.json(); })
                .then(function (data) {
                    var event = new CustomEvent('ratsignal:refresh', { detail: data });
                    document.dispatchEvent(event);
                })
                .catch(function () {
                    /* silent fail — next cycle will retry */
                });
        }
    }

    // --- 6. Nav tab system ---
    function initTabs() {
        var tabs = document.querySelectorAll('.nav-tab');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].addEventListener('click', function (e) {
                e.preventDefault();
                var tabName = this.getAttribute('data-tab');

                var allTabs = document.querySelectorAll('.nav-tab');
                for (var j = 0; j < allTabs.length; j++) {
                    allTabs[j].classList.remove('active');
                }
                var allContent = document.querySelectorAll('.tab-content');
                for (var k = 0; k < allContent.length; k++) {
                    allContent[k].classList.remove('active');
                }

                this.classList.add('active');
                var content = document.getElementById('tab-' + tabName);
                if (content) content.classList.add('active');
            });
        }
    }

    // --- Init ---
    document.addEventListener('DOMContentLoaded', function () {
        initRevealAnimations();
        initCounters();
        initHamburger();
        initSmoothScroll();
        initRefreshCountdown();
        initTabs();
    });

})();
