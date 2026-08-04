import logging
import os
from datetime import date
from urllib.parse import quote

from flask import Flask, Response, jsonify, render_template, request

from search_expansion import fetch_expanded_works, fetch_source_ordered_works
from fanza_api import fetch_fanza_works
from ai_recommend import attach_doujin_reasons, attach_fanza_reasons

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "public", "static")
SITE_URL = "https://milk3-search.vercel.app"

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

IS_VERCEL = os.environ.get("VERCEL") == "1"
# AI紹介文の成否ログを本番でも見えるようにする
logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
if os.environ.get("SEARCH_DEBUG", "0" if IS_VERCEL else "1") == "1":
    pass
MAX_RESULTS = 10 if IS_VERCEL else 30
MAX_AV_RESULTS = 10 if IS_VERCEL else 20

SORT_RECOMMEND = "recommend"
SORT_POPULAR = "popular"
SORT_NEWEST = "newest"
VALID_SORTS = {SORT_RECOMMEND, SORT_POPULAR, SORT_NEWEST}

POPULAR_KEYWORDS = [
    "男の娘",
    "メス堕ち",
    "催眠",
    "NTR",
    "寝取られ",
    "巨乳",
    "学園",
    "ラブラブ",
]

# Sitemap に載せる人気キーワード（検索結果の発見性向上）
SITEMAP_KEYWORDS = ["催眠", "巨乳", "NTR", "男の娘", "メス堕ち", "寝取られ"]


def _asset_version() -> str:
    """CSS/JS のキャッシュ bust 用。デプロイごとに自動更新される。"""
    env_version = os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("ASSET_VERSION")
    if env_version:
        return env_version[:12]

    latest = 0.0
    for name in ("site.css", "menu.js"):
        path = os.path.join(STATIC_DIR, name)
        try:
            latest = max(latest, os.path.getmtime(path))
        except OSError:
            continue
    return str(int(latest)) if latest else "1"


@app.context_processor
def inject_asset_version():
    return {"asset_version": _asset_version()}


@app.after_request
def prevent_html_cache(response):
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


def _normalize_sort(value: str | None) -> str:
    key = (value or SORT_RECOMMEND).strip().lower()
    if key in ("new", "date", "release"):
        return SORT_NEWEST
    if key in ("rank", "hot", "trend"):
        return SORT_POPULAR
    if key in VALID_SORTS:
        return key
    return SORT_RECOMMEND


