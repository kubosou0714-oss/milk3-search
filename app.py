import hashlib
import logging
import os
import socket

from flask import Flask, render_template, request

from search_expansion import (
    INITIAL_DISPLAY_COUNT,
    LOAD_MORE_STEP,
    fetch_expanded_works,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "public", "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

IS_VERCEL = os.environ.get("VERCEL") == "1"
DEFAULT_PORT = int(os.environ.get("PORT", "5000"))
if os.environ.get("SEARCH_DEBUG", "0" if IS_VERCEL else "1") == "1":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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


def _watched_asset_paths() -> list[str]:
    """更新検知対象（CSS/JS/HTML）。内容が変わると asset_version も変わる。"""
    templates_dir = os.path.join(BASE_DIR, "templates")
    return [
        os.path.join(STATIC_DIR, "site.css"),
        os.path.join(STATIC_DIR, "menu.js"),
        os.path.join(templates_dir, "index.html"),
        os.path.join(templates_dir, "results.html"),
    ]


def _asset_version() -> str:
    """CSS/JS の ?v= 用。ファイル内容のハッシュなので同一URLでも更新後に再取得される。"""
    hasher = hashlib.sha256()
    for path in _watched_asset_paths():
        hasher.update(path.encode("utf-8"))
        try:
            with open(path, "rb") as file:
                hasher.update(file.read())
        except OSError:
            hasher.update(b"missing")

    deploy_id = os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("ASSET_VERSION") or ""
    if deploy_id:
        hasher.update(deploy_id.encode("utf-8"))

    return hasher.hexdigest()[:12]


def _detect_lan_ip() -> str:
    """同一Wi-Fi内のスマホからアクセスできるPCのIPアドレスを取得。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _share_url(port: int = DEFAULT_PORT) -> str:
    """PC・スマホで共通して使えるURL。"""
    if IS_VERCEL:
        vercel_url = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
        if vercel_url:
            return f"https://{vercel_url}"

    try:
        host = request.host.split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            scheme = "https" if request.is_secure else "http"
            return f"{scheme}://{request.host}"
    except RuntimeError:
        pass

    lan_ip = _detect_lan_ip()
    return f"http://{lan_ip}:{port}"


@app.context_processor
def inject_asset_version():
    return {
        "asset_version": _asset_version(),
        "share_url": _share_url(),
        "is_local_dev": not IS_VERCEL,
    }


@app.after_request
def set_cache_headers(response):
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.path.startswith("/static/"):
        # ?v= が変わるたび URL 自体が変わるので、古いCSSが残りにくい
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


def _recommendation_reasons(keyword: str, title: str, circle_name: str) -> list[str]:
    reasons: list[str] = []
    if keyword and keyword in title:
        reasons.append(f"「{keyword}」のキーワードにマッチしています")
    if circle_name and circle_name != "—":
        reasons.append(f"サークル「{circle_name}」の作品です")
    reasons.append("DLsiteトレンド検索からのおすすめです")
    return reasons[:3]


def _works_to_results(scored_works, keyword: str = ""):
    return [
        {
            "title": s.work.title,
            "circle_name": s.work.circle_name,
            "price": s.work.price,
            "image_url": s.work.thumbnail_url,
            "url": s.work.product_url,
            "reasons": _recommendation_reasons(keyword, s.work.title, s.work.circle_name),
        }
        for s in scored_works
    ]


@app.route("/")
def index():
    return render_template("index.html", popular_keywords=POPULAR_KEYWORDS)


@app.route("/results", methods=["GET"])
def results():
    keyword = request.args.get("keyword", "").strip()

    if not keyword:
        return render_template(
            "results.html",
            keyword="",
            results=[],
            error="キーワードを入力してください。",
            popular_keywords=POPULAR_KEYWORDS,
        )

    scored, _search_url, error, _search_terms = fetch_expanded_works(keyword)
    results_data = _works_to_results(scored, keyword)

    return render_template(
        "results.html",
        keyword=keyword,
        results=results_data,
        error=error,
        popular_keywords=POPULAR_KEYWORDS,
        initial_display=INITIAL_DISPLAY_COUNT,
        load_more_step=LOAD_MORE_STEP,
        total_results=len(results_data),
    )


if __name__ == "__main__":
    port = DEFAULT_PORT
    share = _share_url(port)
    print("起動しました")
    print(f"  PC・スマホ共通URL: {share}")
    print("  ※ http://127.0.0.1:5000 はPC専用です。スマホでは上のURLを使ってください。")
    print("  ※ PCとスマホは同じWi-Fiに接続してください。")
    print("  終了するには Ctrl+C を押してください。")
    app.run(debug=True, host="0.0.0.0", port=port)
