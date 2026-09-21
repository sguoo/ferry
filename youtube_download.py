"""Command-line front for the same backend the Ferry app uses (app/youtube.py).

    uv run youtube_download.py info   <url>
    uv run youtube_download.py search <query> [-n 5]
    uv run youtube_download.py playlist <url>
    uv run youtube_download.py get    <url> [--audio] [--quality 1080] [--container mkv] [--sub ko]
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from app import youtube
from app.youtube import DownloadOptions, Progress, YoutubeError


def _auth(args: argparse.Namespace) -> DownloadOptions:
    return DownloadOptions(cookies_file=args.cookies, cookies_from_browser=args.browser)


def cmd_info(args: argparse.Namespace) -> None:
    info = youtube.fetch_info(args.url, _auth(args))
    print(f"{info.title}")
    print(f"  채널: {info.channel}{' (인증)' if info.channel_verified else ''} · {youtube.fmt_subscribers(info.subscribers)}")
    print(f"  {youtube.fmt_views(info.views)} · {youtube.fmt_relative(info.upload_date)} · 길이 {youtube.fmt_duration(info.duration)}")
    print(f"  오디오: {info.audio_codec} {info.audio_bitrate or '?'}k ({youtube.fmt_size(info.audio_est_size, approx=True)})")
    print(f"  스트림 {info.stream_count}개, 선택 가능한 화질:")
    for q in info.qualities:
        print(f"    {q.label:<9} {q.tag:<4} {q.vcodec:<6} {youtube.fmt_size(q.est_size, approx=True)}")
    if info.subtitles:
        print("  자막: " + ", ".join(f"{s.lang}{'(자동)' if s.auto else ''}" for s in info.subtitles[:12]))


def cmd_search(args: argparse.Namespace) -> None:
    for i, hit in enumerate(youtube.search(args.query, args.count, args.kind), start=1):
        kind = "재생목록" if hit.is_playlist else ("LIVE" if hit.is_live else youtube.fmt_duration(hit.duration))
        print(f"{i}. {hit.title}")
        print(f"   {hit.channel} · {youtube.fmt_views(hit.views)} · {kind}")
        print(f"   {hit.url}")


def cmd_playlist(args: argparse.Namespace) -> None:
    pl = youtube.fetch_playlist(args.url, _auth(args))
    print(f"{pl.title} — {pl.channel}")
    print(f"  총 {pl.count}개 · {youtube.fmt_duration_long(pl.total_duration)}")
    for e in pl.entries[: args.limit]:
        print(f"  {e.index:>3}. {youtube.fmt_duration(e.duration):>8}  {e.title}")
    if pl.count > args.limit:
        print(f"  ... 외 {pl.count - args.limit}개")


def cmd_get(args: argparse.Namespace) -> None:
    opts = DownloadOptions(
        mode="audio" if args.audio else ("subtitles" if args.subs_only else "video"),
        quality=args.quality,
        container=args.container,
        audio_format=args.audio_format,
        subtitle_langs=args.sub or [],
        output_dir=Path(args.out) if args.out else youtube.DEFAULT_OUTPUT_DIR,
        rate_limit=args.limit_rate,
        cookies_file=args.cookies,
        cookies_from_browser=args.browser,
    )
    cancel = threading.Event()
    last_line = {"len": 0}

    def show(p: Progress) -> None:
        if p.phase == "downloading":
            line = (
                f"\r{p.percent:5.1f}%  {youtube.fmt_size(p.downloaded)} / {youtube.fmt_size(p.total)}"
                f"  {youtube.fmt_speed(p.speed)}  남은 시간 {youtube.fmt_eta(p.eta)}"
            )
        else:
            line = f"\r[{p.phase}] {p.note or p.filename}"
        pad = max(0, last_line["len"] - len(line))
        sys.stdout.write(line + " " * pad)
        sys.stdout.flush()
        last_line["len"] = len(line)

    try:
        path = youtube.download(args.url, opts, on_progress=show, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("\n취소됨 (부분 파일은 다음 실행 때 이어받습니다)")
        return
    print(f"\n완료: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="youtube_download", description="Ferry 백엔드 CLI")
    parser.add_argument("--cookies", help="로그인된 브라우저에서 내보낸 cookies.txt (봇 확인·연령 제한 우회)")
    parser.add_argument("--browser", choices=["chrome", "edge", "firefox", "brave"], help="브라우저 쿠키를 직접 읽기")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="영상 정보와 선택 가능한 화질")
    p.add_argument("url")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("search", help="YouTube 검색")
    p.add_argument("query")
    p.add_argument("-n", "--count", type=int, default=5)
    p.add_argument("--kind", choices=["all", "video", "playlist", "channel"], default="all")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("playlist", help="재생목록/채널 항목 나열")
    p.add_argument("url")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_playlist)

    p = sub.add_parser("get", help="다운로드")
    p.add_argument("url")
    p.add_argument("--audio", action="store_true", help="음원만 추출")
    p.add_argument("--audio-format", choices=["mp3", "flac", "m4a", "opus"], default="mp3")
    p.add_argument("--subs-only", action="store_true", help="자막만 받기")
    p.add_argument("--quality", type=int, help="최대 세로 해상도 (예: 1080)")
    p.add_argument("--container", choices=["mp4", "mkv", "webm"], default="mp4")
    p.add_argument("--sub", action="append", help="자막 언어 (여러 번 지정 가능, 예: --sub ko --sub en)")
    p.add_argument("--out", help="저장 폴더")
    p.add_argument("--limit-rate", help="속도 제한 (예: 20M)")
    p.set_defaults(fn=cmd_get)

    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except YoutubeError as exc:
        print(f"오류: {exc.message}", file=sys.stderr)
        if exc.detail and exc.detail != exc.message:
            print(f"  ({exc.detail.splitlines()[0][:200]})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
