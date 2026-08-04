"""FANZA（DMM Affiliate API）のAV作品検索。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
IS_VERCEL = os.environ.get("VERCEL") == "1"
REQUEST_TIMEOUT = 12 if IS_VERCEL else 20
SEARCH_DEBUG = os.environ.get("SEARCH_DEBUG", "0" if IS_VERCEL else "1") == "1"

CONFIG_ERROR = "FANZA検索の設定がまだ完了していません"
FLOORS = ("videoa", "videoc", "anime")

# UI sort → DMM ItemList sort
SORT_POPULAR = "rank"
SORT_NEWEST = "date"
VALID_SORTS = {SORT_POPULAR, SORT_NEWEST, "review", "price", "-price"}


@dataclass
class FanzaFetchResult:
    works: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    total_count: int | None = None
    page: int = 1
    hits: int = 20
    sort: str = SORT_POPULAR
    floor: str | None = None


def _credentials() -> tuple[str, str] | None:
    api_id = os.environ.get("DMM_API_ID", "").strip()
    affiliate_id = os.environ.get("DMM_AFFILIATE_ID", "").strip()
    # Vercel CLI のプレースホルダを誤って入れた場合を除外
    if not api_id or not affiliate_id:
        return None
    if api_id.upper() in {"[SENSITIVE]", "SENSITIVE", "ENCRYPTED"}:
        return None
    if affiliate_id.upper() in {"[SENSITIVE]", "SENSITIVE", "ENCRYPTED"}:
        return None
    return api_id, affiliate_id


def _api_affiliate_id(affiliate_id: str) -> str:
    """API は末尾 990〜999 のみ許可。001 等なら 990 に寄せる。"""
    match = re.match(r"^(.*-)(\d+)$", affiliate_id.strip())
    if not match:
        return affiliate_id.strip()
    suffix = int(match.group(2))
    if 990 <= suffix <= 999:
        return affiliate_id.strip()
    return f"{match.group(1)}990"


def _debug(message: str) -> None:
    # 本番でも結果件数は残す（秘密情報は出さない）
    if SEARCH_DEBUG or IS_VERCEL:
        logger.info(message)


def _safe_request_query(params: dict[str, Any]) -> str:
    redacted = {key: value for key, value in params.items() if key not in ("api_id", "affiliate_id")}
    return f"{API_URL}?{urlencode(redacted)}"


def normalize_fanza_sort(sort: str | None) -> str:
    key = (sort or "popular").strip().lower()
    if key in ("newest", "new", "date", "release"):
        return SORT_NEWEST
    if key in ("popular", "rank", "hot", "trend"):
        return SORT_POPULAR
    if key in VALID_SORTS:
        return key
    return SORT_POPULAR


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

    affiliate_url = str(item.get("affiliateURL") or item.get("affiliateUrl") or "").strip()
    url = str(item.get("URL") or item.get("url") or "").strip()
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
        "content_id": str(item.get("content_id") or item.get("product_id") or "").strip(),
        "source": "fanza",
        "reasons": reasons[:3],
    }


def _extract_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = result.get("items")
    if isinstance(raw_items, list):
        return [item for item in raw_items if isinstance(item, dict)]
    if isinstance(raw_items, dict):
        # 1件だけのとき dict になるケースに備える
        nested = raw_items.get("item")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [nested]
        return [raw_items]
    return []


def _friendly_api_error(result: dict[str, Any]) -> str:
    errors = result.get("errors")
    if isinstance(errors, dict):
        keys = {str(key).lower() for key in errors}
        if "api_id" in keys and "affiliate_id" in keys:
            return (
                "FANZA API認証に失敗しました。"
                "Vercelの DMM_API_ID と、末尾990〜999の DMM_AFFILIATE_ID を確認してください。"
            )
        if "api_id" in keys:
            return "FANZA API認証に失敗しました。Vercelの DMM_API_ID が正しいか確認してください。"
        if "affiliate_id" in keys:
            return (
                "FANZA API認証に失敗しました。"
                "DMM_AFFILIATE_ID は末尾が990〜999のAPI用IDにしてください。"
            )
    message = str(result.get("message") or "").strip()
    if message:
        return f"FANZA検索に失敗しました（{message}）。"
    return "FANZA検索に失敗しました。時間をおいて再度お試しください。"


def _request_items(
    api_id: str,
    affiliate_id: str,
    keyword: str,
    hits: int,
    floor: str,
    sort: str,
    offset: int,
) -> tuple[list[dict[str, Any]], str | None, int | None]:
    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": "FANZA",
        "service": "digital",
        "floor": floor,
        "hits": hits,
        "offset": max(1, int(offset)),
        "sort": sort if sort in VALID_SORTS else SORT_POPULAR,
        "keyword": keyword,
        "output": "json",
    }
    _debug(f"FANZA request: floor={floor} {_safe_request_query(params)}")

    try:
        response = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        try:
            payload = response.json()
        except ValueError:
            _debug(f"FANZA error: invalid JSON http={response.status_code}")
            return [], "FANZA検索の応答形式が想定外でした。", None
    except requests.RequestException as exc:
        text = str(exc).lower()
        _debug(f"FANZA error: request failed ({exc})")
        if "403" in text or "401" in text:
            return [], "FANZAへのアクセスが制限されました。時間をおいて再度お試しください。", None
        if "timeout" in text or "timed out" in text:
            return [], "FANZAへの接続がタイムアウトしました。時間をおいて再度お試しください。", None
        return [], "FANZA検索に失敗しました。時間をおいて再度お試しください。", None

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        _debug("FANZA error: unexpected response shape")
        return [], "FANZA検索の応答形式が想定外でした。", None

    status = result.get("status")
    status_ok = str(status).upper() in {"200", "OK"}
    if not status_ok or response.status_code >= 400:
        _debug(f"FANZA error: http={response.status_code} status={status} errors={result.get('errors')}")
        return [], _friendly_api_error(result), None

    items = _extract_items(result)
    total_count: int | None = None
    raw_total = result.get("total_count")
    if raw_total is not None:
        try:
            total_count = int(raw_total)
        except (TypeError, ValueError):
            total_count = None

    _debug(
        f"FANZA floor={floor} raw_count={len(items)} "
        f"result_count={result.get('result_count')} total_count={total_count}"
    )
    return items, None, total_count


def fetch_fanza_works(
    keyword: str,
    limit: int = 20,
    sort: str = "popular",
    page: int = 1,
    floor: str | None = None,
) -> FanzaFetchResult:
    """
    FANZA AV作品をキーワード検索する。

    sort: popular(rank) / newest(date)
    page: 1始まり。offset = (page-1)*hits + 1
    """
    keyword = keyword.strip()
    api_sort = normalize_fanza_sort(sort)
    page = max(1, int(page))
    hits = max(1, min(int(limit), 100))
    offset = (page - 1) * hits + 1

    if not keyword:
        return FanzaFetchResult(works=[], error=None, page=page, hits=hits, sort=api_sort)

    creds = _credentials()
    if creds is None:
        _debug("FANZA search skipped: credentials not configured")
        return FanzaFetchResult(
            works=[],
            error=CONFIG_ERROR,
            page=page,
            hits=hits,
            sort=api_sort,
        )

    api_id, affiliate_id_raw = creds
    affiliate_id = _api_affiliate_id(affiliate_id_raw)
    if affiliate_id != affiliate_id_raw:
        _debug(f"FANZA affiliate_id normalized to API suffix (...{affiliate_id[-3:]})")

    _debug(f"FANZA search keyword={keyword!r} sort={api_sort} page={page} offset={offset}")

    floors: tuple[str, ...]
    if floor and floor in FLOORS:
        floors = (floor,)
    elif page > 1:
        # ページ送り時は videoa を優先（初回と同じフロア想定）
        floors = ("videoa",)
    else:
        floors = FLOORS

    last_error: str | None = None
    raw_items: list[dict[str, Any]] = []
    total_count: int | None = None
    used_floor: str | None = None

    for candidate in floors:
        items, error, count = _request_items(
            api_id, affiliate_id, keyword, hits, candidate, api_sort, offset
        )
        if error:
            last_error = error
            if "API認証" in error:
                return FanzaFetchResult(
                    works=[],
                    error=error,
                    page=page,
                    hits=hits,
                    sort=api_sort,
                )
            continue
        total_count = count
        if items:
            raw_items = items
            used_floor = candidate
            break
        # 0件でも成功ならそのフロアの total を採用して終了（次フロアへは page1 のみ）
        if page == 1 and count == 0:
            continue
        if page > 1:
            used_floor = candidate
            break

    if not raw_items:
        if last_error:
            return FanzaFetchResult(
                works=[],
                error=last_error,
                total_count=total_count,
                page=page,
                hits=hits,
                sort=api_sort,
                floor=used_floor,
            )
        _debug("FANZA fetched count: 0")
        return FanzaFetchResult(
            works=[],
            error=None,
            total_count=total_count if total_count is not None else 0,
            page=page,
            hits=hits,
            sort=api_sort,
            floor=used_floor,
        )

    works = [_normalize_item(item, keyword) for item in raw_items]
    # 重複除外（content_id）しつつ元順維持
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for work in works:
        if not work.get("title") or work["title"] == "—":
            continue
        cid = str(work.get("content_id") or "")
        if cid and cid in seen:
            continue
        if cid:
            seen.add(cid)
        deduped.append(work)

    _debug(f"FANZA normalized count: {len(deduped)}")
    if deduped:
        titles = " | ".join(work["title"][:40] for work in deduped[:5])
        _debug(f"FANZA top titles: {titles}")

    return FanzaFetchResult(
        works=deduped,
        error=None,
        total_count=total_count,
        page=page,
        hits=hits,
        sort=api_sort,
        floor=used_floor,
    )