def _parse_page(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except (TypeError, ValueError):
        return 1


def _has_more(page: int, page_size: int, total_count: int | None, fetched: int) -> bool:
    if total_count is not None:
        return page * page_size < total_count
    return fetched >= page_size


def _works_to_results(works, keyword: str = "", *, with_ai: bool = True, limit: int | None = None):
    cap = MAX_RESULTS if limit is None else max(0, int(limit))
    sliced = works[:cap]
    if with_ai:
        return attach_doujin_reasons(sliced, keyword)
    # 追加読み込みはテンプレ理由のみ（応答速度優先）
    from ai_recommend import template_reasons

    items = []
    for work in sliced:
        title = getattr(work, "title", "") or ""
        circle = getattr(work, "circle_name", "") or ""
        items.append(
            {
                "title": title,
                "circle_name": circle,
                "price": getattr(work, "price", "—"),
                "image_url": getattr(work, "thumbnail_url", ""),
                "url": getattr(work, "product_url", ""),
                "product_id": getattr(work, "product_id", ""),
                "reasons": template_reasons(keyword, title, circle),
            }
        )
    return items


def _fetch_doujin_page(keyword: str, sort: str, page: int, *, with_ai: bool = True):
    """同人結果1ページ分を取得。元サイト順（popular/newest）またはおすすめ拡張。"""
    if sort == SORT_RECOMMEND:
        if page > 1:
            return {
                "items": [],
                "error": None,
                "total_count": None,
                "page": page,
                "page_size": MAX_RESULTS,
                "has_more": False,
                "sort": sort,
            }
        works, _url, error, _terms = fetch_expanded_works(keyword)
        items = _works_to_results(works, keyword, with_ai=with_ai)
        total = len(works) if works else (0 if not error else None)
        return {
            "items": items,
            "error": error,
            "total_count": total,
            "page": 1,
            "page_size": MAX_RESULTS,
            "has_more": False,
            "sort": sort,
        }

    # DLsite は1ページ約30件固定。返却順をそのまま表示する。
    result = fetch_source_ordered_works(
        keyword, sort=sort, page=page, per_page=30
    )
    page_size = 30
    items = _works_to_results(
        result.works,
        keyword,
        with_ai=with_ai and page == 1,
        limit=len(result.works),
    )
    for item, work in zip(items, result.works):
        item["product_id"] = getattr(work, "product_id", "")
    return {
        "items": items,
        "error": result.error,
        "total_count": result.total_count,
        "page": result.page,
        "page_size": page_size,
        "has_more": _has_more(result.page, page_size, result.total_count, len(items)),
        "sort": sort,
    }


def _fetch_av_page(keyword: str, sort: str, page: int, *, with_ai: bool = True):
    api_sort = SORT_NEWEST if sort == SORT_NEWEST else SORT_POPULAR
    # おすすめタブでも AV は公式人気順
    if sort == SORT_RECOMMEND:
        api_sort = SORT_POPULAR
    result = fetch_fanza_works(
        keyword, limit=MAX_AV_RESULTS, sort=api_sort, page=page
    )
    works = result.works
    if works and with_ai and page == 1:
        works = attach_fanza_reasons(works, keyword)
    elif works and not with_ai:
        from ai_recommend import fanza_template_reasons

        for work in works:
            work["reasons"] = fanza_template_reasons(
                keyword,
                str(work.get("title") or ""),
                str(work.get("actress") or ""),
            )
    return {
        "items": works,
        "error": result.error,
        "total_count": result.total_count,
        "page": result.page,
        "page_size": result.hits,
        "has_more": _has_more(result.page, result.hits, result.total_count, len(works)),
        "sort": sort,
        "floor": result.floor,
    }


@app.route("/")
def index():
    return render_template("index.html", popular_keywords=POPULAR_KEYWORDS)


@app.route("/results", methods=["GET"])
def results():
    keyword = request.args.get("keyword", "").strip()
    sort = _normalize_sort(request.args.get("sort"))
    tab = (request.args.get("tab") or "doujin").strip().lower()
    if tab not in ("doujin", "av"):
        tab = "doujin"

    empty_ctx = {
        "keyword": keyword,
        "sort": sort,
        "tab": tab,
        "results": [],
        "av_works": [],
        "av_error": None,
        "error": None,
        "doujin_total": None,
        "av_total": None,
        "doujin_has_more": False,
        "av_has_more": False,
        "doujin_page": 1,
        "av_page": 1,
        "popular_keywords": POPULAR_KEYWORDS,
    }

    if not keyword:
        empty_ctx["error"] = "キーワードを入力してください。"
        return render_template("results.html", **empty_ctx)

    # 初回表示は page=1 固定（追加は /api/results）
    doujin = _fetch_doujin_page(keyword, sort, 1, with_ai=True)
    av = _fetch_av_page(keyword, sort, 1, with_ai=True)

    return render_template(
        "results.html",
        keyword=keyword,
        sort=sort,
        tab=tab,
        results=doujin["items"],
        av_works=av["items"],
        av_error=av["error"],
        error=doujin["error"],
        doujin_total=doujin["total_count"],
        av_total=av["total_count"],
        doujin_has_more=doujin["has_more"],
        av_has_more=av["has_more"],
        doujin_page=doujin["page"],
        av_page=av["page"],
        popular_keywords=POPULAR_KEYWORDS,
    )


@app.route("/api/results", methods=["GET"])
def api_results():
    """同じ sort / keyword で次ページを取得（もっと見る用）。"""
    keyword = request.args.get("keyword", "").strip()
    sort = _normalize_sort(request.args.get("sort"))
    page = _parse_page(request.args.get("page"))
    source = (request.args.get("source") or "doujin").strip().lower()

    if not keyword:
        return jsonify({"ok": False, "error": "キーワードを入力してください。", "items": []}), 400

    if source == "av":
        payload = _fetch_av_page(keyword, sort, page, with_ai=False)
    else:
        payload = _fetch_doujin_page(keyword, sort, page, with_ai=False)

    return jsonify(
        {
            "ok": not bool(payload.get("error")) or bool(payload.get("items")),
            "error": payload.get("error"),
            "items": payload.get("items") or [],
            "total_count": payload.get("total_count"),
            "page": payload.get("page"),
            "page_size": payload.get("page_size"),
            "has_more": payload.get("has_more"),
            "sort": sort,
            "source": source,
            "keyword": keyword,
        }
    )


def _legal_page(page_title: str, content: str, path: str):
    return render_template(
        "legal.html",
        page_title=page_title,
        content=content,
        canonical_url=f"{SITE_URL}{path}",
    )


@app.route("/robots.txt")
def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain; charset=utf-8")


@app.route("/sitemap.xml")
def sitemap_xml():
    today = date.today().isoformat()
    urls = [
        ("/", "1.0", "weekly"),
        ("/terms", "0.3", "yearly"),
        ("/privacy", "0.3", "yearly"),
        ("/tokusho", "0.3", "yearly"),
    ]
    for keyword in SITEMAP_KEYWORDS:
        urls.append((f"/results?keyword={quote(keyword)}", "0.7", "weekly"))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, priority, changefreq in urls:
        loc = f"{SITE_URL}{path}"
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append(f"    <changefreq>{changefreq}</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        parts.append("  </url>")
    parts.append("</urlset>")
    return Response("\n".join(parts) + "\n", mimetype="application/xml; charset=utf-8")


@app.route("/terms")
def terms():
    content = """
    <h1>利用規約</h1>
    <p class="legal-updated">最終更新日: 2026年8月4日</p>
    <p>本利用規約（以下「本規約」）は、SHIKOZO（以下「当サイト」）の利用条件を定めるものです。利用者は本規約に同意のうえ当サイトを利用するものとします。</p>
    <h2>1. 年齢制限</h2>
    <p>当サイトは18歳未満の方のご利用を禁止します。18歳以上であることを確認し、自己の責任においてご利用ください。</p>
    <h2>2. コンテンツについて</h2>
    <p>当サイトはアダルトコンテンツを含む作品情報を表示します。表示内容は外部サービス（DLsite、FANZA等）の情報に基づくものであり、正確性・完全性を保証しません。</p>
    <h2>3. アフィリエイト</h2>
    <p>当サイトはDMMアフィリエイト等の成果報酬型広告を利用しています。外部サイトでの購入・契約は各サービスの規約に従います。</p>
    <h2>4. 禁止事項</h2>
    <ul>
      <li>法令または公序良俗に反する行為</li>
      <li>当サイトの運営を妨害する行為</li>
      <li>不正アクセス、過度な負荷をかける行為</li>
    </ul>
    <h2>5. 免責</h2>
    <p>当サイトの利用により生じた損害について、当サイト運営者は法令で認められる範囲を除き責任を負いません。</p>
    <h2>6. 規約の変更</h2>
    <p>当サイトは必要に応じて本規約を変更できます。変更後の規約は当ページに掲載した時点から効力を生じます。</p>
    """
    return _legal_page("利用規約", content, "/terms")


@app.route("/privacy")
def privacy():
    content = """
    <h1>プライバシーポリシー</h1>
    <p class="legal-updated">最終更新日: 2026年8月4日</p>
    <p>SHIKOZO（以下「当サイト」）は、利用者のプライバシー尊重のため、以下のとおり個人情報等の取扱いを定めます。</p>
    <h2>1. 取得する情報</h2>
    <ul>
      <li>アクセスログ（IPアドレス、ブラウザ情報、参照元等）</li>
      <li>Cookie / localStorage（年齢確認済みフラグ等）</li>
      <li>検索キーワード（サービス提供のため）</li>
    </ul>
    <h2>2. 利用目的</h2>
    <ul>
      <li>サービスの提供・改善</li>
      <li>不正利用の防止</li>
      <li>アフィリエイト成果の計測（外部サービス経由）</li>
    </ul>
    <h2>3. 第三者提供</h2>
    <p>法令に基づく場合を除き、個人を特定できる情報を本人同意なく第三者に提供しません。外部リンク先（DLsite、FANZA等）の取扱いは各サービスのポリシーに従います。</p>
    <h2>4. 保管・安全管理</h2>
    <p>当サイトは、取得情報の漏えい等を防ぐため、合理的な安全管理に努めます。</p>
    <h2>5. お問い合わせ</h2>
    <p>本ポリシーに関するお問い合わせは、当サイト運営者が別途指定する方法にて受け付けます。</p>
    """
    return _legal_page("プライバシーポリシー", content, "/privacy")


@app.route("/tokusho")
def tokusho():
    content = """
    <h1>特定商取引法に基づく表記</h1>
    <p class="legal-updated">最終更新日: 2026年8月4日</p>
    <p>当サイトは情報提供およびアフィリエイトリンクを掲載するウェブサイトです。デジタルコンテンツ自体の販売は行いません。</p>
    <h2>販売事業者</h2>
    <p>SHIKOZO 運営者（請求があった場合に遅滞なく開示します）</p>
    <h2>販売責任者</h2>
    <p>運営責任者（請求があった場合に遅滞なく開示します）</p>
    <h2>所在地</h2>
    <p>請求があった場合に遅滞なく開示します</p>
    <h2>連絡先</h2>
    <p>請求があった場合に遅滞なく開示します</p>
    <h2>販売価格</h2>
    <p>当サイト上での商品販売はありません。外部サイトでの価格は各販売事業者の表示に従います。</p>
    <h2>商品代金以外の必要料金</h2>
    <p>インターネット接続料金等は利用者の負担となります。</p>
    <h2>支払方法・時期</h2>
    <p>外部サイトでの購入時は、各販売事業者の定めに従います。</p>
    <h2>役務の提供時期</h2>
    <p>当サイトは検索・情報表示サービスを即時提供します。</p>
    <h2>返品・キャンセル</h2>
    <p>当サイトでは商品販売を行わないため、返品対応はありません。外部購入分は各販売事業者の規約に従います。</p>
    """
    return _legal_page("特定商取引法に基づく表記", content, "/tokusho")


if __name__ == "__main__":
    print("起動しました: http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
