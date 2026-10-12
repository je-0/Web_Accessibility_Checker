#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
페이지를 받아 id="container" (또는 지정한 CSS 선택자) 안의 http(s) 링크만 모읍니다.

CAFE24 등 일부 쇼핑몰은 #container에 링크가 없고 #wrap에만 메뉴가 있어,
기본 선택자가 #container일 때 링크가 0개면 #contents → #wrap 순으로 자동 대체합니다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
}

DEFAULT_CONTAINER_SELECTOR = "#container"
DEFAULT_FALLBACK_CHAIN = "#contents,#wrap"


def absolute_http_url(page_url: str, href: str) -> str | None:
    if not href:
        return None
    raw = href.strip().split()[0]
    if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    abs_url = urljoin(page_url, raw)
    abs_url, _frag = urldefrag(abs_url)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    path = parsed.path or "/"
    normalized = parsed._replace(
        path=re.sub(r"/{2,}", "/", path.replace("\\", "/")),
        fragment="",
    )
    return urlunparse(normalized)


def fetch_html(session: requests.Session, url: str, timeout: float) -> str | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None


def extract_urls_from_root(soup_root, page_url: str) -> set[str]:
    found: set[str] = set()
    for tag in soup_root.select("a[href]"):
        u = absolute_http_url(page_url, tag.get("href", ""))
        if u:
            found.add(u)
    for tag in soup_root.select("area[href]"):
        u = absolute_http_url(page_url, tag.get("href", ""))
        if u:
            found.add(u)
    return found


def parse_fallback_chain(s: str) -> list[str]:
    parts = []
    for p in (s or "").split(","):
        x = p.strip()
        if x:
            parts.append(x)
    return parts


def pick_extraction_root(soup: BeautifulSoup, selector: str) -> tuple[object | None, str | None]:
    """(BeautifulSoup 노드 또는 None, 사용한 선택자 문자열)"""
    root = soup.select_one(selector)
    if root is not None:
        return root, selector
    return None, None


def collect_with_fallback(
    soup: BeautifulSoup,
    primary_selector: str,
    page_url: str,
    *,
    enable_fallback: bool,
    fallback_chain: str,
) -> tuple[set[str], str]:
    """
    primary에서 http(s) 링크를 모은다.
    기본 #container인데 0개이면 (CAFE24 등) fallback 선택자를 순서대로 시도한다.
    반환: (url 집합, 실제로 사용한 선택자)
    """
    primary = primary_selector.strip()
    root, used = pick_extraction_root(soup, primary)
    if root is None:
        return set(), ""

    collected = extract_urls_from_root(root, page_url)
    if collected or not enable_fallback or primary != DEFAULT_CONTAINER_SELECTOR:
        return collected, used or primary

    for fb in parse_fallback_chain(fallback_chain):
        if fb == primary:
            continue
        node = soup.select_one(fb)
        if node is None:
            continue
        alt = extract_urls_from_root(node, page_url)
        if alt:
            print(
                f'참고: {DEFAULT_CONTAINER_SELECTOR} 안에 http(s) 링크가 없어 "{fb}" 범위로 대체했습니다.',
                file=sys.stderr,
            )
            return alt, fb

    return collected, used or primary


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(
        description="#container(또는 지정 선택자) 안의 http(s) 링크만 모아 저장합니다.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-u", "--url", help="페이지 주소")
    src.add_argument("-f", "--file", help="로컬 HTML 파일 경로")
    p.add_argument(
        "-s",
        "--selector",
        default=DEFAULT_CONTAINER_SELECTOR,
        help=f'범위로 쓸 CSS 선택자 (기본: {DEFAULT_CONTAINER_SELECTOR})',
    )
    p.add_argument(
        "-o",
        "--output",
        help="저장할 txt (미지정 시 stdout에만 출력)",
        default=None,
    )
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument(
        "--base-url",
        help="(-f 사용 시) 상대경로 href를 절대 URL로 만들 기준 주소 (예: https://site.com/page/)",
        default=None,
    )
    p.add_argument(
        "--no-fallback",
        action="store_true",
        help=f"{DEFAULT_CONTAINER_SELECTOR} 안이 비어 있어도 #contents/#wrap 등으로 대체하지 않습니다.",
    )
    p.add_argument(
        "--fallback",
        default=DEFAULT_FALLBACK_CHAIN,
        metavar="SELECTORS",
        help=(
            f"{DEFAULT_CONTAINER_SELECTOR}에서 링크가 0개일 때 순서대로 시도할 선택자(쉼표 구분). "
            f"기본: {DEFAULT_FALLBACK_CHAIN}"
        ),
    )
    args = p.parse_args(list(argv))

    if args.url:
        page_url = args.url.strip()
        if not page_url.startswith(("http://", "https://")):
            page_url = "https://" + page_url.lstrip("/")
        parsed = urlparse(page_url)
        if not parsed.netloc:
            print("유효한 URL을 입력하세요.", file=sys.stderr)
            return 1
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        html = fetch_html(session, page_url, args.timeout)
        if html is None:
            print("페이지를 가져오지 못했습니다.", file=sys.stderr)
            return 2
        base_for_links = page_url
    else:
        path = Path(args.file)
        if not path.is_file():
            print(f"파일이 없습니다: {path}", file=sys.stderr)
            return 1
        html = path.read_text(encoding="utf-8", errors="replace")
        bu = (args.base_url or "").strip()
        if bu and not bu.startswith(("http://", "https://")):
            bu = "https://" + bu.lstrip("/")
        base_for_links = bu
        if not base_for_links:
            print(
                "경고: --base-url 이 없으면 상대경로 링크는 건너뜁니다.",
                file=sys.stderr,
            )

    soup = BeautifulSoup(html, "html.parser")
    primary = args.selector.strip()
    collected, used_sel = collect_with_fallback(
        soup,
        primary,
        base_for_links,
        enable_fallback=not args.no_fallback,
        fallback_chain=args.fallback,
    )
    if not used_sel:
        print(f"선택자에 해당하는 요소가 없습니다: {primary!r}", file=sys.stderr)
        return 3

    lines = [u for u in sorted(collected)]
    if args.output:
        Path(args.output).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        print(f"저장 완료: {args.output} ({len(lines)}개)", file=sys.stderr)
    else:
        sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
