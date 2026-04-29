/**
 * RatSignal — Marquee / Ticker initialization
 * Duplicates marquee track content for seamless infinite scroll.
 */
(function() {
    'use strict';

    function initMarquees() {
        var tracks = document.querySelectorAll('.marquee-track');
        tracks.forEach(function(track) {
            // Only duplicate once
            if (track.dataset.duplicated) return;
            track.dataset.duplicated = 'true';

            // Clone all children and append for seamless loop
            var children = Array.from(track.children);
            children.forEach(function(child) {
                var clone = child.cloneNode(true);
                clone.setAttribute('aria-hidden', 'true');
                track.appendChild(clone);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initMarquees);
    } else {
        initMarquees();
    }

    window.initMarquees = initMarquees;
})();
