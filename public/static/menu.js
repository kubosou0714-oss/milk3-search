(function () {
    var toggle = document.querySelector('.menu-toggle');
    var nav = document.querySelector('.site-nav');
    var overlay = document.querySelector('.nav-overlay');
    if (!toggle || !nav || !overlay) return;

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
})();
