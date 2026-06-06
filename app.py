import os

from flask import Flask, render_template, request

from dlsite_scraper import fetch_works

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "public", "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

IS_VERCEL = os.environ.get("VERCEL") == "1"
MAX_RESULTS = 10 if IS_VERCEL else 30


def _works_to_results(works):
    return [
        {
            "title": w.title,
            "circle_name": w.circle_name,
            "price": w.price,
            "image_url": w.thumbnail_url,
            "url": w.product_url,
        }
        for w in works[:MAX_RESULTS]
    ]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/results", methods=["GET"])
def results():
    keyword = request.args.get("keyword", "").strip()

    if not keyword:
        return render_template(
            "results.html",
            keyword="",
            results=[],
            error="キーワードを入力してください。",
        )

    works, _search_url, error = fetch_works(keyword)
    results_data = _works_to_results(works)

    return render_template(
        "results.html",
        keyword=keyword,
        results=results_data,
        error=error,
    )


if __name__ == "__main__":
    print("起動しました: http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
