"""DLsite 同人（maniax）検索の取得・解析。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

SITE = "maniax"
BASE = "https://www.dlsite.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
IS_VERCEL = os.environ.get("VERCEL") == "1"
REQUEST_TIMEOUT = 8 if IS_VERCEL else 30
# DLsite の fsr/ajax は per_page 指定を無視し、実質 30 件固定で返す
PER_PAGE = 30
IMG_URL_RE = re.compile(r"//img\.dlsite\.jp/[^'\"\s>]+\.(?:jpg|jpeg|png|webp)", re.I)

# UI sort → DLsite /order/ 値（サイトの並び替えに合わせる）
# 人気順: trend（DLsite UI の「人気順」デフォルト）
# 新着順: release_d（発売日が新しい順）
ORDER_POPULAR = "trend"
ORDER_NEWEST = "release_d"
VALID_ORDERS = {ORDER_POPULAR, ORDER_NEWEST, "dl_d", "sale_d", "release", "date"}

# DLsite がキーワードをジャンルへ変換し 0 件になる場合の代替語
KEYWORD_ALIASES: dict[str, str] = {
    "催眠": "暗示",
}


@dataclass
class WorkItem:
    product_id: str
    title: str
    thumbnail_url: str
    circle_name: str
    price: str
    product_url: str
    description: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class FetchResult:
    works: list[WorkItem]
    search_url: str
    error: str | None = None
    total_count: int | None = None
    page: int = 1
    per_page: int = PER_PAGE
    order: str = ORDER_POPULAR


def _resolve_keyword(keyword: str) -> str:
    """API 検索用にキーワードを正規化する。"""
    stripped = keyword.strip()
    return KEYWORD_ALIASES.get(stripped, stripped)


def normalize_dlsite_order(sort: str | None) -> str:
    """アプリの sort 値を DLsite order に変換する。"""
    key = (sort or "popular").strip().lower()
    if key in ("newest", "new", "date", "release"):
        return ORDER_NEWEST
    if key in ("popular", "trend", "hot", "rank"):
        return ORDER_POPULAR
    if key in VALID_ORDERS:
        return key
    return ORDER_POPULAR


def build_search_url(keyword: str, order: str = ORDER_POPULAR) -> str:
    """ユーザー向け DLsite 検索ページ URL。"""
    encoded = quote(keyword.strip(), safe="")
    order = order if order in VALID_ORDERS else ORDER_POPULAR
    return f"{BASE}/{SITE}/fsr/=/keyword/{encoded}/order/{order}"


def build_sapi_url(
    keyword: str,
    page: int = 1,
    order: str = ORDER_POPULAR,
    per_page: int | None = None,
) -> str:
    """作品一覧 JSON（HTML 断片入り）の API URL。"""
    encoded = quote(_resolve_keyword(keyword), safe="")
    order = order if order in VALID_ORDERS else ORDER_POPULAR
    page = max(1, int(page))
    size = per_page if per_page is not None else PER_PAGE
    size = max(1, min(int(size), 100))
    return (
        f"{BASE}/{SITE}/fsr/ajax/=/language/jp/keyword/{encoded}"
        f"/order/{order}/per_page/{size}/page/{page}"
    )


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.9",
            "Referer": f"{BASE}/{SITE}/",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    session.cookies.set("adultchecked", "1", domain=".dlsite.com")
    return session


def _normalize_image_url(src: str | None) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return BASE + src
    return src


def _product_url(product_id: str) -> str:
    return f"{BASE}/{SITE}/work/=/product_id/{product_id}.html"


def _extract_price(item: BeautifulSoup) -> str:
    sale = item.select_one(".work_price .work_price_base")
    original = item.select_one(".work_price_wrap .strike .work_price_base")
    if original and sale and sale is not original:
        return f"{sale.get_text(strip=True)}（定価 {original.get_text(strip=True)}）"
    target = sale or item.select_one(".work_price")
    if target:
        return target.get_text(strip=True)
    return "—"


def _unique_labels(values: list[str], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = (value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _extract_genres_and_tags(li: BeautifulSoup) -> tuple[list[str], list[str]]:
    genres: list[str] = []
    tags: list[str] = []
    for a in li.select(".search_tag a, a[href*='/genre/'], a[href*='/fsr/=/genre']"):
        label = a.get_text(strip=True)
        href = a.get("href") or ""
        if not label:
            continue
        if "/genre" in href:
            genres.append(label)
        else:
            tags.append(label)
    for span in li.select(".search_tag span, .work_genre, .genre_rank"):
        label = span.get_text(strip=True)
        if label:
            tags.append(label)
    return _unique_labels(genres), _unique_labels(tags)


def _extract_description(li: BeautifulSoup) -> str:
    for selector in (".work_text", ".work_article", ".search_summary", ".work_intro"):
        el = li.select_one(selector)
        if el:
            text = el.get_text(" ", strip=True)
            if text:
                return text[:1000]
    return ""


def _extract_thumbnail(li: BeautifulSoup) -> str:
    img_el = li.select_one(".work_thumb_inner > img") or li.select_one("img")
    if img_el:
        for attr in ("data-src", "data-original", "src"):
            value = img_el.get(attr)
            if value and not value.startswith("data:"):
                return _normalize_image_url(value)

        vue_src = img_el.get(":src") or img_el.get("v-bind:src")
        if vue_src:
            match = IMG_URL_RE.search(vue_src)
            if match:
                return _normalize_image_url(match.group(0))

    match = IMG_URL_RE.search(str(li))
    if match:
        return _normalize_image_url(match.group(0))
    return ""


def _parse_search_result_html(html_fragment: str) -> list[WorkItem]:
    soup = BeautifulSoup(html_fragment, "html.parser")
    items: list[WorkItem] = []
    seen: set[str] = set()

    for li in soup.select("#search_result_img_box > li"):
        product_el = li.select_one("div[data-product_id]")
        if not product_el:
            continue
        product_id = product_el.get("data-product_id", "").strip()
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)

        title_el = li.select_one(".work_name a[title]") or li.select_one(".work_name a")
        title = ""
        if title_el:
            title = title_el.get("title") or title_el.get_text(strip=True)

        circle_el = li.select_one(".maker_name a")
        circle_name = circle_el.get_text(strip=True) if circle_el else "—"
        genres, tags = _extract_genres_and_tags(li)
        description = _extract_description(li)

        items.append(
            WorkItem(
                product_id=product_id,
                title=title or product_id,
                thumbnail_url=_extract_thumbnail(li),
                circle_name=circle_name,
                price=_extract_price(li),
                product_url=_product_url(product_id),
                description=description,
                genres=genres,
                tags=tags,
            )
        )

    return items


def _extract_total_count(payload: dict) -> int | None:
    page_info = payload.get("page_info")
    if isinstance(page_info, dict):
        count = page_info.get("count")
        if count is not None:
            try:
                return int(count)
            except (TypeError, ValueError):
                pass
    for key in ("count", "total_count", "work_count"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _find_sapi_url_from_page(soup: BeautifulSoup, keyword: str, order: str) -> str | None:
    """検索結果ページ内の data-url から maniax 用 API を探す。"""
    encoded = quote(_resolve_keyword(keyword), safe="")
    pattern = re.compile(
        rf"/{SITE}/(?:fsr/ajax|sapi)/.*keyword/{re.escape(encoded)}", re.I
    )
    for el in soup.select("[data-url]"):
        url = el.get("data-url", "")
        if not pattern.search(url):
            continue
        # 可能なら order を差し替え
        if "/order/" in url:
            url = re.sub(r"/order/[^/]+", f"/order/{order}", url, count=1)
        return url
    return None


def _friendly_connection_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "403" in text or "401" in text or "forbidden" in text:
        return "DLsiteへのアクセスが制限されました。時間をおいて再度お試しください。"
    if "timeout" in text or "timed out" in text:
        return "DLsiteへの接続がタイムアウトしました。時間をおいて再度お試しください。"
    return f"DLsite への接続に失敗しました: {exc}"


def fetch_works(
    keyword: str,
    page: int = 1,
    order: str = ORDER_POPULAR,
    per_page: int | None = None,
) -> FetchResult:
    """
    キーワードで作品を取得する（元サイトの並び順を維持）。

    Returns:
        FetchResult（作品一覧・検索URL・エラー・総件数など）
    """
    keyword = keyword.strip()
    order = order if order in VALID_ORDERS else ORDER_POPULAR
    page = max(1, int(page))
    size = PER_PAGE if per_page is None else max(1, min(int(per_page), 100))
    search_url = build_search_url(keyword, order=order)

    if not keyword:
        return FetchResult(
            works=[],
            search_url=search_url,
            error="キーワードを入力してください。",
            page=page,
            per_page=size,
            order=order,
        )

    session = _session()
    error: str | None = None
    page_soup: BeautifulSoup | None = None

    try:
        page_resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
        if page_resp.status_code in (401, 403):
            return FetchResult(
                works=[],
                search_url=search_url,
                error="DLsiteへのアクセスが制限されました。時間をおいて再度お試しください。",
                page=page,
                per_page=size,
                order=order,
            )
        page_resp.raise_for_status()
        page_soup = BeautifulSoup(page_resp.text, "html.parser")
        sapi_url = _find_sapi_url_from_page(page_soup, keyword, order) or build_sapi_url(
            keyword, page=page, order=order, per_page=size
        )
        # data-url に page が含まれる場合は要求ページへ合わせる
        if "/page/" in sapi_url:
            sapi_url = re.sub(r"/page/\d+", f"/page/{page}", sapi_url, count=1)
        if "/per_page/" in sapi_url:
            sapi_url = re.sub(r"/per_page/\d+", f"/per_page/{size}", sapi_url, count=1)
        if "/order/" in sapi_url:
            sapi_url = re.sub(r"/order/[^/]+", f"/order/{order}", sapi_url, count=1)

        api_resp = session.get(sapi_url, timeout=REQUEST_TIMEOUT)
        if api_resp.status_code in (401, 403):
            return FetchResult(
                works=[],
                search_url=search_url,
                error="DLsiteへのアクセスが制限されました。時間をおいて再度お試しください。",
                page=page,
                per_page=size,
                order=order,
            )
        api_resp.raise_for_status()
        payload = api_resp.json()

        if isinstance(payload, dict) and "search_result" in payload:
            works = _parse_search_result_html(payload["search_result"])
            total_count = _extract_total_count(payload)
            if works:
                return FetchResult(
                    works=works,
                    search_url=search_url,
                    error=None,
                    total_count=total_count,
                    page=page,
                    per_page=size,
                    order=order,
                )
            return FetchResult(
                works=[],
                search_url=search_url,
                error="検索結果が 0 件でした。キーワードを変えて試してください。",
                total_count=total_count if total_count is not None else 0,
                page=page,
                per_page=size,
                order=order,
            )

    except requests.RequestException as exc:
        error = _friendly_connection_error(exc)
    except (json.JSONDecodeError, ValueError) as exc:
        error = f"検索結果の読み取りに失敗しました: {exc}"

    try:
        if page_soup is not None and page == 1:
            fragment = page_soup.select_one("#search_result_img_box")
            if fragment:
                works = _parse_search_result_html(str(fragment.parent or fragment))
                if works:
                    return FetchResult(
                        works=works,
                        search_url=search_url,
                        error=None,
                        total_count=len(works),
                        page=page,
                        per_page=size,
                        order=order,
                    )
    except Exception:
        pass

    return FetchResult(
        works=[],
        search_url=search_url,
        error=error or "作品情報を取得できませんでした。",
        page=page,
        per_page=size,
        order=order,
    )
