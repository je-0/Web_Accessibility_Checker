#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
URL 목록 파일(prmr.txt 등)을 읽어 각 페이지에 접속해 웹접근성 점검을 수행합니다.

[기존 검사 항목]
- axe-core(주입) 위반 요약 + 세부 규칙 ID
- 문서 주언어(html lang) / 이미지 alt 누락·의심 / 폼 컨트롤 접근 가능한 이름
- 제목 구조 / 테이블 caption / 키보드(tabindex>0) / 랜드마크 / 다이얼로그

[필수 추가 검사 항목]
- iframe title 속성 누락 검사
- 새 창 열림 링크(target="_blank") 안내 문구 누락 검사 (title 등)
- 이미지 맵 (<area>) alt 속성 누락 검사
- 키보드 포커스 링 미표시 의심 요소 검사 (outline: none 상태 확인)
- 폼 컨트롤 에러 처리 유무 (aria-invalid 등 사용 여부 카운트)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

AXE_CDN = "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js"


def url_dir_slug(url: str, *, max_len: int = 100) -> str:
    """Windows 경로에 쓸 수 있는 URL별 폴더 이름."""
    p = urlparse(url)
    host = re.sub(r"[^\w.-]", "_", p.netloc or "unknown")
    path_part = (p.path or "/").strip("/").replace("/", "_") or "index"
    path_part = re.sub(r"[^\w.-]", "_", path_part)
    slug = f"{host}__{path_part}"
    if p.query:
        q = re.sub(r"[^\w.-]", "_", p.query)[:32]
        slug = f"{slug}__{q}"
    slug = re.sub(r"_+", "_", slug).strip("_")
    if len(slug) > max_len:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        slug = f"{slug[: max_len - 11]}_{digest}"
    return slug or "page"


def resolve_run_out_dir(
    urls_file: Path | None,
    out_dir: Path | None,
    *,
    auto_out_dir: bool,
) -> Path | None:
    if out_dir is not None:
        return out_dir
    if auto_out_dir and urls_file is not None:
        return Path("reports") / urls_file.stem
    return None


def apply_out_dir_to_outputs(out_dir: Path, args: argparse.Namespace) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "json_out": Path("a11y_report.json"),
        "summary_out": Path("a11y_report_summary.txt"),
        "excel_out": Path("a11y_report.xlsx"),
    }
    if args.json_out == defaults["json_out"]:
        args.json_out = out_dir / defaults["json_out"].name
    if args.summary_out == defaults["summary_out"]:
        args.summary_out = out_dir / defaults["summary_out"].name
    if args.excel_out == defaults["excel_out"]:
        args.excel_out = out_dir / defaults["excel_out"].name


