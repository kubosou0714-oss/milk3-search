"""OpenAI による作品紹介文生成（作品IDキャッシュ付き）。"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ai_blurb_store import get_blurb, save_blurb

logger = logging.getLogger(__name__)

IS_VERCEL = os.environ.get("VERCEL") == "1"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
MAX_WORKERS = 2 if IS_VERCEL else 4
REQUEST_TIMEOUT = 15 if IS_VERCEL else 25
# Vercel の実行時間制限を避けるため、1リクエストあたりの新規生成数を抑える
MAX_NEW_GENERATIONS = 5 if IS_VERCEL else 12

SYSTEM_PROMPT = """あなたは同人・アダルト作品のレコメンドサイトの編集者です。
与えられた作品情報をもとに、ユーザーが「読んでみたい／見てみたい」と思える紹介文を日本語で書いてください。

厳守ルール:
- 敬体（です・ます）で書く
- 全体で100〜150文字程度（空白含む）
- 商品説明の丸写し禁止。要約・再構成する
- ネタバレ禁止（結末・重要な展開の暴露禁止）
- 性的表現は直接的・露骨にしすぎない
- 箇条書きや見出しは使わず、自然な文章1〜3文
- 作品タイトルを長く繰り返さない
- 「おすすめです」で無理に終わらせない（自然なら可）
- 出力は紹介文本文のみ。前置きや引用符は付けない
"""


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def template_reasons(keyword: str, title: str, circle_name: str) -> list[str]:
    reasons: list[str] = []
    if keyword and keyword in title:
        reasons.append(f"「{keyword}」のキーワードにマッチしています")
    if circle_name and circle_name != "—":
        reasons.append(f"サークル「{circle_name}」の作品です")
    reasons.append("DLsiteトレンド検索からのおすすめです")
    return reasons[:3]


def fanza_template_reasons(keyword: str, title: str, actress: str) -> list[str]:
    reasons: list[str] = []
    if keyword and keyword in title:
        reasons.append(f"「{keyword}」のキーワードにマッチしています")
    if actress and actress != "—":
        reasons.append(f"女優：{actress}")
    reasons.append("FANZA検索からのおすすめです")
    return reasons[:3]


def _normalize_blurb(text: str) -> str:
    cleaned = (text or "").strip().strip("「」\"'")
    cleaned = re.sub(r"[\r\n\t]+", "", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    if len(cleaned) > 180:
        cleaned = cleaned[:177] + "…"
    return cleaned


def _build_user_prompt(payload: dict[str, Any]) -> str:
    genres = payload.get("genres") or []
    tags = payload.get("tags") or []
    if isinstance(genres, str):
        genres = [genres]
    if isinstance(tags, str):
        tags = [tags]
    genre_text = "・".join(str(g) for g in genres if g) or "（不明）"
    tag_text = "・".join(str(t) for t in tags if t) or "（不明）"
    description = str(payload.get("description") or "").strip() or "（なし）"
    if len(description) > 800:
        description = description[:800] + "…"

    return (
        f"検索キーワード: {payload.get('keyword') or '（なし）'}\n"
        f"タイトル: {payload.get('title') or '（なし）'}\n"
        f"サークル名: {payload.get('circle_name') or '（なし）'}\n"
        f"ジャンル: {genre_text}\n"
        f"タグ: {tag_text}\n"
        f"商品説明: {description}\n"
    )


def _call_openai(payload: dict[str, Any]) -> str | None:
    api_key = _api_key()
    if not api_key:
        logger.info("AI blurb skipped: OPENAI_API_KEY missing")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.info("AI blurb skipped: openai package not installed")
        return None

    client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT)
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            temperature=0.7,
            max_tokens=220,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(payload)},
            ],
        )
    except Exception as exc:  # noqa: BLE001 — 生成失敗時はテンプレへフォールバック
        logger.info(
            "OpenAI blurb generation failed: %s: %s",
            type(exc).__name__,
            str(exc)[:180],
        )
        return None

    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, KeyError):
        logger.info("OpenAI blurb generation failed: empty choices")
        return None

    blurb = _normalize_blurb(content)
    if len(blurb) < 30:
        logger.info("OpenAI blurb generation failed: too short (%s chars)", len(blurb))
        return None
    return blurb


def get_or_create_blurb(work_key: str, payload: dict[str, Any]) -> str | None:
    """キャッシュがあれば返し、なければ生成して保存する。失敗時は None。"""
    cached = get_blurb(work_key)
    if cached:
        return cached

    blurb = _call_openai(payload)
    if not blurb:
        return None

    save_blurb(work_key, blurb, model=DEFAULT_MODEL)
    return blurb


def attach_doujin_reasons(works: list[Any], keyword: str) -> list[dict[str, Any]]:
    """WorkItem リストをカード用 dict にし、AI紹介文（なければテンプレ）を付与する。"""
    items: list[dict[str, Any]] = []
    pending: list[tuple[int, str, dict[str, Any]]] = []

    for index, work in enumerate(works):
        product_id = getattr(work, "product_id", "") or ""
        title = getattr(work, "title", "") or ""
        circle = getattr(work, "circle_name", "") or ""
        description = getattr(work, "description", "") or ""
        genres = list(getattr(work, "genres", []) or [])
        tags = list(getattr(work, "tags", []) or [])
        work_key = f"dlsite:{product_id}" if product_id else ""

        card = {
            "title": title,
            "circle_name": circle,
            "price": getattr(work, "price", "—"),
            "image_url": getattr(work, "thumbnail_url", ""),
            "url": getattr(work, "product_url", ""),
            "reasons": template_reasons(keyword, title, circle),
        }
        items.append(card)

        if not work_key or not _api_key():
            continue

        cached = get_blurb(work_key)
        if cached:
            card["reasons"] = [cached]
            continue

        pending.append(
            (
                index,
                work_key,
                {
                    "keyword": keyword,
                    "title": title,
                    "circle_name": circle,
                    "description": description,
                    "genres": genres,
                    "tags": tags,
                },
            )
        )

    if not _api_key():
        logger.info("AI blurbs: OPENAI_API_KEY missing; using templates")
    elif not pending:
        logger.info("AI blurbs: all cached or nothing to generate")
    else:
        pending = pending[:MAX_NEW_GENERATIONS]
        logger.info(
            "AI blurbs: generating %s new (model=%s)",
            len(pending),
            DEFAULT_MODEL,
        )

    if not pending:
        return items

    def _job(entry: tuple[int, str, dict[str, Any]]) -> tuple[int, str | None]:
        idx, key, payload = entry
        return idx, get_or_create_blurb(key, payload)

    ok_count = 0
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending))) as pool:
        futures = [pool.submit(_job, entry) for entry in pending]
        for future in as_completed(futures):
            try:
                idx, blurb = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.info("AI blurb worker failed: %s", type(exc).__name__)
                continue
            if blurb:
                items[idx]["reasons"] = [blurb]
                ok_count += 1
    logger.info("AI blurbs: generated_ok=%s / attempted=%s", ok_count, len(pending))

    return items


def attach_fanza_reasons(works: list[dict[str, Any]], keyword: str) -> list[dict[str, Any]]:
    if not works:
        return works

    pending: list[tuple[int, str, dict[str, Any]]] = []

    for index, work in enumerate(works):
        content_id = str(work.get("content_id") or work.get("product_id") or "").strip()
        title = str(work.get("title") or "")
        actress = str(work.get("actress") or "")
        maker = str(work.get("maker") or "")
        genre = str(work.get("genre") or "")
        work_key = f"fanza:{content_id}" if content_id else ""

        # 既存テンプレを一旦セット
        work["reasons"] = fanza_template_reasons(keyword, title, actress)

        if not work_key or not _api_key():
            continue

        cached = get_blurb(work_key)
        if cached:
            work["reasons"] = [cached]
            continue

        pending.append(
            (
                index,
                work_key,
                {
                    "keyword": keyword,
                    "title": title,
                    "circle_name": maker if maker != "—" else actress,
                    "description": "",
                    "genres": [g for g in genre.split("・") if g and g != "—"],
                    "tags": [actress] if actress and actress != "—" else [],
                },
            )
        )

    if not pending:
        return works

    def _job(entry: tuple[int, str, dict[str, Any]]) -> tuple[int, str | None]:
        idx, key, payload = entry
        return idx, get_or_create_blurb(key, payload)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(pending))) as pool:
        futures = [pool.submit(_job, entry) for entry in pending]
        for future in as_completed(futures):
            try:
                idx, blurb = future.result()
            except Exception:  # noqa: BLE001
                continue
            if blurb:
                works[idx]["reasons"] = [blurb]

    return works
