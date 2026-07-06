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
    value = os.getenv(name, default).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value


def _normalize_smtp_password(password: str) -> str:
    # Gmail app passwords are often copied as "abcd efgh ijkl mnop".
    return password.replace(" ", "")


def _is_gmail_host(host: str) -> bool:
    return "gmail.com" in host.lower()


def _gmail_auth_hint(user: str) -> str:
    lines = [
        "Gmail のログインに失敗しました（ユーザー名またはパスワードが拒否されました）。",
        "次を確認してください:",
        "1. SMTP_USER には送信元の Gmail アドレス全体（例: name@gmail.com）を設定する",
        "2. SMTP_PASSWORD には通常の Gmail パスワードではなく「アプリパスワード」を設定する",
        "3. Google アカウントで2段階認証を有効にしてからアプリパスワードを作成する",
        "   https://myaccount.google.com/apppasswords",
    ]
    if "@" not in user:
        lines.append("※ 現在の SMTP_USER に @ が含まれていません。")
    return "\n".join(lines)


def _validate_smtp_credentials(host: str, user: str, password: str) -> Optional[str]:
    if not _is_gmail_host(host):
        return None
    if "@" not in user:
        return (
            "SMTP_USER には Gmail のメールアドレス全体（@gmail.com まで）を設定してください。"
        )
    if not _normalize_smtp_password(password):
        return "SMTP_PASSWORD を .env に設定してください。"
    return None


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


def _smtp_use_ssl(port: int) -> bool:
    explicit = _env("SMTP_USE_SSL").lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    return port == 465


def _send_via_smtp(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    mail_from: str,
    recipient: str,
    message: str,
) -> None:
    use_ssl = _smtp_use_ssl(port)
    use_tls = _env("SMTP_USE_TLS", "true").lower() not in ("0", "false", "no")

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.sendmail(mail_from, [recipient], message)
        return

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        server.login(user, password)
        server.sendmail(mail_from, [recipient], message)


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
    password = _normalize_smtp_password(_env("SMTP_PASSWORD"))
    mail_from = _smtp_from()

    credential_error = _validate_smtp_credentials(host, user, password)
    if credential_error:
        raise ValueError(credential_error)

    try:
        _send_via_smtp(
            host=host,
            port=port,
            user=user,
            password=password,
            mail_from=mail_from,
            recipient=recipient,
            message=msg.as_string(),
        )
    except smtplib.SMTPAuthenticationError as exc:
        if _is_gmail_host(host):
            raise ValueError(_gmail_auth_hint(user)) from exc
        raise ValueError(
            "メールサーバーのログインに失敗しました。SMTP_USER と SMTP_PASSWORD を確認してください。"
        ) from exc

    return recipient
