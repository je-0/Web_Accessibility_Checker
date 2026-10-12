#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
사이트의 sitemap(인덱스 포함)만 따라가며, 등장하는 페이지 URL(<url><loc>)을 텍스트 파일로 저장합니다.
HTML 크롤링은 하지 않습니다.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from typing import Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse
from xml.etree import ElementTree as ET

import requests

DEFAULT_HEADERS = {
    "User-Agent": "SitemapURLCollector/1.0",
    "Accept": "application/xml,text/xml,*/*;q=0.8",
    "Accept-Language": "ko,en;q=0.9",
}

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def normalize_page_url(href: str) -> str | None:
    if not href or not href.strip():
        return None
    raw = href.strip().split()[0]
    abs_url = urljoin("", raw)
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


def fetch_text(session: requests.Session, url: str, timeout: float) -> str | None:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None


def parse_sitemap_xml(text: str) -> tuple[list[str], list[str]]:
    """(<url>의 페이지 URL 목록, <sitemapindex>의 하위 sitemap URL 목록)"""
    pages: list[str] = []
    child_maps: list[str] = []
    try:
        root = ET.fromstring(text.encode("utf-8", errors="replace"))
    except ET.ParseError:
        return pages, child_maps

    def local(tag: str) -> str:
        return f"{{{SITEMAP_NS}}}{tag}"

    if root.tag.endswith("sitemapindex") or root.find(local("sitemap")) is not None:
        for sm in root.findall(f".//{local('sitemap')}"):
            loc = sm.find(local("loc"))
            if loc is not None and loc.text:
                u = normalize_page_url(loc.text)
                if u:
                    child_maps.append(u)
        return pages, child_maps

    for elem in root.findall(f".//{local('url')}"):
        loc = elem.find(local("loc"))
        if loc is not None and loc.text:
            u = normalize_page_url(loc.text)
            if u:
                pages.append(u)

    if not pages and not child_maps:
        for el in root.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "loc" and el.text:
                u = normalize_page_url(el.text)
                if not u:
                    continue
                if root.tag.endswith("sitemapindex") or "index" in root.tag.lower():
                    child_maps.append(u)
                else:
                    pages.append(u)

    return pages, child_maps


def discover_seed_sitemaps(session: requests.Session, start_url: str, timeout: float) -> list[str]:
    parsed = urlparse(start_url)
    origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    seeds: list[str] = [
        urljoin(origin, "/sitemap.xml"),
        urljoin(origin, "/sitemap_index.xml"),
        urljoin(origin, "/wp-sitemap.xml"),
    ]

    robots = fetch_text(session, urljoin(origin, "/robots.txt"), timeout)
    if robots:
        for line in robots.splitlines():
            line = line.strip()
            if line.lower().startswith("sitemap:"):
                part = line.split(":", 1)[1].strip()
                if part:
                    seeds.append(part)

    return seeds


def collect_urls_from_sitemaps(session: requests.Session, start_url: str, timeout: float) -> tuple[set[str], bool]:
    """
    사이트맵에서 페이지 URL만 수집.
    반환: (url 집합, 실제로 읽은 sitemap 파일이 하나라도 있었는지)
    """
    normalized_start = normalize_page_url(start_url) or start_url.strip()
    if not normalized_start.startswith(("http://", "https://")):
        normalized_start = "https://" + normalized_start.lstrip("/")

    parsed = urlparse(normalized_start)
    if not parsed.netloc:
        return set(), False

    seeds = discover_seed_sitemaps(session, normalized_start, timeout)

    seen_maps: set[str] = set()
    all_pages: set[str] = set()
    q: deque[str] = deque()
    any_fetched = False

    for s in seeds:
        u = normalize_page_url(s)
        if u:
            q.append(u)

    while q:
        sm_url = q.popleft()
        sm_url = urldefrag(sm_url)[0]
        if sm_url in seen_maps:
            continue
        seen_maps.add(sm_url)

        xml_text = fetch_text(session, sm_url, timeout)
        if xml_text is None:
            continue
        any_fetched = True

        pages, children = parse_sitemap_xml(xml_text)
        for p in pages:
            all_pages.add(p)
        for c in children:
            cu = normalize_page_url(c)
            if cu and cu not in seen_maps:
                q.append(cu)

    return all_pages, any_fetched


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(
        description="사이트맵(XML)에 있는 URL만 모아 텍스트 파일로 저장합니다.",
    )
    p.add_argument("url", help="사이트 주소 (예: https://example.com)")
    p.add_argument(
        "-o",
        "--output",
        help="저장할 파일 (기본: sitemap_urls_<호스트>.txt)",
        default=None,
    )
    p.add_argument("--timeout", type=float, default=20.0, help="요청 타임아웃 초")
    args = p.parse_args(list(argv))

    start = (args.url or "").strip()
    parsed = urlparse(start if start.startswith(("http://", "https://")) else f"https://{start}")
    if not parsed.netloc:
        print("유효한 URL을 입력하세요.", file=sys.stderr)
        return 1

    out_path = args.output
    if not out_path:
        host = re.sub(r"[^\w.-]+", "_", parsed.netloc)
        out_path = f"sitemap_urls_{host}.txt"

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    collected, ok = collect_urls_from_sitemaps(session, start, args.timeout)
    if not ok:
        print(
            "사이트맵을 가져오지 못했습니다. robots.txt의 Sitemap: 또는 "
            "/sitemap.xml 등이 있는지 확인하세요.",
            file=sys.stderr,
        )
        return 2

    with open(out_path, "w", encoding="utf-8") as f:
        for u in sorted(collected):
            f.write(u + "\n")

    print(f"저장 완료: {out_path} ({len(collected)}개)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
