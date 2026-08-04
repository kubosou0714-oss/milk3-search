document.addEventListener('DOMContentLoaded', function () {
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

    var zone = document.querySelector('.center-cards-zone');
    var doujinPanel = document.getElementById('doujin-results-panel');
    var avPanel = document.getElementById('av-results-panel');
    var modeTabs = document.querySelector('.mode-tabs');

    function setPanelVisible(panel, visible) {
        if (!panel) return;
        panel.hidden = !visible;
        panel.classList.toggle('is-hidden', !visible);
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function reasonsHtml(reasons) {
        if (!reasons || !reasons.length) return '';
        var lis = reasons.map(function (r) {
            return '<li>' + escapeHtml(r) + '</li>';
        }).join('');
        return (
            '<div class="recommend-reasons">' +
            '<div class="recommend-reasons-title">おすすめ理由</div>' +
            '<ul>' + lis + '</ul></div>'
        );
    }

    function buildDoujinCard(item) {
        var id = item.product_id || '';
        var img = item.image_url
            ? '<img src="' + escapeHtml(item.image_url) + '" alt="" class="thumb-img" loading="lazy" onerror="this.closest(\'.thumb-area\').classList.remove(\'has-image\'); this.remove();">'
            : '';
        return (
            '<a href="' + escapeHtml(item.url) + '" class="product-card" target="_blank" rel="noopener noreferrer" data-id="' + escapeHtml(id) + '">' +
            '<div class="thumb-area' + (item.image_url ? ' has-image' : '') + '">' + img + '</div>' +
            '<div class="info-area"><div>' +
            '<div class="title-text">' + escapeHtml(item.title) + '</div>' +
            '<div class="author-text">サークル：' + escapeHtml(item.circle_name || '—') + '</div>' +
            reasonsHtml(item.reasons) +
            '</div><div class="meta-bottom">' +
            '<div class="price-text">' + escapeHtml(item.price || '—') + '<span>(税込)</span></div>' +
            '<span class="btn-dlsite touch-btn">DLsiteで見る ↗</span>' +
            '</div></div></a>'
        );
    }

    function buildAvCard(item) {
        var id = item.content_id || '';
        var link = item.link_url || item.url || '#';
        var img = item.image_url
            ? '<img src="' + escapeHtml(item.image_url) + '" alt="" class="thumb-img" loading="lazy" onerror="this.closest(\'.thumb-area\').classList.remove(\'has-image\'); this.remove();">'
            : '';
        var priceExtra = '';
        if (item.list_price && item.list_price !== item.price) {
            priceExtra = '<span>（定価 ' + escapeHtml(item.list_price) + '）(税込)</span>';
        } else {
            priceExtra = '<span>(税込)</span>';
        }
        var genre = (item.genre && item.genre !== '—')
            ? '<div class="author-text">ジャンル：' + escapeHtml(item.genre) + '</div>'
            : '';
        return (
            '<a href="' + escapeHtml(link) + '" class="product-card product-card--av" target="_blank" rel="noopener noreferrer" data-id="' + escapeHtml(id) + '">' +
            '<div class="thumb-area thumb-area--av' + (item.image_url ? ' has-image' : '') + '">' + img + '</div>' +
            '<div class="info-area"><div>' +
            '<div class="title-text">' + escapeHtml(item.title) + '</div>' +
            '<div class="author-text">女優：' + escapeHtml(item.actress || '—') + '</div>' +
            '<div class="author-text">メーカー：' + escapeHtml(item.maker || '—') + '</div>' +
            genre +
            reasonsHtml(item.reasons) +
            '</div><div class="meta-bottom">' +
            '<div class="price-text">' + escapeHtml(item.price || '—') + priceExtra + '</div>' +
            '<span class="btn-av touch-btn">FANZAで見る ↗</span>' +
            '</div></div></a>'
        );
    }

    function existingIds(panel) {
        var ids = {};
        panel.querySelectorAll('.product-card[data-id]').forEach(function (el) {
            var id = el.getAttribute('data-id');
            if (id) ids[id] = true;
        });
        return ids;
    }

    function loadMore(source) {
        if (!zone) return;
        var panel = source === 'av' ? avPanel : doujinPanel;
        if (!panel) return;
        var btn = panel.querySelector('.results-load-more[data-source="' + source + '"]');
        var errEl = panel.querySelector('.results-load-error');
        var keyword = zone.getAttribute('data-keyword') || '';
        var sort = zone.getAttribute('data-sort') || 'recommend';
        var page = parseInt(panel.getAttribute('data-page') || '1', 10) || 1;
        var nextPage = page + 1;

        if (btn) {
            btn.disabled = true;
            btn.textContent = '読み込み中…';
        }
        if (errEl) {
            errEl.hidden = true;
            errEl.textContent = '';
        }

        var url = '/api/results?keyword=' + encodeURIComponent(keyword) +
            '&sort=' + encodeURIComponent(sort) +
            '&page=' + encodeURIComponent(String(nextPage)) +
            '&source=' + encodeURIComponent(source);

        fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
            .then(function (result) {
                var data = result.data || {};
                if (!result.ok || data.error && !(data.items && data.items.length)) {
                    throw new Error(data.error || '追加の取得に失敗しました。');
                }
                var seen = existingIds(panel);
                var html = '';
                (data.items || []).forEach(function (item) {
                    var id = source === 'av' ? (item.content_id || '') : (item.product_id || '');
                    if (id && seen[id]) return;
                    if (id) seen[id] = true;
                    html += source === 'av' ? buildAvCard(item) : buildDoujinCard(item);
                });
                if (btn && html) {
                    btn.insertAdjacentHTML('beforebegin', html);
                }
                panel.setAttribute('data-page', String(data.page || nextPage));
                var hasMore = !!data.has_more;
                panel.setAttribute('data-has-more', hasMore ? '1' : '0');
                if (data.total_count != null) {
                    panel.setAttribute('data-total', String(data.total_count));
                    var countEl = panel.querySelector('.results-count');
                    if (countEl) countEl.textContent = '全' + data.total_count + '件';
                }
                if (btn) {
                    if (!hasMore) {
                        btn.hidden = true;
                    } else {
                        btn.disabled = false;
                        btn.textContent = 'もっと見る▽';
                    }
                }
            })
            .catch(function (err) {
                if (errEl) {
                    errEl.hidden = false;
                    errEl.textContent = err.message || '追加の取得に失敗しました。時間をおいて再度お試しください。';
                }
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = 'もっと見る▽';
                }
            });
    }

    document.querySelectorAll('.results-load-more').forEach(function (btn) {
        btn.addEventListener('click', function () {
            loadMore(btn.getAttribute('data-source') || 'doujin');
        });
    });

    if (modeTabs && doujinPanel && avPanel) {
        modeTabs.querySelectorAll('.mode-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                var mode = tab.getAttribute('data-tab');
                var showDoujin = mode === 'doujin';

                modeTabs.querySelectorAll('.mode-tab').forEach(function (item) {
                    var active = item === tab;
                    item.classList.toggle('is-active', active);
                    item.setAttribute('aria-selected', active ? 'true' : 'false');
                });

                setPanelVisible(doujinPanel, showDoujin);
                setPanelVisible(avPanel, !showDoujin);

                try {
                    var url = new URL(window.location.href);
                    url.searchParams.set('tab', mode);
                    window.history.replaceState({}, '', url.toString());
                } catch (e) { /* ignore */ }

                document.querySelectorAll('.sort-tab').forEach(function (link) {
                    try {
                        var href = new URL(link.href, window.location.origin);
                        href.searchParams.set('tab', mode);
                        link.href = href.pathname + href.search;
                    } catch (err) { /* ignore */ }
                });
            });
        });
    }

    var AGE_KEY = 'shikozo_age_verified';
    var ageGate = document.getElementById('age-gate');
    if (ageGate) {
        var askView = ageGate.querySelector('[data-age-view="ask"]');
        var deniedView = ageGate.querySelector('[data-age-view="denied"]');
        var yesBtn = ageGate.querySelector('[data-age-yes]');
        var noBtn = ageGate.querySelector('[data-age-no]');
        var root = document.documentElement;

        function setPending(pending) {
            root.classList.toggle('age-pending', pending);
            root.classList.toggle('age-verified', !pending);
            document.body.classList.toggle('age-gate-open', pending);
            ageGate.setAttribute('aria-hidden', pending ? 'false' : 'true');
            if (!pending) {
                ageGate.hidden = true;
            } else {
                ageGate.hidden = false;
            }
        }

        function showDenied() {
            if (askView) askView.hidden = true;
            if (deniedView) deniedView.hidden = false;
            setPending(true);
        }

        var verified = root.classList.contains('age-verified');
        if (!verified) {
            setPending(true);
        } else {
            ageGate.hidden = true;
            ageGate.setAttribute('aria-hidden', 'true');
        }

        if (yesBtn) {
            yesBtn.addEventListener('click', function () {
                try {
                    window.localStorage.setItem(AGE_KEY, '1');
                } catch (err) {
                    /* ignore */
                }
                setPending(false);
            });
        }

        if (noBtn) {
            noBtn.addEventListener('click', function () {
                showDenied();
            });
        }
    }
});
