#!/usr/bin/env python3
"""Gmail SMTP 接続テスト（.env の設定確認用）。"""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.config  # noqa: F401  # loads .env
from app.mailer import _env, _normalize_smtp_password


def _try_login(host: str, port: int, user: str, password: str, *, use_ssl: bool) -> None:
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
        return

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)


def main() -> int:
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT") or "587")
    user = _env("SMTP_USER")
    raw_password = _env("SMTP_PASSWORD")
    password = _normalize_smtp_password(raw_password)

    print("=== Gmail SMTP 接続テスト ===")
    print(f"SMTP_HOST: {host or '（未設定）'}")
    print(f"SMTP_PORT: {port}")
    print(f"SMTP_USER: {user or '（未設定）'}")
    print("  ※ アプリパスワードは、この SMTP_USER と同じ Google アカウントで作成してください。")
    print(f"SMTP_PASSWORD: {'設定あり' if password else '（未設定）'}")
    print(f"  文字数（スペース除去後）: {len(password)}")
    if raw_password != password:
        print(f"  文字数（.env の生値）: {len(raw_password)}")
    print()

    if not host or not user or not password:
        print("NG: SMTP_HOST / SMTP_USER / SMTP_PASSWORD を .env に設定してください。")
        return 1

    attempts = [
        ("587 + STARTTLS", host, 587, False),
        ("465 + SSL", host, 465, True),
    ]
    last_error: Exception | None = None
    for label, attempt_host, attempt_port, use_ssl in attempts:
        try:
            _try_login(attempt_host, attempt_port, user, password, use_ssl=use_ssl)
            print(f"OK: {label} でログイン成功")
            if len(password) != 16:
                print("注意: アプリパスワードは通常16文字です。文字数が違う場合は再作成を検討してください。")
            return 0
        except smtplib.SMTPAuthenticationError as exc:
            last_error = exc
            detail = str(exc.args[-1]) if exc.args else str(exc)
            if "Application-specific password required" in detail or "5.7.9" in detail:
                print(
                    f"NG: {label} -> Gmail は通常パスワードを拒否しました。"
                    " アプリパスワードが必要です。"
                )
            else:
                print(f"NG: {label} -> {exc}")
        except Exception as exc:
            last_error = exc
            print(f"NG: {label} -> {exc}")

    print()
    print("すべての接続方法で失敗しました。")
    if len(password) == 16:
        print()
        print("パスワードの形式（16文字）は正しいですが、Gmail が中身を拒否しています。")
        print("次を順に確認してください:")
        print(f"1. ブラウザ右上が {user} でログインしているか")
        print("2. https://myaccount.google.com/apppasswords をそのアカウントで開く")
        print("3. 古いアプリパスワードをすべて削除する")
        print("4. 新しいアプリパスワードを作成する（名前は「メール」など任意）")
        print("5. .env の SMTP_PASSWORD を新しい16文字だけに置き換える")
        print("6. 別アカウント（例: thebirdofwonder@gmail.com）で作ったパスワードは使えません")
    print()
    print("対処: https://myaccount.google.com/apppasswords で新しいアプリパスワードを作成し、")
    print("      .env の SMTP_PASSWORD を置き換えてからアプリを再起動してください。")
    if last_error:
        print(f"最後のエラー: {last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
