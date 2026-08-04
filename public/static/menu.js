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
    var sortTabs = document.querySelector('.sort-tabs');
    var sortCache = {};
    var sortRequestId = 0;

    function setPanelVisible(panel, visible) {
        if (!panel) return;
        panel.hidden = !visible;
        panel.classList.toggle('is-hidden', !visible);
    }

    function currentTab() {
        var active = modeTabs && modeTabs.querySelector('.mode-tab.is-active');
        return (active && active.getAttribute('data-tab')) || 'doujin';
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

    function bindLoadMoreButtons(panel) {
        if (!panel) return;
        panel.querySelectorAll('.results-load-more').forEach(function (btn) {
            btn.addEventListener('click', function () {
                loadMore(btn.getAttribute('data-source') || 'doujin');
            });
        });
    }

    function fetchSource(keyword, sort, source) {
        var url = '/api/results?keyword=' + encodeURIComponent(keyword) +
            '&sort=' + encodeURIComponent(sort) +
            '&page=1' +
            '&source=' + encodeURIComponent(source);
        return fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (res) {
                return res.json().then(function (data) {
                    return { ok: res.ok, data: data || {} };
                });
            });
    }

    function renderPanel(source, data, sort) {
        var panel = source === 'av' ? avPanel : doujinPanel;
        if (!panel) return;
        var items = data.items || [];
        var error = data.error;
        var html = '';

        if (data.total_count != null) {
            html += '<p class="results-count">全' + escapeHtml(data.total_count) + '件</p>';
        } else if (sort === 'recommend' && items.length) {
            html += '<p class="results-count">表示' + items.length + '件（おすすめ）</p>';
        }

        if (error && !items.length) {
            html += '<p class="error-message">' + escapeHtml(error) + '</p>';
        } else if (items.length) {
            items.forEach(function (item) {
                html += source === 'av' ? buildAvCard(item) : buildDoujinCard(item);
            });
            if (data.has_more) {
                html += '<button type="button" class="results-load-more touch-btn" data-source="' +
                    escapeHtml(source) + '">もっと見る▽</button>';
            }
        } else {
            html += '<p class="error-message">' +
                (source === 'av' ? 'AV作品が見つかりませんでした' : '同人作品が見つかりませんでした') +
                '</p>';
        }
        html += '<p class="error-message results-load-error" hidden></p>';

        panel.innerHTML = html;
        panel.setAttribute('data-page', String(data.page || 1));
        panel.setAttribute('data-has-more', data.has_more ? '1' : '0');
        panel.setAttribute('data-total', data.total_count != null ? String(data.total_count) : '');
        panel.classList.remove('is-loading');
        bindLoadMoreButtons(panel);
    }

    function setSortTabsEnabled(enabled) {
        if (!sortTabs) return;
        sortTabs.querySelectorAll('.sort-tab').forEach(function (btn) {
            btn.disabled = !enabled;
        });
    }

    function setActiveSort(sort) {
        if (!sortTabs) return;
        sortTabs.querySelectorAll('.sort-tab').forEach(function (btn) {
            var active = btn.getAttribute('data-sort') === sort;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        if (zone) zone.setAttribute('data-sort', sort);
        var hidden = document.querySelector('.mini-form input[name="sort"]');
        if (hidden) hidden.value = sort;
        try {
            var url = new URL(window.location.href);
            url.searchParams.set('sort', sort);
            url.searchParams.set('tab', currentTab());
            window.history.replaceState({}, '', url.pathname + url.search);
        } catch (e) { /* ignore */ }
    }

    function showLoading(panel) {
        if (!panel) return;
        panel.classList.add('is-loading');
        panel.innerHTML = '<p class="results-loading-msg">読み込み中…</p>';
    }

    function switchSort(sort) {
        if (!zone || !sort) return;
        var keyword = zone.getAttribute('data-keyword') || '';
        var current = zone.getAttribute('data-sort') || 'recommend';
        if (!keyword || sort === current) {
            setActiveSort(sort);
            return;
        }

        setActiveSort(sort);
        var cached = sortCache[sort];
        if (cached && cached.doujin && cached.av) {
            renderPanel('doujin', cached.doujin, sort);
            renderPanel('av', cached.av, sort);
            return;
        }

        var reqId = ++sortRequestId;
        var active = currentTab();
        setSortTabsEnabled(false);
        showLoading(active === 'av' ? avPanel : doujinPanel);
        if (doujinPanel && active !== 'doujin') doujinPanel.classList.add('is-loading');
        if (avPanel && active !== 'av') avPanel.classList.add('is-loading');

        var primary = fetchSource(keyword, sort, active);
        var secondarySource = active === 'av' ? 'doujin' : 'av';
        var secondary = fetchSource(keyword, sort, secondarySource);

        primary.then(function (result) {
            if (reqId !== sortRequestId) return;
            var data = result.data || {};
            if (!result.ok && !(data.items && data.items.length)) {
                throw new Error(data.error || '並び替え結果の取得に失敗しました。');
            }
            renderPanel(active, data, sort);
            sortCache[sort] = sortCache[sort] || {};
            sortCache[sort][active] = data;
        }).catch(function (err) {
            if (reqId !== sortRequestId) return;
            var panel = active === 'av' ? avPanel : doujinPanel;
            if (panel) {
                panel.classList.remove('is-loading');
                panel.innerHTML = '<p class="error-message">' +
                    escapeHtml(err.message || '並び替え結果の取得に失敗しました。') +
                    '</p><p class="error-message results-load-error" hidden></p>';
            }
        });

        secondary.then(function (result) {
            if (reqId !== sortRequestId) return;
            var data = result.data || {};
            if (!result.ok && !(data.items && data.items.length) && data.error) {
                renderPanel(secondarySource, data, sort);
            } else {
                renderPanel(secondarySource, data, sort);
            }
            sortCache[sort] = sortCache[sort] || {};
            sortCache[sort][secondarySource] = data;
        }).catch(function (err) {
            if (reqId !== sortRequestId) return;
            renderPanel(secondarySource, {
                items: [],
                error: err.message || '取得に失敗しました。',
                page: 1,
                has_more: false
            }, sort);
        }).then(function () {
            if (reqId !== sortRequestId) return;
            setSortTabsEnabled(true);
        });

        primary.finally(function () {
            if (reqId !== sortRequestId) return;
            // アクティブ側が終わったらタブ再操作は許可（裏側は継続）
            if (sortCache[sort] && sortCache[sort][active]) {
                setSortTabsEnabled(true);
            }
        });
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
                // キャッシュを無効化（追加後は再取得が安全）
                if (sortCache[sort]) delete sortCache[sort];
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

    bindLoadMoreButtons(doujinPanel);
    bindLoadMoreButtons(avPanel);

    if (sortTabs) {
        sortTabs.querySelectorAll('.sort-tab').forEach(function (btn) {
            btn.addEventListener('click', function () {
                switchSort(btn.getAttribute('data-sort') || 'recommend');
            });
        });
    }

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
