from __future__ import annotations

from typing import Optional

import app.config  # noqa: F401
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.feeds import (
    Article,
    SOURCE_ABBREV,
    SOURCES,
    fetch_selected_feeds,
    format_published_date,
    source_abbrev,
)
from app.filter import parse_keywords, select_candidates
from app.mailer import (
    has_mail_config,
    mail_config_hint,
    send_papers_email,
    validate_email_address,
)
from app.ranking import rank_by_clinical_impact
from app.summarizer import (
    ClaudeApiError,
    claude_api_key_setup_hint,
    english_title,
    has_claude_api_key,
    translate_abstract_full,
    translate_and_summarize_paper,
    validate_claude_api_key_format,
)

app = FastAPI(title="Med Journal Search App", version="3.0.0")

ALLOWED_PAPER_COUNTS = (10, 20, 30, 50)
DEFAULT_PAPER_COUNT = 20
ALLOWED_SUMMARY_CHARS = (200, 400, 600)
DEFAULT_SUMMARY_CHARS = 200

_abstract_full_cache: dict[str, str] = {}
_article_cache: dict[str, Article] = {}


class PaperResponse(BaseModel):
    id: str
    rank: int
    impact_score: int
    impact_reason: str
    link: str
    source_name: str
    source_abbrev: str
    published_date: Optional[str] = None
    title_en: str
    title_ja: str
    abstract_summary_ja: str


class FetchRequest(BaseModel):
    email_to: str = ""
    source_ids: list[str]
    custom_urls: list[str] = []
    keywords: str
    paper_count: int = DEFAULT_PAPER_COUNT
    summary_length: int = DEFAULT_SUMMARY_CHARS


class FetchResponse(BaseModel):
    papers: list[PaperResponse]
    message: Optional[str] = None
    email_sent: bool = False
    email_to: Optional[str] = None
    displayed_on_screen: bool = False


class AbstractFullResponse(BaseModel):
    id: str
    abstract_full_ja: str


@app.get("/api/sources")
def list_sources() -> list[dict[str, str]]:
    return [
        {
            "id": source["id"],
            "name": source["name"],
            "abbrev": SOURCE_ABBREV.get(source["id"], source["name"]),
        }
        for source in SOURCES
    ]


def _normalize_custom_urls(raw_urls: list[str]) -> list[str]:
    urls: list[str] = []
    for raw in raw_urls:
        for part in raw.replace("\n", ",").split(","):
            url = part.strip()
            if url and url not in urls:
                urls.append(url)
    return urls


def _validate_fetch_request(
    body: FetchRequest,
) -> tuple[list[str], list[str], list[str]]:
    source_ids = [source_id.strip() for source_id in body.source_ids if source_id.strip()]
    custom_urls = _normalize_custom_urls(body.custom_urls)
    keywords = parse_keywords(body.keywords)

    if not source_ids and not custom_urls:
        raise HTTPException(
            status_code=400,
            detail="雑誌を1つ以上選ぶか、追加RSSのURLを入力してください。",
        )
    if not keywords:
        raise HTTPException(
            status_code=400,
            detail="キーワードを1つ以上入力してください（カンマ区切りで複数可、OR検索）。",
        )
    if body.paper_count not in ALLOWED_PAPER_COUNTS:
        allowed = ", ".join(str(count) for count in ALLOWED_PAPER_COUNTS)
        raise HTTPException(
            status_code=400,
            detail=f"Paper count must be one of: {allowed}.",
        )
    if body.summary_length not in ALLOWED_SUMMARY_CHARS:
        allowed = ", ".join(str(n) for n in ALLOWED_SUMMARY_CHARS)
        raise HTTPException(
            status_code=400,
            detail=f"Summary length must be one of: {allowed} characters.",
        )

    valid_ids = {source["id"] for source in SOURCES}
    unknown = [source_id for source_id in source_ids if source_id not in valid_ids]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"不明な雑誌IDです: {', '.join(unknown)}",
        )

    return source_ids, custom_urls, keywords


@app.get("/api/paper-counts")
def list_paper_counts() -> dict[str, object]:
    return {
        "options": list(ALLOWED_PAPER_COUNTS),
        "default": DEFAULT_PAPER_COUNT,
    }


@app.get("/api/summary-lengths")
def list_summary_lengths() -> dict[str, object]:
    return {
        "options": list(ALLOWED_SUMMARY_CHARS),
        "default": DEFAULT_SUMMARY_CHARS,
    }


