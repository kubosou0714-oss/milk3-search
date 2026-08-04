"""辞書ベースの検索語拡張とスコアリング。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from dlsite_scraper import (
    FetchResult,
    WorkItem,
    build_search_url,
    fetch_works,
    normalize_dlsite_order,
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DICTIONARY_PATH = os.path.join(BASE_DIR, "data", "fetish_dictionary.json")

IS_VERCEL = os.environ.get("VERCEL") == "1"
MAX_SEARCH_TERMS = 6 if IS_VERCEL else 8
MAX_ALIASES_PER_ENTRY = 4 if IS_VERCEL else 6
MAX_RELATED_PER_ENTRY = 2
LONG_QUERY_LEN = 40

SCORE_ORIGINAL_TITLE = 100
SCORE_ORIGINAL_TAG = 80
SCORE_ORIGINAL_DESC = 50
SCORE_ALIAS = 40
SCORE_RELATED = 15
SCORE_TREND_ONLY = 5


@dataclass
class DictionaryEntry:
    key: str
    aliases: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)

    def alias_terms(self) -> list[str]:
        return list(self.aliases)

    def related_terms(self) -> list[str]:
        return list(self.related)


@dataclass
class DetectedMatch:
    entry: DictionaryEntry
    matched_text: str
    match_kind: str  # "key" | "alias" | "related"


@dataclass
class ScoredWork:
    work: WorkItem
    score: int
    tier: int
    score_reason: str
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class SearchContext:
    query: str
    primary_keys: set[str]
    alias_terms: set[str]
    related_terms: set[str]
    detected: list[DetectedMatch]


def _load_dictionary() -> list[DictionaryEntry]:
    with open(DICTIONARY_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    return [
        DictionaryEntry(
            key=item["key"],
            aliases=item.get("aliases", []),
            related=item.get("related", []),
        )
        for item in payload.get("entries", [])
    ]


def _term_index(entries: list[DictionaryEntry]) -> dict[str, DictionaryEntry]:
    index: dict[str, DictionaryEntry] = {}
    for entry in entries:
        index[entry.key] = entry
        for alias in entry.aliases:
            if alias not in index:
                index[alias] = entry
    return index


def _primary_key_index(entries: list[DictionaryEntry]) -> dict[str, str]:
    """語 → その語が primary key であるエントリの key。"""
    mapping: dict[str, str] = {}
    for entry in entries:
        mapping[entry.key] = entry.key
    return mapping


def detect_terms(query: str, entries: list[DictionaryEntry] | None = None) -> list[DetectedMatch]:
    """入力文から辞書に登録された語を検出する（key / alias 優先）。"""
    if entries is None:
        entries = _load_dictionary()

    primary_keys = _primary_key_index(entries)
    matches: list[DetectedMatch] = []
    seen_entries: set[str] = set()
    is_long = len(query.strip()) > LONG_QUERY_LEN

    key_alias_terms: list[tuple[str, DictionaryEntry, str]] = []
    for entry in entries:
        key_alias_terms.append((entry.key, entry, "key"))
        for alias in entry.aliases:
            key_alias_terms.append((alias, entry, "alias"))

    key_alias_terms.sort(key=lambda item: len(item[0]), reverse=True)

    for term, entry, kind in key_alias_terms:
        if entry.key in seen_entries:
            continue
        if term and term in query:
            matches.append(DetectedMatch(entry=entry, matched_text=term, match_kind=kind))
            seen_entries.add(entry.key)

    if is_long:
        related_terms: list[tuple[str, DictionaryEntry, str]] = []
        for entry in entries:
            if entry.key in seen_entries:
                continue
            for related in entry.related:
                related_terms.append((related, entry, "related"))
        related_terms.sort(key=lambda item: len(item[0]), reverse=True)

        for term, entry, kind in related_terms:
            if entry.key in seen_entries:
                continue
            if not term or term not in query:
                continue
            if term in primary_keys and primary_keys[term] != entry.key:
                continue
            matches.append(DetectedMatch(entry=entry, matched_text=term, match_kind=kind))
            seen_entries.add(entry.key)

    return matches


def _resolve_primary_entries(
    query: str, detected: list[DetectedMatch], entries: list[DictionaryEntry]
) -> list[DictionaryEntry]:
    if detected:
        return [m.entry for m in detected]

    index = _term_index(entries)
    entry = index.get(query)
    return [entry] if entry else []


def build_search_terms(query: str) -> tuple[list[str], list[DetectedMatch], SearchContext]:
    """検索に使う語リストとスコアリング用コンテキストを組み立てる。"""
    query = query.strip()
    entries = _load_dictionary()
    detected = detect_terms(query, entries)
    primary_entries = _resolve_primary_entries(query, detected, entries)
    is_long = len(query) > LONG_QUERY_LEN or len(detected) > 1 or len(detected) > 1

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        t = term.strip()
        if t and t not in seen:
            seen.add(t)
            terms.append(t)

    primary_keys: set[str] = set()
    alias_terms: set[str] = set()
    related_terms: set[str] = set()

    if primary_entries:
        if is_long:
            terms.clear()
            seen.clear()
            for entry in primary_entries:
                primary_keys.add(entry.key)
                add(entry.key)
            for entry in primary_entries:
                for alias in entry.alias_terms()[:2]:
                    alias_terms.add(alias)
                    add(alias)
            for entry in primary_entries:
                for related in entry.related_terms()[:MAX_RELATED_PER_ENTRY]:
                    related_terms.add(related)
                    add(related)
        else:
            for entry in primary_entries:
                primary_keys.add(entry.key)
                add(entry.key)
                for alias in entry.alias_terms()[:MAX_ALIASES_PER_ENTRY]:
                    alias_terms.add(alias)
                    add(alias)
    else:
        add(query)

    ctx = SearchContext(
        query=query,
        primary_keys=primary_keys,
        alias_terms=alias_terms,
        related_terms=related_terms,
        detected=detected,
    )
    return terms[:MAX_SEARCH_TERMS], detected, ctx


def _work_text(work: WorkItem) -> str:
    return f"{work.title}\n{work.circle_name}"


def _score_work(work: WorkItem, ctx: SearchContext, search_term: str) -> ScoredWork:
    text = _work_text(work)
    title = work.title
    reasons: list[str] = []
    score = 0
    tier = 0

    original_terms = {ctx.query, *ctx.primary_keys}

    for term in sorted(original_terms, key=len, reverse=True):
        if term and term in title:
            score += SCORE_ORIGINAL_TITLE
            tier = max(tier, 3)
            reasons.append(f"original keyword '{term}' in title (+{SCORE_ORIGINAL_TITLE})")
            break
        if term and term in text and term not in title:
            score += SCORE_ORIGINAL_DESC
            tier = max(tier, 2)
            reasons.append(f"original keyword '{term}' in metadata (+{SCORE_ORIGINAL_DESC})")
            break

    if tier < 3:
        for alias in sorted(ctx.alias_terms, key=len, reverse=True):
            if alias in title:
                score += SCORE_ALIAS
                tier = max(tier, 2)
                reasons.append(f"alias '{alias}' in title (+{SCORE_ALIAS})")
                break
            if alias in text:
                score += SCORE_ALIAS // 2
                tier = max(tier, 2)
                reasons.append(f"alias '{alias}' in metadata (+{SCORE_ALIAS // 2})")
                break

    if tier < 2 and ctx.related_terms:
        for related in sorted(ctx.related_terms, key=len, reverse=True):
            if related in title:
                score += SCORE_RELATED
                tier = max(tier, 1)
                reasons.append(f"related '{related}' in title (+{SCORE_RELATED})")
                break

    if search_term in ctx.primary_keys or search_term == ctx.query:
        reasons.append(f"found via primary search '{search_term}' (+{SCORE_TREND_ONLY})")
    elif search_term in ctx.alias_terms:
        reasons.append(f"found via alias search '{search_term}' (+{SCORE_TREND_ONLY})")
    elif search_term in ctx.related_terms:
        reasons.append(f"found via related search '{search_term}' (+{SCORE_TREND_ONLY})")
    else:
        reasons.append(f"found via other search '{search_term}' (+{SCORE_TREND_ONLY})")

    score += SCORE_TREND_ONLY

    if tier == 0:
        reasons.append("no primary/alias/related match in title (low relevance)")

    if tier == 0 and search_term not in ctx.primary_keys and search_term not in ctx.alias_terms:
        score = min(score, SCORE_RELATED)

    reason = "; ".join(reasons)
    return ScoredWork(
        work=work,
        score=score,
        tier=tier,
        score_reason=reason,
        matched_terms=[search_term],
    )


def _merge_scored(existing: ScoredWork | None, incoming: ScoredWork) -> ScoredWork:
    if existing is None:
        return incoming
    if (incoming.tier, incoming.score) > (existing.tier, existing.score):
        merged_terms = list(dict.fromkeys(existing.matched_terms + incoming.matched_terms))
        incoming.matched_terms = merged_terms
        return incoming
    existing.matched_terms = list(dict.fromkeys(existing.matched_terms + incoming.matched_terms))
    return existing


def _log_top_results(query: str, ranked: list[ScoredWork], limit: int = 5) -> None:
    logger.info("search debug: query=%r top %d results", query, limit)
    for i, item in enumerate(ranked[:limit], 1):
        logger.info(
            "  #%d tier=%d score=%d title=%r reason=%s",
            i,
            item.tier,
            item.score,
            item.work.title[:60],
            item.score_reason,
        )


def _rank_works(query: str) -> tuple[list[ScoredWork], str, str | None, list[str]]:
    search_terms, _detected, ctx = build_search_terms(query)
    search_url = build_search_url(query)

    merged: dict[str, ScoredWork] = {}
    last_error: str | None = None

    for term in search_terms:
        result = fetch_works(term)
        if not result.works and result.error:
            last_error = result.error

        for work in result.works:
            pid = work.product_id
            scored = _score_work(work, ctx, term)
            merged[pid] = _merge_scored(merged.get(pid), scored)

    if not merged:
        return [], search_url, last_error or "検索結果が 0 件でした。キーワードを変えて試してください。", search_terms

    ranked = sorted(merged.values(), key=lambda s: (s.tier, s.score), reverse=True)
    return ranked, search_url, None, search_terms


def fetch_expanded_works(
    query: str,
) -> tuple[list[WorkItem], str, str | None, list[str]]:
    """
    辞書拡張＋スコアリングで作品一覧を取得する。

    Returns:
        (作品一覧, 検索ページURL, エラー or None, 使用した検索語)
    """
    query = query.strip()
    if not query:
        return [], build_search_url(""), "キーワードを入力してください。", []

    ranked, search_url, error, search_terms = _rank_works(query)
    if error:
        return [], search_url, error, search_terms

    _log_top_results(query, ranked)
    return [s.work for s in ranked], search_url, None, search_terms


def fetch_source_ordered_works(
    query: str,
    sort: str = "popular",
    page: int = 1,
    per_page: int | None = None,
) -> FetchResult:
    """
    DLsite の並び順をそのまま使う（拡張スコアなし）。
    sort: popular / newest
    """
    query = query.strip()
    order = normalize_dlsite_order(sort)
    if not query:
        return FetchResult(
            works=[],
            search_url=build_search_url("", order=order),
            error="キーワードを入力してください。",
            page=max(1, int(page)),
            order=order,
        )
    return fetch_works(query, page=page, order=order, per_page=per_page)
