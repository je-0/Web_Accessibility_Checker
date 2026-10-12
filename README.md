# 사이트 IA · 웹접근성 자동 점검

URL 목록을 만들고, Playwright + [axe-core](https://github.com/dequelabs/axe-core) + DOM 규칙으로 페이지별 웹접근성을 자동 점검한 뒤 **JSON / 요약 TXT / 엑셀**로 저장하는 도구 모음입니다.

> 자동 검사 결과는 **WA 인증·전문 감사를 대체하지 않습니다.** 참고·1차 스크리닝용으로 사용하세요.

## 프로젝트 구성

| 파일 | 설명 |
|------|------|
| `crawl_urls.py` | XML 사이트맵에서 URL 수집 → `.txt` |
| `container_urls.py` | HTML 페이지 특정 영역의 링크 수집 → `.txt` |
| `a11y_scan.py` | URL 목록 접근성 검사 → 보고서 |
| `requirements.txt` | Python 패키지 목록 |

URL 목록(`.txt`)과 검사 결과(`reports/`)는 실행할 때마다 생성합니다. 저장소에는 스크립트만 두는 것을 권장합니다.

## 설치 (최초 1회)

```bash
cd "C:\Users\Administrator\Desktop\사이트ia"
pip install -r requirements.txt
playwright install chromium
```

## 전체 흐름

```
[1] URL 추출                    [2] 접근성 검사                 [3] 결과 확인
crawl_urls.py (XML 사이트맵)  →  a11y_scan.py              →  reports/<이름>/
container_urls.py (HTML 링크)      --auto-out-dir --per-url-dirs
```

---

## 1단계: URL 목록 만들기

목록 파일 형식: **한 줄에 URL 하나**, `#`으로 시작하는 줄과 빈 줄은 무시.

### A. XML 사이트맵 (페이지 많을 때 추천)

`robots.txt`의 `Sitemap:` 및 `/sitemap.xml`, `/sitemap_index.xml` 등을 따라갑니다.

```bash
python crawl_urls.py https://www.example.com -o urls.txt
```

| 옵션 | 설명 |
|------|------|
| `url` | 사이트 주소 (필수) |
| `-o`, `--output` | 저장 파일 (기본: `sitemap_urls_<호스트>.txt`) |
| `--timeout` | 요청 타임아웃 초 (기본 20) |

### B. HTML 한 페이지 / HTML 사이트맵

쇼핑몰·브랜드 사이트처럼 **HTML 사이트맵**만 있는 경우:

```bash
python container_urls.py -u "https://hanyul.com/util/sitemap.html" -s body -o hanyul.txt
```

일반 페이지에서 `#container` 안 링크만:

```bash
python container_urls.py -u https://www.example.com -o urls.txt
```

| 옵션 | 설명 |
|------|------|
| `-u`, `--url` | 페이지 URL (`-f`와 택일) |
| `-f`, `--file` | 로컬 HTML 파일 |
| `-s`, `--selector` | 링크를 모을 CSS 선택자 (기본 `#container`) |
| `-o`, `--output` | 저장 txt (없으면 터미널 출력) |
| `--base-url` | `-f` 사용 시 상대 경로 기준 URL |
| `--no-fallback` | `#contents`, `#wrap` 자동 대체 끄기 |
| `--fallback` | 대체 선택자 목록 (기본 `#contents,#wrap`) |

---

## 2단계: 접근성 검사

### 권장 (사이트별 폴더 + URL마다 하위 폴더)

목록 파일 이름이 `hanyul.txt`이면 결과는 `reports/hanyul/` 아래에 저장됩니다.

```bash
python a11y_scan.py hanyul.txt --auto-out-dir --per-url-dirs
```

### 출력 구조

```
reports/hanyul/
├── a11y_report.json          # 전체 원본
├── a11y_report_summary.txt   # URL별 OK/점검 요약
├── a11y_report.xlsx          # 엑셀 요약
└── pages/
    └── hanyul.com__best_index.html/
        ├── url.txt
        ├── report.json
        └── summary.txt
```

- `pages/` 폴더명: 호스트 + 경로를 Windows 경로에 맞게 변환. 중복 시 `__2`, `__3` 접미사.

### 기타 실행 예

```bash
# 현재 폴더에만 통합 결과 3종 (예전 방식)
python a11y_scan.py urls.txt

# 저장 폴더 직접 지정
python a11y_scan.py urls.txt --out-dir reports/my-site --per-url-dirs

# 앞 5개만, 브라우저 창 표시
python a11y_scan.py urls.txt --max-urls 5 --headed --auto-out-dir --per-url-dirs

# JSON만 다시 TXT/엑셀/pages 생성 (재크롤링 없음)
python a11y_scan.py --from-json reports/hanyul/a11y_report.json --out-dir reports/hanyul --per-url-dirs
```

### `a11y_scan.py` 옵션

| 옵션 | 설명 |
|------|------|
| `urls_file` | URL 목록 txt |
| `--from-json` | 기존 JSON에서 보고서만 재생성 |
| `--auto-out-dir` | `reports/<목록파일 stem>/` 사용 |
| `--out-dir` | 결과 폴더 직접 지정 |
| `--per-url-dirs` | `pages/<slug>/` per URL |
| `--no-auto-out-dir` | 자동 폴더 끄기 |
| `-o`, `--json-out` | JSON 경로 (기본 `a11y_report.json`) |
| `--summary-out` | 요약 TXT 경로 |
| `--excel-out` | 엑셀 경로 |
| `--no-excel` / `--no-summary-txt` | 해당 출력 생략 |
| `--max-urls` | 앞 N개만 검사 (0=전체) |
| `--timeout` | ms (기본 35000) |
| `--headed` | headless 끄기 |

---

## 3단계: 결과 읽기

- **요약 TXT / 엑셀**: URL마다 `[OK]` / `[점검]` — 주언어, alt, 폼 이름, 제목 구조, iframe title, 새 창 안내, axe 위반 수 등.
- **JSON**: axe 위반 상세(규칙 id, impact, HTML 샘플), DOM 검사, 접근성 트리 읽기 순서 샘플.

### 자동 점검 항목 (요약)

- axe-core: WCAG 2.0 A/AA, 2.1 AA 태그
- `html lang`, 이미지/`area` alt, 폼 접근 가능한 이름, `aria-invalid` 카운트
- heading 단계·다중 h1, 테이블 caption, `tabindex>0`
- 포커스 링 숨김 의심(`outline`), 랜드마크(`main` 등), dialog 속성
- iframe `title`, `target="_blank"` 새 창 안내 문구

---

## 예시: 한율 (HTML 사이트맵)

```bash
python container_urls.py -u "https://hanyul.com/util/sitemap.html" -s body -o hanyul.txt
python a11y_scan.py hanyul.txt --auto-out-dir --per-url-dirs
```

## 예시: XML 사이트맵 사이트

```bash
python crawl_urls.py https://www.thenorthfacekorea.co.kr/ -o northface.txt
python a11y_scan.py northface.txt --auto-out-dir --per-url-dirs
```

URL이 수백 개면 `--max-urls 10`으로 먼저 확인한 뒤 전체 실행하세요.

---

## 문제 해결

| 증상 | 조치 |
|------|------|
| `playwright 패키지가 필요합니다` | `pip install playwright` 후 `playwright install chromium` |
| `openpyxl이 필요합니다` | `pip install openpyxl` |
| 사이트맵을 가져오지 못했습니다 | HTML 사이트맵이면 `container_urls.py` 사용 |
| `python` 명령 없음 (Windows) | `py crawl_urls.py ...` 형태로 실행 |

---

## 라이선스·책임

내부 점검용 스크립트입니다. 대상 사이트 robots·이용약관을 준수하고, 트래픽이 큰 사이트는 `--max-urls`와 실행 간격을 조절하세요.
