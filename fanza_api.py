"""FANZA（DMM Affiliate API）のAV作品検索。"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
IS_VERCEL = os.environ.get("VERCEL") == "1"
REQUEST_TIMEOUT = 8 if IS_VERCEL else 20
SEARCH_DEBUG = os.environ.get("SEARCH_DEBUG", "0" if IS_VERCEL else "1") == "1"

CONFIG_ERROR = "FANZA検索の設定がまだ完了していません"


def _credentials() -> tuple[str, str] | None:
    api_id = os.environ.get("DMM_API_ID", "").strip()
    affiliate_id = os.environ.get("DMM_AFFILIATE_ID", "").strip()
    if not api_id or not affiliate_id:
        return None
    return api_id, affiliate_id


def _debug(message: str) -> None:
    if SEARCH_DEBUG:
        logger.info(message)


def _safe_request_query(params: dict[str, Any]) -> str:
    redacted = {key: value for key, value in params.items() if key not in ("api_id", "affiliate_id")}
    return f"{API_URL}?{urlencode(redacted)}"


def _join_names(items: Any, limit: int = 3) -> str:
    if not isinstance(items, list):
        return "—"
    names: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                names.append(name)
    if not names:
        return "—"
    return "・".join(names[:limit])


def _format_yen(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace(",", "").replace("円", "")
    if not text:
        return ""
    if text.isdigit():
        return f"{int(text):,}"
    return text


def _normalize_item(item: dict[str, Any], keyword: str) -> dict[str, Any]:
    prices = item.get("prices") if isinstance(item.get("prices"), dict) else {}
    iteminfo = item.get("iteminfo") if isinstance(item.get("iteminfo"), dict) else {}

    price = _format_yen(prices.get("price"))
    list_price = _format_yen(prices.get("list_price"))

    image_url = ""
    image_obj = item.get("imageURL")
    if isinstance(image_obj, dict):
        image_url = str(image_obj.get("large") or image_obj.get("small") or "").strip()

    affiliate_url = str(item.get("affiliateURL") or "").strip()
    url = str(item.get("URL") or "").strip()
    link_url = affiliate_url or url

    reasons: list[str] = []
    title = str(item.get("title") or "").strip()
    if keyword and keyword in title:
        reasons.append(f"「{keyword}」のキーワードにマッチしています")
    actress = _join_names(iteminfo.get("actress"))
    if actress != "—":
        reasons.append(f"女優：{actress}")
    reasons.append("FANZA検索からのおすすめです")

    return {
        "title": title or "—",
        "image_url": image_url,
        "actress": actress,
        "maker": _join_names(iteminfo.get("maker"), limit=2),
        "genre": _join_names(iteminfo.get("genre"), limit=4),
        "price": price or "—",
        "list_price": list_price,
        "url": url,
        "affiliate_url": affiliate_url,
        "link_url": link_url,
        "source": "fanza",
        "reasons": reasons[:3],
    }


def fetch_fanza_works(keyword: str, limit: int = 20) -> tuple[list[dict[str, Any]], str | None]:
    """
    FANZA AV作品をキーワード検索する。

    Returns:
        (正規化済み作品リスト, エラーメッセージ or None)
        成功時（0件含む）の error は None。
    """
    keyword = keyword.strip()
    if not keyword:
        return [], None

    creds = _credentials()
    if creds is None:
        _debug("FANZA search skipped: credentials not configured")
        return [], CONFIG_ERROR

    api_id, affiliate_id = creds
    hits = max(1, min(int(limit), 100))

    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": "FANZA",
        "service": "digital",
        "floor": "videoa",
        "hits": hits,
        "offset": 1,
        "sort": "rank",
        "keyword": keyword,
        "output": "json",
    }

    _debug(f"FANZA search keyword: {keyword}")
    _debug(f"FANZA request URL: {_safe_request_query(params)}")

    try:
        response = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        _debug(f"FANZA error: request failed ({exc})")
        return [], "FANZA検索に失敗しました。時間をおいて再度お試しください。"
    except ValueError as exc:
        _debug(f"FANZA error: invalid JSON ({exc})")
        return [], "FANZA検索の応答形式が想定外でした。"

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        _debug("FANZA error: unexpected response shape")
        return [], "FANZA検索の応答形式が想定外でした。"

    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    works = [_normalize_item(item, keyword) for item in raw_items if isinstance(item, dict)]
    works = [work for work in works if work.get("title") and work["title"] != "—"]

    _debug(f"FANZA fetched count: {len(raw_items)}")
    _debug(f"FANZA normalized count: {len(works)}")
    if works:
        titles = " | ".join(work["title"][:40] for work in works[:5])
        _debug(f"FANZA top titles: {titles}")

    return works, None