async def _prepare_paper(
    ranked_item,
    rank: int,
    *,
    summary_length: int,
) -> PaperResponse:
    article = ranked_item.article
    _article_cache[article.id] = article

    title_ja, abstract_summary_ja = await translate_and_summarize_paper(
        article, summary_chars=summary_length
    )

    return PaperResponse(
        id=article.id,
        rank=rank,
        impact_score=ranked_item.impact_score,
        impact_reason=ranked_item.impact_reason,
        link=article.link,
        source_name=article.source_name,
        source_abbrev=source_abbrev(article.source_id, article.source_name),
        published_date=format_published_date(article.published),
        title_en=english_title(article),
        title_ja=(title_ja or "").strip() or english_title(article),
        abstract_summary_ja=(abstract_summary_ja or "").strip()
        or "（要約を取得できませんでした）",
    )


@app.post("/api/papers", response_model=FetchResponse)
async def fetch_papers(body: FetchRequest) -> FetchResponse:
    if not has_claude_api_key():
        raise HTTPException(status_code=503, detail=claude_api_key_setup_hint())

    key_warning = validate_claude_api_key_format()
    if key_warning:
        raise HTTPException(status_code=503, detail=key_warning)

    email_to = body.email_to.strip()
    if email_to:
        email_error = validate_email_address(email_to)
        if email_error:
            raise HTTPException(status_code=400, detail=email_error)
        if not has_mail_config():
            raise HTTPException(status_code=503, detail=mail_config_hint())

    source_ids, custom_urls, keywords = _validate_fetch_request(body)
    paper_count = body.paper_count
    summary_length = body.summary_length

    try:
        articles = await fetch_selected_feeds(source_ids, custom_urls)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"論文の取得に失敗しました: {exc}") from exc

    candidates = select_candidates(articles, paper_count * 3, keywords)

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="キーワードに一致する論文が見つかりませんでした。キーワードや雑誌の選択を変えてください。",
        )

    try:
        ranked = await rank_by_clinical_impact(candidates, keywords)
    except ClaudeApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    selected = ranked[:paper_count]

    papers = []
    partial_message: Optional[str] = None
    for rank, ranked_item in enumerate(selected, start=1):
        try:
            papers.append(
                await _prepare_paper(
                    ranked_item, rank, summary_length=summary_length
                )
            )
        except ClaudeApiError as exc:
            if papers:
                partial_message = (
                    f"{len(papers)}件まで表示しました。"
                    f"残りは利用上限のため取得できませんでした: {exc}"
                )
                break
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not papers:
        raise HTTPException(status_code=404, detail="論文を処理できませんでした。")

    sent_to: Optional[str] = None
    displayed_on_screen = False
    result_message = partial_message

    if email_to:
        try:
            sent_to = send_papers_email(
                papers,
                recipient=email_to,
                note=partial_message,
                keywords=body.keywords.strip(),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Papers processed but email failed: {exc}",
            ) from exc
        result_message = (
            f"{partial_message} " if partial_message else ""
        ) + f"Sent {len(papers)} papers to {sent_to}."
    else:
        displayed_on_screen = True
        result_message = (
            f"{partial_message} " if partial_message else ""
        ) + f"Showing {len(papers)} papers below (no email)."

    return FetchResponse(
        papers=papers,
        message=result_message,
        email_sent=bool(sent_to),
        email_to=sent_to,
        displayed_on_screen=displayed_on_screen,
    )


@app.get("/api/papers/{paper_id}/abstract-full", response_model=AbstractFullResponse)
async def get_abstract_full(paper_id: str) -> AbstractFullResponse:
    if paper_id in _abstract_full_cache:
        return AbstractFullResponse(
            id=paper_id, abstract_full_ja=_abstract_full_cache[paper_id]
        )

    target = _article_cache.get(paper_id)
    if not target:
        raise HTTPException(status_code=404, detail="論文が見つかりませんでした。")

    try:
        text = await translate_abstract_full(target)
    except ClaudeApiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    _abstract_full_cache[paper_id] = text
    return AbstractFullResponse(id=paper_id, abstract_full_ja=text)


@app.get("/api/fetch")
async def fetch_updates_legacy(email_to: str):
    result = await fetch_papers(FetchRequest(email_to=email_to))
    return {
        "count": len(result.papers),
        "articles": [
            {
                "id": p.id,
                "impact_rank": p.rank,
                "link": p.link,
                "source_name": p.source_name,
                "title_en": p.title_en,
                "title_ja": p.title_ja,
                "abstract_summary": p.abstract_summary_ja,
            }
            for p in result.papers
        ],
    }


@app.get("/api/articles/{paper_id}/abstract-full", response_model=AbstractFullResponse)
async def get_abstract_full_legacy(paper_id: str) -> AbstractFullResponse:
    return await get_abstract_full(paper_id)


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")