def write_per_url_dirs(rows: list[dict[str, Any]], pages_root: Path) -> None:
    pages_root.mkdir(parents=True, exist_ok=True)
    used: dict[str, int] = {}
    for row in rows:
        url = str(row.get("url") or "")
        if not url:
            continue
        base_slug = url_dir_slug(url)
        n = used.get(base_slug, 0)
        used[base_slug] = n + 1
        slug = base_slug if n == 0 else f"{base_slug}__{n + 1}"
        page_dir = pages_root / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "url.txt").write_text(url + "\n", encoding="utf-8")
        (page_dir / "report.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_summary_txt([row], page_dir / "summary.txt")


def read_urls(path: Path) -> list[str]:
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def run_dom_checks_js() -> str:
    """page.evaluate에 넣을 IIFE: 브라우저에서 실행."""
    return r"""
(() => {
  const pick = (el) => {
    if (!el || !el.getAttribute) return "";
    const id = el.getAttribute("id");
    const cls = (el.getAttribute("class") || "").split(/\s+/).slice(0, 3).join(".");
    const tag = el.tagName ? el.tagName.toLowerCase() : "node";
    let s = tag + (id ? "#" + id : "") + (cls ? "." + cls : "");
    return s.slice(0, 120);
  };

  const visibleEnough = (el) => {
    if (!el || el.getAttribute("aria-hidden") === "true") return false;
    const st = window.getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) === 0)
      return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const hasAccName = (el) => {
    if (el.getAttribute("aria-label")) return true;
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled && labelled.trim()) return true;
    if (el.labels && el.labels.length) return true;
    const id = el.getAttribute("id");
    if (id && document.querySelector('label[for="' + CSS.escape(id) + '"]')) return true;
    if (el.closest("label")) return true;
    if (el.getAttribute("title")) return true;
    return false;
  };

  const html = document.documentElement;
  const lang = (html.getAttribute("lang") || html.getAttribute("xml:lang") || "").trim();

  // 1. 이미지 및 이미지맵 검사 보완
  const imgs = Array.from(document.querySelectorAll("img"));
  const imgIssues = [];
  for (const img of imgs) {
    if (!visibleEnough(img)) continue;
    const role = (img.getAttribute("role") || "").toLowerCase();
    const alt = img.getAttribute("alt");
    const presentation = role === "presentation" || role === "none" || alt === "";
    if (alt === null) imgIssues.push({ kind: "missing_alt", selector: pick(img) });
    else if (alt.trim() === "" && !presentation)
      imgIssues.push({ kind: "empty_alt_not_decorative", selector: pick(img) });
  }

  const areas = Array.from(document.querySelectorAll("area"));
  for (const area of areas) {
    const alt = area.getAttribute("alt");
    if (alt === null || alt.trim() === "") {
      imgIssues.push({ kind: "missing_area_alt", selector: pick(area) });
    }
  }

  // 2. 폼 컨트롤 및 에러 감지 검사 보완
  const controls = Array.from(
    document.querySelectorAll(
      'input:not([type="hidden"]):not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="image"]), textarea, select'
    )
  );
  const unlabeled = [];
  let ariaInvalidCount = 0;
  for (const el of controls) {
    if (!visibleEnough(el)) continue;
    if (el.getAttribute("aria-invalid") === "true") ariaInvalidCount++;
    if (hasAccName(el)) continue;
    unlabeled.push({ selector: pick(el), type: el.getAttribute("type") || el.tagName });
  }

  // 3. 헤딩 구조 검사
  const headingTagCounts = { h1: 0, h2: 0, h3: 0, h4: 0, h5: 0, h6: 0 };
  for (let lv = 1; lv <= 6; lv++) {
    headingTagCounts["h" + lv] = document.querySelectorAll("h" + lv).length;
  }

  const headingItems = [];
  for (const el of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
    if (!visibleEnough(el)) continue;
    headingItems.push({
      el,
      level: parseInt(el.tagName[1], 10),
      kind: "tag",
      tagName: el.tagName.toLowerCase(),
    });
  }
  for (const el of document.querySelectorAll('[role="heading"]')) {
    if (/^H[1-6]$/i.test(el.tagName)) continue;
    if (!visibleEnough(el)) continue;
    const lv = parseInt(el.getAttribute("aria-level"), 10);
    if (Number.isNaN(lv) || lv < 1 || lv > 6) continue;
    headingItems.push({
      el,
      level: lv,
      kind: "aria-heading",
      tagName: (el.tagName || "").toLowerCase(),
    });
  }
  headingItems.sort((a, b) => {
    const p = a.el.compareDocumentPosition(b.el);
    if (p & Node.DOCUMENT_POSITION_FOLLOWING) return -1;
    if (p & Node.DOCUMENT_POSITION_PRECEDING) return 1;
    return 0;
  });

  const levels = headingItems.map((x) => x.level);
  const headingOutline = headingItems.map((x) => ({
    level: x.level,
    tagName: x.tagName,
    kind: x.kind,
    text: (x.el.textContent || "").trim().slice(0, 80),
    selector: pick(x.el),
  }));
  let skippedLevel = false;
  for (let i = 1; i < levels.length; i++) {
    if (levels[i] - levels[i - 1] > 1) {
      skippedLevel = true;
      break;
    }
  }
  let h1count = 0;
  for (const el of document.querySelectorAll("h1")) {
    if (visibleEnough(el)) h1count++;
  }
  for (const el of document.querySelectorAll('[role="heading"][aria-level="1"]')) {
    if (/^H1$/i.test(el.tagName)) continue;
    if (visibleEnough(el)) h1count++;
  }

  // 4. 테이블 검사
  const tables = Array.from(document.querySelectorAll("table"));
  const tableReport = [];
  for (const t of tables) {
    if (!visibleEnough(t)) continue;
    const role = (t.getAttribute("role") || "").toLowerCase();
    if (role === "presentation" || role === "none") continue;
    const cap = t.querySelector("caption");
    tableReport.push({
      selector: pick(t),
      hasCaption: !!cap,
    });
  }

  // 5. 키보드 접근성 및 포커스 링 숨김(:focus {outline:none}) 의심 검사 추가
  const tabPositive = Array.from(document.querySelectorAll("[tabindex]")).filter((el) => {
    const n = parseInt(el.getAttribute("tabindex"), 10);
    return !Number.isNaN(n) && n > 0;
  }).length;

  const focusableSel =
    'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const focusable = Array.from(document.querySelectorAll(focusableSel)).filter(visibleEnough);
  
  let hiddenFocusRingCount = 0;
  // 표본 조사: 상위 50개 인터랙티브 요소 스타일 추정 검사
  for (const el of focusable.slice(0, 50)) {
    const st = window.getComputedStyle(el);
    if (st.outlineStyle === "none" || parseInt(st.outlineWidth, 10) === 0) {
      hiddenFocusRingCount++;
    }
  }

  // 6. 랜드마크 검사
  const landmarks = {
    main: document.querySelectorAll("main,[role='main']").length,
    nav: document.querySelectorAll("nav,[role='navigation']").length,
    banner: document.querySelectorAll("header,[role='banner']").length,
    contentinfo: document.querySelectorAll("footer,[role='contentinfo']").length,
  };

  // 7. 다이얼로그 모달 검사
  const dialogs = Array.from(
    document.querySelectorAll('[role="dialog"],[role="alertdialog"]')
  );
  const dialogReport = dialogs.map((d) => ({
    selector: pick(d),
    ariaModal: d.getAttribute("aria-modal"),
    ariaLabel: !!(d.getAttribute("aria-label") && d.getAttribute("aria-label").trim()),
    ariaLabelledby: !!(d.getAttribute("aria-labelledby") && d.getAttribute("aria-labelledby").trim()),
  }));

  // [신규 필수가이드 1] iframe 타이틀 존재 유무 점검
  const iframes = Array.from(document.querySelectorAll("iframe"));
  const iframeIssues = [];
  for (const f of iframes) {
    if (!visibleEnough(f)) continue;
    const title = f.getAttribute("title");
    if (!title || !title.trim()) {
      iframeIssues.push({ kind: "missing_iframe_title", selector: pick(f) });
    }
  }

  // [신규 필수가이드 2] 새 창 링크(target="_blank") 알림 점검
  const links = Array.from(document.querySelectorAll("a[target='_blank']"));
  const newWindowIssues = [];
  for (const l of links) {
    if (!visibleEnough(l)) continue;
    const txt = (l.textContent || "").trim();
    const title = (l.getAttribute("title") || "").toLowerCase();
    const ariaLabel = (l.getAttribute("aria-label") || "").toLowerCase();
    
    // 텍스트나 title 속성에 '새창', '새 창', 'win', 'blank' 문구가 전혀 포함되어 있지 않은 경우 검출
    const hasWarning = /새\s*창|윈도우|새\s*탭|open|window|blank/i.test(txt + " " + title + " " + ariaLabel);
    if (!hasWarning) {
      newWindowIssues.push({ kind: "missing_new_window_warning", selector: pick(l) });
    }
  }

  return {
    document_language: { lang, hasPrimaryLang: !!lang },
    images: { total: imgs.length, issues: imgIssues.slice(0, 30) },
    forms: { controlsChecked: controls.length, missingAccessibleName: unlabeled.slice(0, 30), ariaInvalidCount },
    headings: {
      h1Count: h1count,
      multipleH1: h1count > 1,
      skippedLevel,
      tagCountsInDocument: headingTagCounts,
      outlineSample: headingOutline.slice(0, 25),
    },
    tables: tableReport.slice(0, 20),
    keyboard: {
      positiveTabindexElements: tabPositive,
      visibleFocusableApprox: focusable.length,
      hiddenFocusRingEstimate: hiddenFocusRingCount,
    },
    landmarks,
    dialogs: dialogReport.slice(0, 15),
    iframes: { total: iframes.length, issues: iframeIssues.slice(0, 20) },
    new_window_links: { total: links.length, issues: newWindowIssues.slice(0, 20) }
  };
})()
"""


def run_axe(page) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "error": None, "violations": []}
    try:
        page.add_script_tag(url=AXE_CDN)
        page.wait_for_function("typeof axe !== 'undefined'", timeout=15000)
    except Exception as e:
        out["error"] = f"axe 로드 실패: {e}"
        return out
    try:
        raw = page.evaluate(
            """async () => {
            const res = await axe.run(document, {
              runOnly: { type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa'] }
            });
            return {
              violations: res.violations.map(v => ({
                id: v.id,
                impact: v.impact,
                help: v.help,
                helpUrl: v.helpUrl,
                nodes: (v.nodes || []).slice(0, 5).map(n => ({
                  html: (n.html || '').slice(0, 400),
                  target: (n.target || []).join(' >> ')
                }))
              }))
            };
          }"""
        )
        out["ok"] = True
        out["violations"] = raw.get("violations", [])
    except Exception as e:
        out["error"] = f"axe 실행 실패: {e}"
    return out


