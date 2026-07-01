from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional, Protocol


class PaperLike(Protocol):
    rank: int
    link: str
    source_abbrev: str
    published_date: Optional[str]
    impact_score: int
    impact_reason: str
    title_en: str
    title_ja: str
    abstract_summary_ja: str


def _rank_label(paper: PaperLike) -> str:
    return f"[{paper.rank}]"


def _date_suffix(paper: PaperLike) -> str:
    if paper.published_date:
        return f", {paper.published_date}"
    return ""


def _paper_headline(paper: PaperLike) -> str:
    return (
        f"{_rank_label(paper)} {paper.title_en} {paper.source_abbrev}{_date_suffix(paper)}"
    )


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def has_mail_config() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_USER") and _env("SMTP_PASSWORD"))


def mail_config_hint() -> str:
    missing = []
    if not _env("SMTP_HOST"):
        missing.append("SMTP_HOST（メールサーバー）")
    if not _env("SMTP_USER"):
        missing.append("SMTP_USER（SMTPログイン名）")
    if not _env("SMTP_PASSWORD"):
        missing.append("SMTP_PASSWORD（SMTPパスワード）")
    if not missing:
        return ""
    return (
        "メール送信の設定が不足しています。.env に "
        + "、".join(missing)
        + " を設定してください。"
    )


def validate_email_address(raw: str) -> Optional[str]:
    email = raw.strip()
    if not email:
        return "送信先メールアドレスを入力してください。"
    if email.count("@") != 1:
        return "メールアドレスの形式が正しくありません。"
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return "メールアドレスの形式が正しくありません。"
    return None


def _smtp_from() -> str:
    return _env("SMTP_FROM") or _env("SMTP_USER")


def _smtp_port() -> int:
    raw = _env("SMTP_PORT")
    return int(raw) if raw.isdigit() else 587


def format_papers_plain(
    papers: list[PaperLike],
    note: Optional[str] = None,
    *,
    keywords: Optional[str] = None,
) -> str:
    lines = [
        "医学論文検索結果",
    ]
    if keywords:
        lines.append(f"検索キーワード: {keywords}")
    lines.extend(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            f"件数: {len(papers)}",
        ]
    )
    if note:
        lines.append(f"注記: {note}")
    lines.append("")

    for paper in papers:
        lines.extend(
            [
                _paper_headline(paper),
                f"和訳: {paper.title_ja}",
                f"要約: {paper.abstract_summary_ja}",
                paper.link,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def format_papers_html(
    papers: list[PaperLike],
    note: Optional[str] = None,
    *,
    keywords: Optional[str] = None,
) -> str:
    header_lines = ["医学論文検索結果"]
    if keywords:
        header_lines.append(f"検索キーワード: {escape(keywords)}")
    header_lines.append(
        f"{escape(datetime.now().strftime('%Y-%m-%d %H:%M'))} ／ 件数: {len(papers)}"
    )
    parts = [
        "<html><body style='font-family:sans-serif;font-size:14px;line-height:1.4;margin:8px;'>",
        f"<p>{'<br>'.join(header_lines)}</p>",
    ]
    if note:
        parts.append(f"<p>注記: {escape(note)}</p>")

    for paper in papers:
        date_part = escape(paper.published_date) if paper.published_date else ""
        date_suffix = f", {date_part}" if date_part else ""
        parts.append(
            "<p>"
            f"{paper.rank}. <b>{escape(paper.title_en)}</b> "
            f"<i>{escape(paper.source_abbrev)}</i>{date_suffix}<br>"
            f"和訳: {escape(paper.title_ja)}<br>"
            f"要約: {escape(paper.abstract_summary_ja)}<br>"
            f'<a href="{escape(paper.link)}">{escape(paper.link)}</a>'
            "</p>"
        )

    parts.append("</body></html>")
    return "".join(parts)


def send_papers_email(
    papers: list[PaperLike],
    *,
    recipient: str,
    note: Optional[str] = None,
    keywords: Optional[str] = None,
) -> str:
    if not has_mail_config():
        raise ValueError(mail_config_hint() or "メール設定が不完全です。")

    email_error = validate_email_address(recipient)
    if email_error:
        raise ValueError(email_error)

    recipient = recipient.strip()
    subject = _env("MAIL_SUBJECT") or "医学論文検索結果"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _smtp_from()
    msg["To"] = recipient
    msg.attach(
        MIMEText(format_papers_plain(papers, note, keywords=keywords), "plain", "utf-8")
    )
    msg.attach(
        MIMEText(format_papers_html(papers, note, keywords=keywords), "html", "utf-8")
    )

    host = _env("SMTP_HOST")
    port = _smtp_port()
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    use_tls = _env("SMTP_USE_TLS", "true").lower() not in ("0", "false", "no")

    with smtplib.SMTP(host, port, timeout=30) as server:
        if use_tls:
            server.starttls()
        server.login(user, password)
        server.sendmail(_smtp_from(), [recipient], msg.as_string())

    return recipient
