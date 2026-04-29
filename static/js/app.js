/**
 * RatSignal V4 — Main Application JS
 * GSAP ScrollTrigger orchestration, auto-refresh, smooth transitions.
 */
(function() {
    'use strict';

    // ===== 1. GSAP Registration =====
    if (typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined') {
        gsap.registerPlugin(ScrollTrigger);
    }

    // ===== 2. Scroll Animations (GSAP) =====
    function initScrollAnimations() {
        if (typeof gsap === 'undefined') return;

        // Generic reveal animations
        gsap.utils.toArray('.reveal').forEach(function(el) {
            gsap.from(el, {
                scrollTrigger: { trigger: el, start: 'top 88%', once: true },
                y: 40,
                opacity: 0,
                duration: 0.8,
                ease: 'power3.out'
            });
        });

        // Staggered card reveals
        gsap.utils.toArray('.stagger-grid').forEach(function(grid) {
            var cards = grid.children;
            gsap.from(cards, {
                scrollTrigger: { trigger: grid, start: 'top 82%', once: true },
                y: 30,
                opacity: 0,
                stagger: 0.08,
                duration: 0.6,
                ease: 'power2.out'
            });
        });

        // Section headers
        gsap.utils.toArray('.section-header').forEach(function(header) {
            gsap.from(header.children, {
                scrollTrigger: { trigger: header, start: 'top 85%', once: true },
                y: 25,
                opacity: 0,
                stagger: 0.1,
                duration: 0.7,
                ease: 'power3.out'
            });
        });

        // Counter animations
        gsap.utils.toArray('[data-count-to]').forEach(function(el) {
            var target = parseFloat(el.dataset.countTo);
            var suffix = el.dataset.countSuffix || '';
            var decimals = parseInt(el.dataset.countDecimals || '0');

            ScrollTrigger.create({
                trigger: el,
                start: 'top 90%',
                once: true,
                onEnter: function() {
                    animateCounter(el, 0, target, 1500, decimals, suffix);
                }
            });
        });
    }

    // ===== 3. Counter Animation =====
    function animateCounter(el, from, to, duration, decimals, suffix) {
        var startTime = null;
        var diff = to - from;

        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            // Ease out cubic
            var eased = 1 - Math.pow(1 - progress, 3);
            var current = from + diff * eased;
            el.textContent = current.toFixed(decimals) + suffix;
            if (progress < 1) {
                requestAnimationFrame(step);
            }
        }
        requestAnimationFrame(step);
    }

    // ===== 4. Smooth PnL Value Updates =====
    function animateValue(element, oldVal, newVal, duration) {
        duration = duration || 800;
        var prefix = newVal >= 0 ? '+$' : '-$';
        var absNew = Math.abs(newVal);
        var absOld = Math.abs(oldVal);

        var startTime = null;
        var diff = absNew - absOld;

        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            var eased = 1 - Math.pow(1 - progress, 3);
            var current = absOld + diff * eased;
            element.textContent = prefix + current.toFixed(2);
            if (progress < 1) {
                requestAnimationFrame(step);
            }
        }

        if (Math.abs(diff) > 0.001) {
            requestAnimationFrame(step);
        }
    }

    // ===== 5. Hero Load Animation =====
    function initHeroAnimation() {
        var hero = document.querySelector('.hero-variant');
        if (!hero || typeof gsap === 'undefined') return;

        var tl = gsap.timeline({ delay: 0.2 });

        var pill = hero.querySelector('.pill-badge');
        var headline = hero.querySelector('h1');
        var subtitle = hero.querySelector('.hero-subtitle, .hero-sub');
        var ctas = hero.querySelector('.hero-cta, .hero-ctas');

        if (pill) tl.from(pill, { y: 20, opacity: 0, duration: 0.5, ease: 'power3.out' });
        if (headline) tl.from(headline, { y: 30, opacity: 0, duration: 0.7, ease: 'power3.out' }, '-=0.2');
        if (subtitle) tl.from(subtitle, { y: 20, opacity: 0, duration: 0.5, ease: 'power3.out' }, '-=0.3');
        if (ctas) tl.from(ctas, { y: 20, opacity: 0, duration: 0.5, ease: 'power3.out' }, '-=0.2');
    }

    // ===== 6. Navigation =====
    function initNavigation() {
        // Hamburger toggle
        var hamburger = document.getElementById('hamburger-btn');
        var mobileNav = document.getElementById('mobile-nav-overlay');
        if (hamburger && mobileNav) {
            hamburger.addEventListener('click', function() {
                mobileNav.classList.toggle('active');
                hamburger.classList.toggle('active');
            });
            // Close on link click
            mobileNav.querySelectorAll('a').forEach(function(link) {
                link.addEventListener('click', function() {
                    mobileNav.classList.remove('active');
                    hamburger.classList.remove('active');
                });
            });
        }

        // Smooth scroll for anchor links
        document.querySelectorAll('a[href^="#"]').forEach(function(link) {
            link.addEventListener('click', function(e) {
                var target = document.querySelector(this.getAttribute('href'));
                if (target) {
                    e.preventDefault();
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        });

        // Nav scroll opacity
        var nav = document.querySelector('.nav, .showcase-nav');
        if (nav) {
            window.addEventListener('scroll', function() {
                if (window.scrollY > 80) {
                    nav.classList.add('nav-scrolled');
                } else {
                    nav.classList.remove('nav-scrolled');
                }
            });
        }
    }

    // ===== 7. Showcase Navigation =====
    function initShowcaseNav() {
        var navLinks = document.querySelectorAll('.showcase-nav-link');
        navLinks.forEach(function(link) {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                var targetId = this.getAttribute('href').substring(1);
                var targetSection = document.getElementById(targetId);
                if (targetSection) {
                    var offset = 60; // nav height
                    var y = targetSection.getBoundingClientRect().top + window.pageYOffset - offset;
                    window.scrollTo({ top: y, behavior: 'smooth' });
                }
            });
        });
    }

    // ===== 8. Init =====
    function init() {
        initScrollAnimations();
        initHeroAnimation();
        initNavigation();
        initShowcaseNav();

        // Init spotlight card tracking
        if (typeof window.initCardSpotlights === 'function') {
            window.initCardSpotlights();
        }

        // Init marquees
        if (typeof window.initMarquees === 'function') {
            window.initMarquees();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
