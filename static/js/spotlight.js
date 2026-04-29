/**
 * RatSignal — Mouse Spotlight Effect
 * Creates a subtle radial gradient that follows the cursor.
 * Also tracks per-card spotlight for .card-spotlight elements.
 */
(function() {
    'use strict';

    // === Global page spotlight ===
    const spotlight = document.createElement('div');
    spotlight.className = 'mouse-spotlight';
    document.body.appendChild(spotlight);

    let mx = -1000, my = -1000;

    document.addEventListener('mousemove', function(e) {
        mx = e.clientX;
        my = e.clientY;
        spotlight.style.setProperty('--mx', mx + 'px');
        spotlight.style.setProperty('--my', my + 'px');
    });

    // === Per-card spotlight tracking ===
    function initCardSpotlights() {
        var cards = document.querySelectorAll('.card-spotlight');
        cards.forEach(function(card) {
            card.addEventListener('mousemove', function(e) {
                var rect = card.getBoundingClientRect();
                var x = e.clientX - rect.left;
                var y = e.clientY - rect.top;
                card.style.setProperty('--spotlight-x', x + 'px');
                card.style.setProperty('--spotlight-y', y + 'px');
            });
        });
    }

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCardSpotlights);
    } else {
        initCardSpotlights();
    }

    // Re-init when new cards are added (for dynamic content)
    window.initCardSpotlights = initCardSpotlights;

    // Disable on touch devices
    if ('ontouchstart' in window) {
        spotlight.style.display = 'none';
    }
})();
