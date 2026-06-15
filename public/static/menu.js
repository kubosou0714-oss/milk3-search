(function () {
    var toggle = document.querySelector('.menu-toggle');
    var nav = document.querySelector('.site-nav');
    var overlay = document.querySelector('.nav-overlay');

    if (toggle && nav && overlay) {
        function setOpen(open) {
            toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            nav.classList.toggle('is-open', open);
            overlay.classList.toggle('is-open', open);
            document.body.style.overflow = open ? 'hidden' : '';
        }

        toggle.addEventListener('click', function () {
            setOpen(toggle.getAttribute('aria-expanded') !== 'true');
        });

        overlay.addEventListener('click', function () {
            setOpen(false);
        });

        nav.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                setOpen(false);
            });
        });
    }

    document.querySelectorAll('.keyword-more-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var more = btn.parentElement.querySelector('.keyword-chips--more');
            if (!more) return;
            var open = btn.getAttribute('aria-expanded') === 'true';
            more.hidden = open;
            btn.setAttribute('aria-expanded', open ? 'false' : 'true');
            btn.textContent = open ? 'もっと見る▽' : '閉じる△';
        });
    });

    document.querySelectorAll('.results-load-more').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var step = parseInt(btn.dataset.step, 10) || 10;
            var shown = parseInt(btn.dataset.shown, 10) || 10;
            var total = parseInt(btn.dataset.total, 10) || shown;
            var hidden = document.querySelectorAll('#doujin-results-panel .product-card.is-load-more-hidden');
            var reveal = Math.min(step, hidden.length);
            for (var i = 0; i < reveal; i++) {
                hidden[i].classList.remove('is-load-more-hidden');
            }
            shown += reveal;
            btn.dataset.shown = String(shown);
            if (shown >= total) {
                btn.hidden = true;
            }
        });
    });

    var doujinPanel = document.getElementById('doujin-results-panel');
    var avPanel = document.getElementById('av-results-panel');
    var modeTabs = document.querySelector('.mode-tabs');

    if (modeTabs && doujinPanel && avPanel) {
        modeTabs.querySelectorAll('.mode-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                var mode = tab.getAttribute('data-mode');
                var showDoujin = mode === 'doujin';

                modeTabs.querySelectorAll('.mode-tab').forEach(function (item) {
                    var active = item === tab;
                    item.classList.toggle('is-active', active);
                    item.setAttribute('aria-selected', active ? 'true' : 'false');
                });

                doujinPanel.hidden = !showDoujin;
                avPanel.hidden = showDoujin;
            });
        });
    }
})();