def ax_snapshot_order(page) -> dict[str, Any]:
    try:
        snap = page.accessibility.snapshot(interesting_only=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    order: list[str] = []

    def walk(node: dict | None, depth: int) -> None:
        if node is None or len(order) >= 80:
            return
        role = node.get("role") or ""
        name = (node.get("name") or "").strip()
        if role and role not in ("generic", "InlineTextBox", "text"):
            bit = role + (": " + name[:60] if name else "")
            order.append(bit)
        for ch in node.get("children") or []:
            walk(ch, depth + 1)
            if len(order) >= 80:
                return

    walk(snap, 0)
    return {"ok": True, "readingOrderSample": order}


def summarize_checks(dom: dict, axe: dict) -> dict[str, Any]:
    """통과/주의 요약 플래그 계산기 (보완 신규 항목 반영)"""
    lang_ok = bool(dom.get("document_language", {}).get("hasPrimaryLang"))
    img_ok = len(dom.get("images", {}).get("issues", [])) == 0
    form_ok = len(dom.get("forms", {}).get("missingAccessibleName", [])) == 0
    h = dom.get("headings", {})
    heading_ok = not h.get("multipleH1") and not h.get("skippedLevel")
    tables = dom.get("tables", [])
    table_warn = any(not t.get("hasCaption") for t in tables) if tables else False
    kbd_warn = (dom.get("keyboard", {}).get("positiveTabindexElements") or 0) > 0
    
    # 신규 플래그 검사 조건식들
    focus_ring_warn = (dom.get("keyboard", {}).get("hiddenFocusRingEstimate") or 0) > 25
    iframe_ok = len(dom.get("iframes", {}).get("issues", [])) == 0
    new_window_ok = len(dom.get("new_window_links", {}).get("issues", [])) == 0

    main_ok = (dom.get("landmarks", {}).get("main") or 0) == 1
    dialogs = dom.get("dialogs", [])
    dialog_issues = []
    for d in dialogs:
        problems = []
        if d.get("ariaModal") != "true":
            problems.append("aria-modal=true 권장")
        if not d.get("ariaLabel") and not d.get("ariaLabelledby"):
            problems.append("접근 가능한 이름(aria-label / aria-labelledby) 필요")
        if problems:
            dialog_issues.append({"selector": d.get("selector"), "problems": problems})

    axe_v = axe.get("violations") or []
    axe_ok = axe.get("ok") and len(axe_v) == 0

    return {
        "document_language_ok": lang_ok,
        "images_alt_ok": img_ok,
        "form_names_ok": form_ok,
        "heading_structure_ok": heading_ok,
        "tables_have_caption_or_review": not table_warn,
        "keyboard_tabindex_positive_review": not kbd_warn,
        "keyboard_focus_visible_review": not focus_ring_warn, # 신규 추가
        "iframe_title_ok": iframe_ok,                          # 신규 추가
        "new_window_warning_ok": new_window_ok,                # 신규 추가
        "single_main_landmark": main_ok,
        "dialog_basic_ok": len(dialog_issues) == 0,
        "dialog_issues": dialog_issues,
        "axe_zero_violations": axe_ok,
        "axe_unavailable": (axe.get("error") if not axe.get("ok") else None),
    }


def scan_one_url(page, url: str, nav_timeout_ms: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "url": url,
        "fetched": False,
        "http_status": None,
        "error": None,
        "dom_checks": None,
        "axe": None,
        "accessibility_tree_sample": None,
        "summary_flags": None,
    }
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        row["http_status"] = resp.status if resp else None
        row["fetched"] = True
        time.sleep(0.85)
    except Exception as e:
        row["error"] = str(e)
        return row

    try:
        dom = page.evaluate(run_dom_checks_js())
        row["dom_checks"] = dom
    except Exception as e:
        row["dom_checks"] = {"error": str(e)}

    axe = run_axe(page)
    row["axe"] = axe

    snap = ax_snapshot_order(page)
    row["accessibility_tree_sample"] = snap

    dom_dict = row["dom_checks"] if isinstance(row["dom_checks"], dict) else {}
    row["summary_flags"] = summarize_checks(dom_dict, axe)
    return row


def format_heading_tag_summary(htc: dict[str, Any]) -> str:
    parts = []
    for k in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        parts.append(f"<{k}>:{int(htc.get(k, 0) or 0)}")
    return " ".join(parts)


def write_summary_txt(rows: list[dict[str, Any]], path: Path) -> None:
    lines: list[str] = []
    lines.append("웹접근성 점검 요약 (자동 검사 — 실제 인증/전문 감사를 대체하지 않습니다)")
    lines.append("")

    for r in rows:
        u = r.get("url", "")
        lines.append("=" * 72)
        lines.append(u)
        if r.get("error"):
            lines.append(f"   [페이지 로드 실패] {r['error']}")
            lines.append("")
            continue
        flags = r.get("summary_flags") or {}
        lines.append(f"   HTTP: {r.get('http_status')}")
        dom0 = r.get("dom_checks") or {}
        if isinstance(dom0, dict):
            htc = (dom0.get("headings") or {}).get("tagCountsInDocument")
            if isinstance(htc, dict) and htc:
                lines.append(
                    "   " + format_heading_tag_summary(htc) + " (문서 전체 태그 개수)"
                )
        for key, label in SUMMARY_FLAG_KEYS:
            ok = flags.get(key)
            sym = "OK" if ok else "점검"
            lines.append(f"   [{sym}] {label}")
            
        av = (r.get("axe") or {}).get("violations") or []
        if av:
            lines.append(f"   axe 위반 {len(av)}건 (상위 규칙 id):")
            for v in av[:12]:
                lines.append(f"     - {v.get('id')} ({v.get('impact')}) {v.get('help','')[:60]}")
        
        dom = r.get("dom_checks") or {}
        if isinstance(dom, dict):
            if dom.get("images", {}).get("issues"):
                lines.append("   이미지/맵 이슈 샘플:")
                for it in dom["images"]["issues"][:5]:
                    lines.append(f"     * {it.get('kind')} {it.get('selector','')}")
            
            if dom.get("forms", {}).get("missingAccessibleName"):
                lines.append("   이름 없는 폼 컨트롤 샘플:")
                for it in dom["forms"]["missingAccessibleName"][:5]:
                    lines.append(f"     * {it.get('type')} {it.get('selector','')}")

            if dom.get("iframes", {}).get("issues"):
                lines.append("   iframe 제목 누락 샘플:")
                for it in dom["iframes"]["issues"][:5]:
                    lines.append(f"     * {it.get('kind')} {it.get('selector','')}")

            if dom.get("new_window_links", {}).get("issues"):
                lines.append("   새 창 열림 안내 누락 샘플:")
                for it in dom["new_window_links"]["issues"][:5]:
                    lines.append(f"     * {it.get('kind')} {it.get('selector','')}")
                    
        ros = (r.get("accessibility_tree_sample") or {}).get("readingOrderSample") or []
        if ros:
            lines.append("   스크린리더 순서(추정, 상위 일부):")
            for line in ros[:12]:
                lines.append(f"     | {line}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


SUMMARY_FLAG_KEYS: list[tuple[str, str]] = [
    ("document_language_ok", "주언어(html lang)"),
    ("images_alt_ok", "이미지/맵 alt(누락·의심)"),
    ("form_names_ok", "폼 접근 가능한 이름"),
    ("heading_structure_ok", "제목 단계/다중 h1"),
    ("tables_have_caption_or_review", "테이블 caption(데이터 표)"),
    ("keyboard_tabindex_positive_review", "tabindex>0 없음(키보드 순서)"),
    ("keyboard_focus_visible_review", "포커스 시각적 표시(초점선 숨김 의심)"), # 추가
    ("iframe_title_ok", "프레임 제목 제공 (iframe title)"),                # 추가
    ("new_window_warning_ok", "새 창 링크 사전 알림 경고"),                 # 추가
    ("single_main_landmark", "main 랜드마크 1개"),
    ("dialog_basic_ok", "다이얼로그 기본 속성"),
    ("axe_zero_violations", "axe WCAG 위반 0건"),
]


def _ok_cell(v: object | None) -> str:
    if v is True:
        return "OK"
    if v is False:
        return "점검"
    return ""


def write_summary_xlsx(rows: list[dict[str, Any]], path: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError("openpyxl이 필요합니다: pip install openpyxl") from e

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"

    headers = (
        ["URL", "HTTP", "로드 오류"]
        + [label for _, label in SUMMARY_FLAG_KEYS]
        + [
            "axe 위반 수",
            "axe 규칙 id(일부)",
            "html lang",
            "tabindex>0 개수",
            "포커스 가능 요소(대략)",
            "포커스 숨김의심 수(추정)",
            "이미지/맵 이슈 수",
            "iframe 타이틀 누락 수",
            "새창경고 누락 수",
            "폼 이름없음 수",
            "보이는 대표제목(h1) 개수",
            "문서 전체 h태그 요약",
            "<h1>", "<h2>", "<h3>", "<h4>", "<h5>", "<h6>",
            "axe 로드/실행 오류",
        ]
    )
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for r in rows:
        u = r.get("url", "")
        err = r.get("error")
        http = r.get("http_status")
        flags = r.get("summary_flags") or {}
        dom = r.get("dom_checks") if isinstance(r.get("dom_checks"), dict) else {}
        axe = r.get("axe") or {}
        violations = axe.get("violations") or []
        rule_ids = ",".join(str(v.get("id", "")) for v in violations[:25] if v.get("id"))
        lang = (dom.get("document_language") or {}).get("lang") or ""
        kbd = dom.get("keyboard") or {}
        img_n = len((dom.get("images") or {}).get("issues") or [])
        iframe_n = len((dom.get("iframes") or {}).get("issues") or [])
        newwin_n = len((dom.get("new_window_links") or {}).get("issues") or [])
        form_n = len((dom.get("forms") or {}).get("missingAccessibleName") or [])
        h1c = (dom.get("headings") or {}).get("h1Count", "")
        htc = (dom.get("headings") or {}).get("tagCountsInDocument") or {}
        h_summary = format_heading_tag_summary(htc)
        axe_err = axe.get("error") or ""

        row_vals: list[object] = [
            u,
            http if http is not None else "",
            err or "",
        ]
        for key, _lab in SUMMARY_FLAG_KEYS:
            row_vals.append(_ok_cell(flags.get(key)))
        row_vals.extend(
            [
                len(violations),
                rule_ids[:32000],
                lang,
                (kbd or {}).get("positiveTabindexElements", ""),
                (kbd or {}).get("visibleFocusableApprox", ""),
                (kbd or {}).get("hiddenFocusRingEstimate", ""),
                img_n,
                iframe_n,
                newwin_n,
                form_n,
                h1c,
                h_summary,
                int(htc.get("h1", 0) or 0),
                int(htc.get("h2", 0) or 0),
                int(htc.get("h3", 0) or 0),
                int(htc.get("h4", 0) or 0),
                int(htc.get("h5", 0) or 0),
                int(htc.get("h6", 0) or 0),
                str(axe_err)[:500],
            ]
        )
        ws.append(row_vals)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    widths = (
        [56, 6, 36]
        + [14] * len(SUMMARY_FLAG_KEYS)
        + [10, 44, 10, 12, 14, 14, 10, 10, 10, 10, 8, 42, 7, 7, 7, 7, 7, 7, 40]
    )
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(w, 60)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def load_rows_from_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("results")
    if not isinstance(rows, list):
        raise ValueError("JSON에 results 배열이 없습니다.")
    return rows


def main(argv: Iterable[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(
        description="URL 목록 파일을 읽어 Playwright+axe로 웹접근성 점검을 수행합니다.",
    )
    p.add_argument("urls_file", nargs="?", type=Path, default=None)
    p.add_argument("--from-json", type=Path, default=None)
    p.add_argument("-o", "--json-out", type=Path, default=Path("a11y_report.json"))
    p.add_argument("--summary-out", type=Path, default=Path("a11y_report_summary.txt"))
    p.add_argument("--excel-out", type=Path, default=Path("a11y_report.xlsx"))
    p.add_argument("--no-excel", action="store_true")
    p.add_argument("--no-summary-txt", action="store_true")
    p.add_argument("--max-urls", type=int, default=0)
    p.add_argument("--timeout", type=int, default=35000)
    p.add_argument("--headed", action="store_true")
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="통합 결과(JSON/요약/엑셀)를 저장할 폴더",
    )
    p.add_argument(
        "--auto-out-dir",
        action="store_true",
        help="out-dir 미지정 시 reports/<url목록파일이름>/ 폴더를 자동 사용",
    )
    p.add_argument(
        "--no-auto-out-dir",
        action="store_true",
        help="(호환) 자동 out-dir 사용 안 함 — 현재 폴더에 a11y_report.* 저장",
    )
    p.add_argument(
        "--per-url-dirs",
        action="store_true",
        help="각 URL마다 pages/<폴더명>/ 에 url.txt, report.json, summary.txt 저장",
    )
    args = p.parse_args(argv)

    auto_out = args.auto_out_dir and not args.no_auto_out_dir
    run_out_dir = resolve_run_out_dir(
        args.urls_file if args.from_json is None else None,
        args.out_dir,
        auto_out_dir=auto_out,
    )
    if run_out_dir is not None:
        apply_out_dir_to_outputs(run_out_dir, args)

    if args.from_json is not None:
        if not args.from_json.is_file():
            print(f"JSON 파일이 없습니다: {args.from_json}", file=sys.stderr)
            return 1
        try:
            rows = load_rows_from_json(args.from_json)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(f"JSON 읽기 실패: {e}", file=sys.stderr)
            return 1
        if not args.no_summary_txt:
            write_summary_txt(rows, args.summary_out)
        if not args.no_excel:
            try:
                write_summary_xlsx(rows, args.excel_out)
            except RuntimeError as e:
                print(str(e), file=sys.stderr)
                return 2
        if args.per_url_dirs:
            base = run_out_dir if run_out_dir is not None else Path(".")
            write_per_url_dirs(rows, base / "pages")
        return 0

    if args.urls_file is None or not args.urls_file.is_file():
        print("urls_file 경로를 주거나, --from-json 으로 JSON만 변환하세요.", file=sys.stderr)
        return 1

    urls = read_urls(args.urls_file)
    if args.max_urls and args.max_urls > 0:
        urls = urls[: args.max_urls]
    if not urls:
        print("URL이 없습니다.", file=sys.stderr)
        return 1

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.urls_file.resolve()),
        "count": len(urls),
        "results": [],
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 패키지가 필요합니다: pip install playwright", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(locale="ko-KR")
        page = context.new_page()
        page.set_default_timeout(args.timeout)
        for i, url in enumerate(urls, start=1):
            print(f"[{i}/{len(urls)}] {url}", file=sys.stderr)
            row = scan_one_url(page, url, args.timeout)
            rows.append(row)
        browser.close()

    report["results"] = rows
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_summary_txt:
        write_summary_txt(rows, args.summary_out)
    if not args.no_excel:
        try:
            write_summary_xlsx(rows, args.excel_out)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 2
    if args.per_url_dirs:
        base = run_out_dir if run_out_dir is not None else Path(".")
        write_per_url_dirs(rows, base / "pages")

    print(f"JSON: {args.json_out.resolve()}", file=sys.stderr)
    if not args.no_summary_txt:
        print(f"요약: {args.summary_out.resolve()}", file=sys.stderr)
    if not args.no_excel:
        print(f"엑셀: {args.excel_out.resolve()}", file=sys.stderr)
    if run_out_dir is not None:
        print(f"결과 폴더: {run_out_dir.resolve()}", file=sys.stderr)
    if args.per_url_dirs:
        base = run_out_dir if run_out_dir is not None else Path(".")
        print(f"URL별 폴더: {(base / 'pages').resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())