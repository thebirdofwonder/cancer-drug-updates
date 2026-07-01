from __future__ import annotations

import asyncio
import json
import os
import re
import time
from html import unescape
from typing import Any, Optional

import httpx

import app.config  # noqa: F401 — .env をプロジェクト直下から読み込む
from app.config import ENV_FILE
from app.feeds import Article

_claude_semaphore = asyncio.Semaphore(1)
_request_lock = asyncio.Lock()
_last_request_at = 0.0
PAPER_COUNT = 20
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_INTERVAL_SEC = float(os.getenv("CLAUDE_REQUEST_INTERVAL", "2"))

TITLE_TRANSLATION_RULES = (
    "薬剤名・化合物名・抗体名・標的分子名（例: pembrolizumab, PD-L1, HER2）は英語表記のまま残してください。"
)
JAPANESE_ONLY = "出力は必ず日本語のみにしてください。英語の文は使わないでください。"


class ClaudeApiError(Exception):
    """Claude API 呼び出しエラー（ユーザー向けメッセージ付き）"""


def _api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")


def has_claude_api_key() -> bool:
    key = _api_key()
    return bool(key) and key not in ("your-key-here", "sk-ant-your-key-here")


def claude_api_key_setup_hint() -> str:
    key = _api_key()
    env_hint = f"設定ファイル: {ENV_FILE}"
    if not key:
        return (
            "ANTHROPIC_API_KEY が空です。"
            "= の右側に Claude のキー（sk-ant- で始まる）を貼り付けて、ファイルを保存してください。"
            f"{env_hint} ／ 取得先: https://console.anthropic.com/settings/keys"
        )
    if key in ("your-key-here", "sk-ant-your-key-here"):
        return (
            "ANTHROPIC_API_KEY がサンプルのままです。"
            "sk-ant- で始まる本物のキーに書き換えて保存してください。"
            f"{env_hint} ／ 取得先: https://console.anthropic.com/settings/keys"
        )
    return (
        "日本語のタイトル和訳・要約には Anthropic Claude APIキーが必要です。"
        f"{env_hint} に ANTHROPIC_API_KEY を設定してください。"
        "取得先: https://console.anthropic.com/settings/keys"
    )


def validate_claude_api_key_format() -> Optional[str]:
    key = _api_key()
    if not key:
        return None
    if key.startswith("sk-ant-"):
        return None
    if key.startswith("sk-proj-") or key.startswith("sk-"):
        return (
            "ANTHROPIC_API_KEY に OpenAI のキーが入っています。"
            "Claude のキー（sk-ant- で始まる）を https://console.anthropic.com/settings/keys で作成し、"
            ".env の ANTHROPIC_API_KEY に貼り付けてください。"
        )
    return (
        "ANTHROPIC_API_KEY の形式が読み取れませんでした。"
        "https://console.anthropic.com/settings/keys で作成したキー（sk-ant- で始まる）を .env に貼り付けてください。"
    )


def _model_name() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


async def _throttle_requests() -> None:
    global _last_request_at
    async with _request_lock:
        now = time.monotonic()
        wait = REQUEST_INTERVAL_SEC - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _friendly_claude_error(status_code: int, message: str) -> str:
    lowered = message.lower()
    if status_code == 429 or "rate_limit" in lowered or "overloaded" in lowered:
        return (
            "Claude APIの利用上限に達しました。"
            "1〜2分待ってから再度お試しください。"
            "（プランによっては1分あたり・1日あたりの上限があります）"
        )
    if status_code in (401, 403) or "authentication" in lowered or "invalid x-api-key" in lowered:
        return (
            "ANTHROPIC_API_KEY が無効です。"
            "https://console.anthropic.com/settings/keys で新しいキーを作成し、.env に貼り付けて保存してください。"
        )
    if status_code == 404 or "not_found_error" in lowered:
        return (
            f"Claude のモデル「{_model_name()}」が見つかりませんでした。"
            ".env の CLAUDE_MODEL を claude-haiku-4-5-20251001 に設定するか、"
            "CLAUDE_MODEL の行を削除してデフォルトを使ってください。"
        )
        return (
            "Claude APIの利用料金の残高が不足している可能性があります。"
            "https://console.anthropic.com/settings/billing で課金設定を確認してください。"
        )
    return f"Claude APIでエラーが発生しました（{status_code}）: {message[:200]}"


def _extract_response_text(data: dict[str, Any]) -> str:
    content = data.get("content") or []
    texts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(texts).strip()


