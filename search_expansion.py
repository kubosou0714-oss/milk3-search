"""辞書ベースの検索意図解析・拡張・スコアリング。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dlsite_scraper import WorkItem, build_search_url, fetch_works

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DICTIONARY_PATH = os.path.join(BASE_DIR, "data", "fetish_dictionary.json")

IS_VERCEL = os.environ.get("VERCEL") == "1"
MAX_SEARCH_QUERIES = 4 if IS_VERCEL else 5
RESULTS_PER_QUERY = 12 if IS_VERCEL else 20
MAX_TOTAL_FETCHED = 30 if IS_VERCEL else 50
MAX_DISPLAY_RESULTS = 30 if IS_VERCEL else 50
INITIAL_DISPLAY_COUNT = 10
LOAD_MORE_STEP = 10

PRIMARY_CATEGORIES = {"character", "mind_control"}
REQUIRED_CATEGORIES = {"situation", "act"}
OPTIONAL_CATEGORIES = {"outcome", "mood"}

SCORE_PRIMARY_EXACT_TITLE = 120
SCORE_PRIMARY_ALIAS_TITLE = 110
SCORE_PRIMARY_STRONG_TITLE = 100
SCORE_PRIMARY_TAG = 100
SCORE_PRIMARY_META = 70
SCORE_REQUIRED_TITLE = 90
SCORE_REQUIRED_META = 50
SCORE_OPTIONAL = 25
SCORE_RELATED = 8
SCORE_TREND = 3

MIND_CONTROL_SEARCH_QUERIES = ["催眠", "洗脳", "暗示", "常識改変", "精神操作"]

MIN_SCORE_NORMAL = 40
MIN_SCORE_LONG = 60
MIN_RESULTS_BEFORE_FALLBACK = 5
FALLBACK_MIN_SCORE = 15
LONG_QUERY_LEN = 15


@dataclass
class DictionaryEntry:
    key: str
    category: str
    priority: int
    concept_group: str = ""
    aliases: list[str] = field(default_factory=list)
    strong_aliases: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)


@dataclass
class TextMatch:
    entry: DictionaryEntry
    matched_text: str
    match_kind: str  # "key" | "alias"


@dataclass
class SearchIntent:
    query: str
    primary_intent: list[str] = field(default_factory=list)
    required_keywords: list[str] = field(default_factory=list)
    optional_keywords: list[str] = field(default_factory=list)
    ignored_words: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    related_keywords: list[str] = field(default_factory=list)
    strong_aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primaryIntent": self.primary_intent,
            "requiredKeywords": self.required_keywords,
            "optionalKeywords": self.optional_keywords,
            "ignoredWords": self.ignored_words,
            "searchQueries": self.search_queries,
            "relatedKeywords": self.related_keywords,
            "strongAliases": self.strong_aliases,
        }


@dataclass
class ScoredWork:
    work: WorkItem
    score: int
    has_primary: bool
    has_required: bool
    has_optional: bool
    has_related: bool
    score_reason: str
    fetch_order: int = 9999
    is_fallback: bool = False
    matched_terms: list[str] = field(default_factory=list)

    @property
    def has_content_match(self) -> bool:
        return self.has_primary or self.has_required or self.has_optional

    @property
    def is_related_only(self) -> bool:
        return self.has_related and not self.has_content_match

    @property
    def is_trend_only(self) -> bool:
        return not self.has_content_match and not self.has_related


def _load_dictionary() -> tuple[list[DictionaryEntry], list[str]]:
    with open(DICTIONARY_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    ignored = payload.get("ignored", [])
    entries = [
        DictionaryEntry(
            key=item["key"],
            category=item.get("category", "outcome"),
            priority=int(item.get("priority", 50)),
            concept_group=item.get("conceptGroup", ""),
            aliases=item.get("aliases", []),
            strong_aliases=item.get("strongAliases", []),
            related=item.get("related", []),
        )
        for item in payload.get("entries", [])
    ]
    return entries, ignored


def _entry_by_key(entries: list[DictionaryEntry]) -> dict[str, DictionaryEntry]:
    return {e.key: e for e in entries}


def _term_index(entries: list[DictionaryEntry]) -> dict[str, DictionaryEntry]:
    index: dict[str, DictionaryEntry] = {}
    for entry in entries:
        index[entry.key] = entry
        for alias in entry.aliases:
            if alias not in index:
                index[alias] = entry
        for strong in entry.strong_aliases:
            if strong not in index:
                index[strong] = entry
    return index


def _primary_compatible_terms(entry: DictionaryEntry) -> list[str]:
    return [entry.key, *entry.aliases, *entry.strong_aliases]


def _concept_group_entries(entries: list[DictionaryEntry], group: str) -> list[DictionaryEntry]:
    if not group:
        return []
    return [e for e in entries if e.concept_group == group]


def _intent_primary_entries(intent: SearchIntent, entries: list[DictionaryEntry]) -> list[DictionaryEntry]:
    by_key = _entry_by_key(entries)
    seen: set[str] = set()
    result: list[DictionaryEntry] = []
    for pkey in intent.primary_intent:
        entry = by_key.get(pkey)
        if not entry or entry.key in seen:
            continue
        seen.add(entry.key)
        if entry.concept_group:
            for ge in _concept_group_entries(entries, entry.concept_group):
                if ge.key not in seen:
                    seen.add(ge.key)
                    result.append(ge)
        else:
            result.append(entry)
    return result


def _mind_control_search_queries(matched_term: str | None = None) -> list[str]:
    queries = list(MIND_CONTROL_SEARCH_QUERIES)
    if matched_term and matched_term in queries:
        queries = [matched_term] + [q for q in queries if q != matched_term]
    return queries[:MAX_SEARCH_QUERIES]


def _collect_strong_aliases(entries: list[DictionaryEntry], primary_keys: list[str]) -> list[str]:
    by_key = _entry_by_key(entries)
    terms: list[str] = []
    seen: set[str] = set()
    for pkey in primary_keys:
        entry = by_key.get(pkey)
        if not entry:
            continue
        for term in entry.strong_aliases:
            if term not in seen:
                seen.add(term)
                terms.append(term)
    return terms


def _find_ignored(query: str, ignored: list[str]) -> list[str]:
    found: list[str] = []
    for word in sorted(ignored, key=len, reverse=True):
        if word and word in query and word not in found:
            found.append(word)
    return found


def _find_text_matches(query: str, entries: list[DictionaryEntry]) -> list[TextMatch]:
    """文中に直接含まれる key / alias / strongAlias のみ検出。"""
    matches: list[TextMatch] = []

    for entry in entries:
        if entry.key in query:
            matches.append(TextMatch(entry=entry, matched_text=entry.key, match_kind="key"))
            continue
        matched = False
        for alias in sorted(entry.aliases, key=len, reverse=True):
            if alias in query:
                matches.append(TextMatch(entry=entry, matched_text=alias, match_kind="alias"))
                matched = True
                break
        if matched:
            continue
        for strong in sorted(entry.strong_aliases, key=len, reverse=True):
            if strong in query:
                matches.append(TextMatch(entry=entry, matched_text=strong, match_kind="strong"))
                break

    return matches


def analyze_search_intent(query: str) -> SearchIntent:
    """入力文から検索意図を解析する。"""
    query = query.strip()
    entries, ignored_list = _load_dictionary()
    by_key = _entry_by_key(entries)

    intent = SearchIntent(query=query)
    intent.ignored_words = _find_ignored(query, ignored_list)

    matches = _find_text_matches(query, entries)
    if not matches and query in by_key:
        matches = [TextMatch(entry=by_key[query], matched_text=query, match_kind="key")]

    is_short = len(query) <= LONG_QUERY_LEN and len(matches) <= 1

    if is_short and (query in by_key or matches):
        main_entry = by_key.get(query) or matches[0].entry
        matched_term = query if query in _primary_compatible_terms(main_entry) else matches[0].matched_text
        primary = [main_entry.key]
        required = []
        optional = []
        if main_entry.category in OPTIONAL_CATEGORIES:
            for alias in main_entry.aliases[:2]:
                if alias not in optional:
                    optional.append(alias)
        intent.primary_intent = primary
        intent.required_keywords = required
        intent.optional_keywords = optional
        intent.strong_aliases = _collect_strong_aliases(entries, primary)
        related_pool = [rel for rel in main_entry.related if rel not in primary]
        intent.related_keywords = related_pool
        if main_entry.concept_group == "mind_control":
            intent.search_queries = _mind_control_search_queries(matched_term)
        else:
            queries: list[str] = []
            seen_q: set[str] = set()

            def add_q(term: str) -> None:
                if term and term not in seen_q:
                    seen_q.add(term)
                    queries.append(term)

            add_q(main_entry.key)
            for alias in main_entry.aliases:
                if len(queries) >= MAX_SEARCH_QUERIES:
                    break
                add_q(alias)
            intent.search_queries = queries[:MAX_SEARCH_QUERIES]
        intent.ignored_words = _find_ignored(query, ignored_list)
        return intent

    primary: list[str] = []
    required: list[str] = []
    optional: list[str] = []

    for m in matches:
        key = m.entry.key
        if m.entry.category in PRIMARY_CATEGORIES and key not in primary:
            primary.append(key)

    for m in matches:
        key = m.entry.key
        if key in primary:
            continue
        if m.entry.category in REQUIRED_CATEGORIES and key not in required:
            required.append(key)
        elif m.entry.category in OPTIONAL_CATEGORIES and key not in optional:
            optional.append(key)

    if not primary and required:
        primary.append(required.pop(0))

    if not primary and optional:
        promoted = max(optional, key=lambda k: by_key[k].priority if k in by_key else 0)
        optional = [o for o in optional if o != promoted]
        primary.append(promoted)

    for pkey in list(primary):
        entry = by_key[pkey]
        for alias in entry.aliases:
            if alias in query and alias != entry.key and alias not in required and alias not in optional:
                required.append(alias)
        for strong in entry.strong_aliases:
            if strong in query and strong not in primary and strong not in required and strong not in optional:
                pass  # strongAlias は primary 互換。required には入れない

    ordered_optional: list[str] = []
    for pkey in primary:
        entry = by_key[pkey]
        for rel in entry.related:
            if rel in by_key and rel not in primary and rel not in required:
                rel_entry = by_key[rel]
                if rel_entry.category in OPTIONAL_CATEGORIES and rel not in ordered_optional:
                    ordered_optional.append(rel)
    for o in optional:
        if o not in ordered_optional:
            ordered_optional.append(o)
    optional = ordered_optional

    intent.primary_intent = primary
    intent.required_keywords = required
    intent.optional_keywords = optional
    intent.strong_aliases = _collect_strong_aliases(entries, primary)

    related_pool: list[str] = []
    for pkey in primary:
        entry = by_key[pkey]
        for rel in entry.related:
            if rel not in primary and rel not in required and rel not in optional:
                related_pool.append(rel)
    intent.related_keywords = list(dict.fromkeys(related_pool))

    queries: list[str] = []
    seen_q: set[str] = set()

    def add_query(term: str) -> None:
        if term and term not in seen_q:
            seen_q.add(term)
            queries.append(term)

    mind_control_keys = [
        k for k in primary if by_key.get(k) and by_key[k].concept_group == "mind_control"
    ]
    if mind_control_keys and len(primary) == 1:
        matched = next(
            (m.matched_text for m in matches if m.entry.concept_group == "mind_control"),
            mind_control_keys[0],
        )
        intent.search_queries = _mind_control_search_queries(matched)
        return intent

    for k in primary:
        add_query(k)
    if mind_control_keys:
        matched = next(
            (m.matched_text for m in matches if m.entry.concept_group == "mind_control"),
            None,
        )
        for q in _mind_control_search_queries(matched):
            if len(queries) >= MAX_SEARCH_QUERIES:
                break
            add_query(q)
    for k in required:
        add_query(k)
    detected_optional = [
        o for o in optional
        if o in query or (o in by_key and any(a in query for a in by_key[o].aliases))
    ]
    related_optional = [o for o in optional if o not in detected_optional]
    for k in detected_optional:
        if len(queries) >= MAX_SEARCH_QUERIES:
            break
        add_query(k)
    if related_optional and len(queries) < MAX_SEARCH_QUERIES:
        if len(queries) < len(primary) + len(required) + 1:
            add_query(related_optional[0])

    if not queries and query:
        add_query(query)

    intent.search_queries = queries[:MAX_SEARCH_QUERIES]
    return intent


def _work_text(work: WorkItem) -> str:
    return f"{work.title}\n{work.circle_name}"


def _term_in_title(term: str, title: str) -> bool:
    return bool(term and term in title)


def _term_in_meta(term: str, text: str, title: str) -> bool:
    return bool(term and term in text and term not in title)


def _entry_terms(entry: DictionaryEntry) -> list[str]:
    return _primary_compatible_terms(entry)


def _score_primary_for_entry(
    entry: DictionaryEntry,
    title: str,
    circle: str,
    text: str,
) -> tuple[int, bool, list[str]]:
    """primary / alias / strongAlias / conceptGroup 代表語の一致をスコアリング。"""
    if _term_in_title(entry.key, title):
        return SCORE_PRIMARY_EXACT_TITLE, True, [
            f"primary exact title match: {entry.key} +{SCORE_PRIMARY_EXACT_TITLE}"
        ]

    for alias in entry.aliases:
        if _term_in_title(alias, title):
            return SCORE_PRIMARY_ALIAS_TITLE, True, [
                f"primary alias title match: {alias} +{SCORE_PRIMARY_ALIAS_TITLE}"
            ]

    for strong in entry.strong_aliases:
        if _term_in_title(strong, title):
            return SCORE_PRIMARY_STRONG_TITLE, True, [
                f"strongAlias title match: {strong} +{SCORE_PRIMARY_STRONG_TITLE}"
            ]

    for term in _primary_compatible_terms(entry):
        if circle and circle != "—" and term in circle:
            return SCORE_PRIMARY_TAG, True, [
                f"primary tag match: {term} +{SCORE_PRIMARY_TAG}"
            ]

    for term in _primary_compatible_terms(entry):
        if _term_in_meta(term, text, title):
            return SCORE_PRIMARY_META, True, [
                f"primary description match: {term} +{SCORE_PRIMARY_META}"
            ]

    return 0, False, []


def _is_long_search(intent: SearchIntent) -> bool:
    return (
        len(intent.query) > LONG_QUERY_LEN
        or len(intent.primary_intent) > 1
        or bool(intent.required_keywords)
    )


def _min_display_score(intent: SearchIntent) -> int:
    return MIN_SCORE_LONG if _is_long_search(intent) else MIN_SCORE_NORMAL


def _score_work(work: WorkItem, intent: SearchIntent, search_term: str) -> ScoredWork:
    entries, _ = _load_dictionary()
    by_key = _entry_by_key(entries)
    title = work.title
    circle = work.circle_name
    text = _work_text(work)
    reasons: list[str] = []
    score = 0
    has_primary = False
    has_required = False
    has_optional = False
    has_related = False

    primary_entries = _intent_primary_entries(intent, entries)
    if not primary_entries:
        for pkey in intent.primary_intent:
            entry = by_key.get(pkey)
            if entry:
                primary_entries.append(entry)

    for entry in primary_entries:
        pts, matched, preasons = _score_primary_for_entry(entry, title, circle, text)
        if matched:
            score += pts
            has_primary = True
            reasons.extend(preasons)
            break

    for rkey in intent.required_keywords:
        entry = by_key.get(rkey)
        terms = _entry_terms(entry) if entry else [rkey]
        for term in terms:
            if _term_in_title(term, title):
                score += SCORE_REQUIRED_TITLE
                has_required = True
                reasons.append(f"requiredKeyword title match: {term} +{SCORE_REQUIRED_TITLE}")
                break
            if circle and circle != "—" and term in circle:
                score += SCORE_REQUIRED_META + 30
                has_required = True
                reasons.append(f"requiredKeyword tag match: {term} +{SCORE_REQUIRED_META + 30}")
                break
            if _term_in_meta(term, text, title):
                score += SCORE_REQUIRED_META
                has_required = True
                reasons.append(f"requiredKeyword description match: {term} +{SCORE_REQUIRED_META}")
                break

    matched_optional: set[str] = set()
    for okey in intent.optional_keywords:
        entry = by_key.get(okey)
        terms = _entry_terms(entry) if entry else [okey]
        for term in terms:
            if term in matched_optional:
                continue
            if term in title or (term in text and term not in title):
                score += SCORE_OPTIONAL
                has_optional = True
                matched_optional.add(term)
                reasons.append(f"optionalKeyword match: {term} +{SCORE_OPTIONAL}")
                break

    for rkey in intent.related_keywords:
        if rkey in title or (rkey in text and rkey not in title):
            score += SCORE_RELATED
            has_related = True
            reasons.append(f"relatedKeyword match: {rkey} +{SCORE_RELATED}")
            break

    score += SCORE_TREND
    reasons.append(f"DLsite trend/recommendation: {search_term} +{SCORE_TREND}")

    reason = "; ".join(reasons)
    return ScoredWork(
        work=work,
        score=score,
        has_primary=has_primary,
        has_required=has_required,
        has_optional=has_optional,
        has_related=has_related,
        score_reason=reason,
        matched_terms=[search_term],
    )


def _can_fallback(item: ScoredWork) -> bool:
    """required / optional / related のいずれかがあれば fallback 候補（trend のみは除外）。"""
    if item.is_trend_only:
        return False
    return item.has_required or item.has_optional or item.has_related


def _apply_result_filter(ranked: list[ScoredWork], intent: SearchIntent) -> list[ScoredWork]:
    """最低スコア・primary 一致で足切り。件数不足時のみ fallback を下位に追加。"""
    min_score = _min_display_score(intent)
    main: list[ScoredWork] = []
    fallback: list[ScoredWork] = []

    for item in ranked:
        if item.is_trend_only:
            continue

        if intent.primary_intent and not item.has_primary:
            if _can_fallback(item):
                fallback.append(item)
            continue

        if item.is_related_only:
            fallback.append(item)
            continue

        if item.score < min_score and not item.has_primary:
            if _can_fallback(item):
                fallback.append(item)
            continue

        main.append(item)

    if len(main) < MIN_RESULTS_BEFORE_FALLBACK:
        seen_ids = {s.work.product_id for s in main}
        for item in sorted(fallback, key=_sort_scored_work):
            if len(main) >= MIN_RESULTS_BEFORE_FALLBACK:
                break
            if item.work.product_id in seen_ids:
                continue
            item.is_fallback = True
            item.score_reason = f"[fallback] {item.score_reason}"
            main.append(item)
            seen_ids.add(item.work.product_id)

    return sorted(main, key=_sort_scored_work)[:MAX_DISPLAY_RESULTS]


def _sort_scored_work(item: ScoredWork) -> tuple:
    """スコア優先、同帯域は DLsite 取得順を維持。"""
    score_band = -(item.score // 10)
    return (item.is_fallback, not item.has_primary, not item.has_required, score_band, item.fetch_order)


def _merge_scored(existing: ScoredWork | None, incoming: ScoredWork) -> ScoredWork:
    if existing is None:
        return incoming
    incoming.fetch_order = min(existing.fetch_order, incoming.fetch_order)
    if (incoming.has_primary, incoming.has_required, incoming.score) > (
        existing.has_primary,
        existing.has_required,
        existing.score,
    ):
        incoming.matched_terms = list(dict.fromkeys(existing.matched_terms + incoming.matched_terms))
        incoming.has_required = existing.has_required or incoming.has_required
        incoming.has_optional = existing.has_optional or incoming.has_optional
        incoming.has_related = existing.has_related or incoming.has_related
        return incoming
    existing.matched_terms = list(dict.fromkeys(existing.matched_terms + incoming.matched_terms))
    existing.has_primary = existing.has_primary or incoming.has_primary
    existing.has_required = existing.has_required or incoming.has_required
    existing.has_optional = existing.has_optional or incoming.has_optional
    existing.has_related = existing.has_related or incoming.has_related
    existing.fetch_order = min(existing.fetch_order, incoming.fetch_order)
    if incoming.score > existing.score:
        existing.score = incoming.score
        existing.score_reason = incoming.score_reason
    return existing


def _log_search_debug(
    intent: SearchIntent,
    request_count: int,
    total_fetched: int,
    ranked: list[ScoredWork],
) -> None:
    logger.info("search debug: user input=%r", intent.query)
    logger.info("  primaryIntent=%s", intent.primary_intent)
    logger.info("  strongAliases=%s", intent.strong_aliases)
    logger.info("  requiredKeywords=%s", intent.required_keywords)
    logger.info("  optionalKeywords=%s", intent.optional_keywords)
    logger.info("  ignoredWords=%s", intent.ignored_words)
    logger.info("  final searchQueries=%s", intent.search_queries)
    logger.info("  minDisplayScore=%d", _min_display_score(intent))
    logger.info("  total fetched count=%d", total_fetched)
    logger.info("  filtered result count=%d", len(ranked))
    logger.info("  initially displayed count=%d", min(INITIAL_DISPLAY_COUNT, len(ranked)))
    logger.info("  max display count=%d", MAX_DISPLAY_RESULTS)
    logger.info("  number of DLsite requests=%d", request_count)
    for i, item in enumerate(ranked[:10], 1):
        logger.info(
            "  top #%d score=%d primary=%s fallback=%s title=%r",
            i,
            item.score,
            item.has_primary,
            item.is_fallback,
            item.work.title[:60],
        )
        logger.info("    scoreReason: %s", item.score_reason)


def _rank_works(query: str) -> tuple[list[ScoredWork], str, str | None, SearchIntent]:
    intent = analyze_search_intent(query)
    search_url = build_search_url(query)

    if not intent.search_queries and query:
        intent.search_queries = [query]

    merged: dict[str, ScoredWork] = {}
    last_error: str | None = None
    request_count = 0
    fetch_order = 0
    total_fetched = 0

    for term in intent.search_queries:
        if total_fetched >= MAX_TOTAL_FETCHED:
            break
        request_count += 1
        works, _, error = fetch_works(term, per_page=RESULTS_PER_QUERY)
        if not works and error:
            last_error = error

        for work in works[:RESULTS_PER_QUERY]:
            pid = work.product_id
            is_new = pid not in merged
            if is_new and total_fetched >= MAX_TOTAL_FETCHED:
                break
            fetch_order += 1
            if is_new:
                total_fetched += 1
            scored = _score_work(work, intent, term)
            scored.fetch_order = fetch_order
            merged[pid] = _merge_scored(merged.get(pid), scored)

    if not merged:
        return [], search_url, last_error or "検索結果が 0 件でした。キーワードを変えて試してください。", intent

    ranked = sorted(merged.values(), key=_sort_scored_work)
    filtered = _apply_result_filter(ranked, intent)

    _log_search_debug(intent, request_count, total_fetched, filtered)
    return filtered, search_url, None, intent


def fetch_expanded_works(
    query: str,
) -> tuple[list[ScoredWork], str, str | None, list[str]]:
    query = query.strip()
    if not query:
        return [], build_search_url(""), "キーワードを入力してください。", []

    ranked, search_url, error, intent = _rank_works(query)
    if error:
        return [], search_url, error, intent.search_queries

    return ranked, search_url, None, intent.search_queries
