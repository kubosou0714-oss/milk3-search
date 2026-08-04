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
PER_PAGE = 10 if IS_VERCEL else 30
IMG_URL_RE = re.compile(r"//img\.dlsite\.jp/[^'\"\s>]+\.(?:jpg|jpeg|png|webp)", re.I)

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


def _resolve_keyword(keyword: str) -> str:
    """API 検索用にキーワードを正規化する。"""
    stripped = keyword.strip()
    return KEYWORD_ALIASES.get(stripped, stripped)


def build_search_url(keyword: str) -> str:
    """ユーザー向け DLsite 検索ページ URL。"""
    encoded = quote(keyword.strip(), safe="")
    return f"{BASE}/{SITE}/fsr/=/keyword/{encoded}/order/trend"


def build_sapi_url(keyword: str, page: int = 1) -> str:
    """作品一覧 JSON（HTML 断片入り）の API URL。"""
    encoded = quote(_resolve_keyword(keyword), safe="")
    return (
        f"{BASE}/{SITE}/fsr/ajax/=/language/jp/keyword/{encoded}"
        f"/order/trend/per_page/{PER_PAGE}/page/{page}"
    )


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.9",
            "Referer": f"{BASE}/{SITE}/",
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

    for li in soup.select("#search_result_img_box > li"):
        product_el = li.select_one("div[data-product_id]")
        if not product_el:
            continue
        product_id = product_el.get("data-product_id", "").strip()
        if not product_id:
            continue

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


def _find_sapi_url_from_page(soup: BeautifulSoup, keyword: str) -> str | None:
    """検索結果ページ内の data-url から maniax 用 API を探す。"""
    encoded = quote(_resolve_keyword(keyword), safe="")
    pattern = re.compile(
        rf"/{SITE}/(?:fsr/ajax|sapi)/.*keyword/{re.escape(encoded)}", re.I
    )
    for el in soup.select("[data-url]"):
        url = el.get("data-url", "")
        if pattern.search(url):
            return url
    return None


def fetch_works(keyword: str) -> tuple[list[WorkItem], str, str | None]:
    """
    キーワードで作品を取得する。

    Returns:
        (作品一覧, 検索ページURL, エラーメッセージ or None)
    """
    keyword = keyword.strip()
    if not keyword:
        return [], build_search_url(""), "キーワードを入力してください。"

    search_url = build_search_url(keyword)
    session = _session()
    error: str | None = None

    try:
        page_resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
        page_resp.raise_for_status()
        page_soup = BeautifulSoup(page_resp.text, "html.parser")
        sapi_url = _find_sapi_url_from_page(page_soup, keyword) or build_sapi_url(keyword)

        api_resp = session.get(sapi_url, timeout=REQUEST_TIMEOUT)
        api_resp.raise_for_status()
        payload = api_resp.json()

        if isinstance(payload, dict) and "search_result" in payload:
            works = _parse_search_result_html(payload["search_result"])
            if works:
                return works, search_url, None
            error = "検索結果が 0 件でした。キーワードを変えて試してください。"
            return [], search_url, error

    except requests.RequestException as exc:
        error = f"DLsite への接続に失敗しました: {exc}"
    except (json.JSONDecodeError, ValueError) as exc:
        error = f"検索結果の読み取りに失敗しました: {exc}"

    try:
        if "page_soup" in locals():
            fragment = page_soup.select_one("#search_result_img_box")
            if fragment:
                works = _parse_search_result_html(str(fragment.parent or fragment))
                if works:
                    return works, search_url, None
    except Exception:
        pass

    return [], search_url, error or "作品情報を取得できませんでした。"