async def generate_text(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 1024,
) -> str:
    api_key = _api_key()
    if not has_claude_api_key():
        raise ClaudeApiError(
            "ANTHROPIC_API_KEY が設定されていません。.env ファイルにAPIキーを追加してください。"
        )

    payload: dict[str, Any] = {
        "model": _model_name(),
        "max_tokens": max_output_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system

    async with _claude_semaphore:
        await _throttle_requests()
        async with httpx.AsyncClient(timeout=90.0) as client:
            last_error = "不明なエラー"
            for attempt in range(6):
                response = await client.post(
                    CLAUDE_API_URL,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": ANTHROPIC_VERSION,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                if response.status_code == 200:
                    text = _extract_response_text(response.json())
                    if text:
                        return text
                    last_error = "応答が空でした"
                    break

                last_error = response.text
                if response.status_code in (429, 529, 503):
                    await asyncio.sleep(min(5 * (attempt + 1), 30))
                    continue

                raise ClaudeApiError(
                    _friendly_claude_error(response.status_code, last_error)
                )

    raise ClaudeApiError(_friendly_claude_error(429, last_error))


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_title(title: str) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", title).strip()


def english_title(article: Article) -> str:
    return _clean_title(article.title)


def abstract_source(article: Article) -> str:
    abstract = _strip_html(article.summary_text)
    if len(abstract) >= 40:
        return abstract
    return english_title(article)


def _parse_paper_json(raw: str, title_en: str) -> tuple[str, str]:
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            data = json.loads(match.group())
            title_ja = str(data.get("title_ja", "")).strip()
            summary = str(data.get("abstract_summary_ja", "")).strip()
            if title_ja and summary:
                return title_ja, summary
        except json.JSONDecodeError:
            pass
    return title_en, raw.strip() or "（要約を取得できませんでした）"


DEFAULT_SUMMARY_CHARS = 200
ALLOWED_SUMMARY_CHARS = (200, 400, 600)


def _summary_max_tokens(summary_chars: int) -> int:
    if summary_chars <= 200:
        return 768
    if summary_chars <= 400:
        return 1200
    return 1536


async def translate_and_summarize_paper(
    article: Article,
    *,
    summary_chars: int = DEFAULT_SUMMARY_CHARS,
) -> tuple[str, str]:
    if summary_chars not in ALLOWED_SUMMARY_CHARS:
        summary_chars = DEFAULT_SUMMARY_CHARS

    title_en = english_title(article)
    abstract = abstract_source(article)
    prompt = (
        "あなたは癌薬物治療に詳しい医学翻訳者です。"
        "以下の論文について、次の2つを日本語で作成してください。\n"
        "1. タイトルの和訳\n"
        f"2. Abstractの和文要約（約{summary_chars}文字。{summary_chars}文字前後に収めてください）\n"
        f"{TITLE_TRANSLATION_RULES}\n"
        f"{JAPANESE_ONLY}\n"
        'JSONのみ返してください: {"title_ja":"...", "abstract_summary_ja":"..."}\n\n'
        f"英語タイトル: {title_en}\n"
        f"出典: {article.source_name}\n"
        f"Abstract:\n{abstract[:3500]}"
    )
    raw = await generate_text(
        prompt,
        system="医学論文を日本語に翻訳・要約する専門家です。",
        temperature=0.3,
        max_output_tokens=_summary_max_tokens(summary_chars),
    )
    return _parse_paper_json(raw, title_en)


async def translate_title(article: Article) -> str:
    title_en = english_title(article)
    prompt = (
        "あなたは医学論文の翻訳者です。"
        "次の英語論文タイトルを自然な日本語に翻訳してください。\n"
        f"{TITLE_TRANSLATION_RULES}\n"
        f"{JAPANESE_ONLY}\n"
        "翻訳文だけを返してください。\n\n"
        f"{title_en}"
    )
    text = await generate_text(
        prompt,
        system="医学論文を日本語に翻訳する専門家です。",
        temperature=0.2,
        max_output_tokens=256,
    )
    return text or title_en


async def summarize_abstract_ja(article: Article) -> str:
    abstract = abstract_source(article)
    prompt = (
        "あなたは癌薬物治療に詳しい医学翻訳者です。"
        "以下の論文Abstractを、日本語の和文で要約してください（約300文字）。\n"
        "背景、方法の要点、主要な結果、臨床的な意味を含めてください。\n"
        f"{TITLE_TRANSLATION_RULES}\n"
        f"{JAPANESE_ONLY}\n"
        "要約文だけを返してください。\n\n"
        f"英語タイトル: {english_title(article)}\n"
        f"出典: {article.source_name}\n"
        f"Abstract:\n{abstract[:4000]}"
    )
    text = await generate_text(
        prompt,
        system="医学論文を日本語で要約する専門家です。",
        temperature=0.3,
        max_output_tokens=768,
    )
    return text or "（要約を取得できませんでした）"


async def translate_abstract_full(article: Article) -> str:
    abstract = abstract_source(article)
    prompt = (
        "あなたは癌薬物治療に詳しい医学翻訳者です。"
        "以下の論文Abstractを、日本語に全文翻訳してください。\n"
        f"{TITLE_TRANSLATION_RULES}\n"
        f"{JAPANESE_ONLY}\n"
        "医学用語は正確に訳し、必要なら短い補足を括弧で添えてください。\n"
        "翻訳文だけを返し、見出しや箇条書きは使わないでください。\n\n"
        f"英語タイトル: {english_title(article)}\n"
        f"Abstract:\n{abstract[:6000]}"
    )
    text = await generate_text(
        prompt,
        system="医学論文のAbstractを日本語に翻訳する専門家です。",
        temperature=0.2,
        max_output_tokens=2048,
    )
    return text or abstract
