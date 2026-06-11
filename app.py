import os

from flask import Flask, render_template, request

from dlsite_scraper import fetch_works

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "public", "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

IS_VERCEL = os.environ.get("VERCEL") == "1"
MAX_RESULTS = 10 if IS_VERCEL else 30

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


def _recommendation_reasons(keyword: str, title: str, circle_name: str) -> list[str]:
    reasons: list[str] = []
    if keyword and keyword in title:
        reasons.append(f"「{keyword}」のキーワードにマッチしています")
    if circle_name and circle_name != "—":
        reasons.append(f"サークル「{circle_name}」の作品です")
    reasons.append("DLsiteトレンド検索からのおすすめです")
    return reasons[:3]


def _works_to_results(works, keyword: str = ""):
    return [
        {
            "title": w.title,
            "circle_name": w.circle_name,
            "price": w.price,
            "image_url": w.thumbnail_url,
            "url": w.product_url,
            "reasons": _recommendation_reasons(keyword, w.title, w.circle_name),
        }
        for w in works[:MAX_RESULTS]
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

    works, _search_url, error = fetch_works(keyword)
    results_data = _works_to_results(works, keyword)

    return render_template(
        "results.html",
        keyword=keyword,
        results=results_data,
        error=error,
        popular_keywords=POPULAR_KEYWORDS,
    )


if __name__ == "__main__":
    print("起動しました: http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
