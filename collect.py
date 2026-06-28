#!/usr/bin/env python3
"""
받소 (batso) v2 — 소상공인 지원사업 자동 수집기
수정: 기업마당 엔드포인트 교정, KPIPA 결과공고 필터, BEPA/BIPA/소상공인마당 스크래핑 보강
"""

import os, json, re, sys, traceback
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.date()
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M")

# ── API 키 ────────────────────────────────────────────
BIZINFO_KEY = os.environ.get("BIZINFO_JIWON_KEY", "")
GOV_KEY     = os.environ.get("GOV_HYETAEK_KEY", "")
KISED_KEY   = os.environ.get("KISED_JIWON_KEY", "")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA}

# ── 유틸 ──────────────────────────────────────────────
def parse_date(s):
    if not s or not s.strip():
        return None
    s = re.sub(r"[./]", "-", s.strip())
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # "2026.01.28~2026.02.25" 형태 → 종료일만 추출
    m = re.search(r"(\d{4}-\d{2}-\d{2})$", s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None

def days_left(deadline):
    if not deadline:
        return None
    return (deadline - TODAY).days

def is_active(deadline):
    return deadline is None or deadline >= TODAY

def safe(text, maxlen=200):
    if not text:
        return ""
    return text.strip()[:maxlen]

def prog(title, org, source, summary="", deadline=None, url=""):
    dl = parse_date(deadline) if isinstance(deadline, str) else deadline
    return {
        "title": safe(title),
        "org": safe(org),
        "source": source,
        "summary": safe(summary, 150),
        "deadline": dl.isoformat() if dl else None,
        "days_left": days_left(dl),
        "url": url or "",
    }


# ══════════════════════════════════════════════════════
# 1. 기업마당 — bizinfo.go.kr 직접 API + data.go.kr 프록시
# ══════════════════════════════════════════════════════
def fetch_bizinfo():
    tag = "기업마당"
    results = []

    # --- 방법 A: bizinfo.go.kr 직접 호출 ---
    url_a = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
    for page in range(1, 6):
        try:
            r = requests.get(url_a, params={
                "dataType": "json",
                "crtfcKey": BIZINFO_KEY,  # data.go.kr 키로 시도
                "numOfRows": 100,
                "pageNo": page,
            }, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                print(f"[{tag}] 직접API HTTP {r.status_code}")
                break
            data = r.json()
            # 응답 구조 탐색 (jsonArray 또는 items)
            items = (data.get("jsonArray") or
                     data.get("items") or
                     data.get("response", {}).get("body", {}).get("items", {}).get("item", []) or
                     [])
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            if not items:
                if page == 1:
                    print(f"[{tag}] 직접API 응답 키: {list(data.keys())[:5]}")
                break
            for it in items:
                title = it.get("title") or it.get("pblancNm") or ""
                if not title.strip():
                    continue
                # 신청기간 파싱
                period = it.get("reqstBeginEndDe") or it.get("reqstDt") or ""
                end_str = it.get("reqstEndDe") or ""
                if not end_str and "~" in period:
                    end_str = period.split("~")[-1].strip()
                end = parse_date(end_str)
                if end and not is_active(end):
                    continue
                link = it.get("link") or it.get("pblancUrl") or it.get("detailUrl") or ""
                if link and link.startswith("/"):
                    link = "https://www.bizinfo.go.kr" + link
                results.append(prog(
                    title=title,
                    org=it.get("author") or it.get("jrsdInsttNm") or it.get("excInsttNm") or "",
                    source=tag,
                    summary=it.get("description") or it.get("bsnsSumryCn") or "",
                    deadline=end,
                    url=link or "https://www.bizinfo.go.kr",
                ))
        except Exception as e:
            print(f"[{tag}] 직접API 오류 p{page}: {e}")
            break

    # --- 방법 B: data.go.kr 프록시 (A가 0건이면) ---
    if not results:
        print(f"[{tag}] 직접API 0건 → data.go.kr 프록시 시도")
        base = "https://apis.data.go.kr/1421000/bizinfo"
        for path in ["", "/getSupportBizList", "/getJiwonList"]:
            try:
                r = requests.get(base + path, params={
                    "serviceKey": BIZINFO_KEY,
                    "pageNo": 1, "numOfRows": 100, "dataType": "json",
                }, timeout=30)
                print(f"[{tag}] data.go.kr{path} → HTTP {r.status_code}")
                if r.status_code == 200:
                    txt = r.text[:500]
                    print(f"[{tag}] 응답 미리보기: {txt}")
                    if "SERVICE_KEY_IS_NOT_REGISTERED" in txt or "error" in txt.lower():
                        continue
                    data = r.json()
                    body = data
                    for k in ("response", "body"):
                        if isinstance(body, dict) and k in body:
                            body = body[k]
                    items = body.get("items", body.get("item", []))
                    if isinstance(items, dict):
                        items = items.get("item", [])
                    if isinstance(items, list) and items:
                        print(f"[{tag}] data.go.kr{path} → {len(items)}건 발견!")
                        for it in items:
                            title = it.get("pblancNm") or it.get("title") or it.get("sj") or ""
                            if not title.strip():
                                continue
                            end = parse_date(it.get("reqstEndDe") or it.get("endDt") or "")
                            if end and not is_active(end):
                                continue
                            results.append(prog(
                                title=title,
                                org=it.get("jrsdInsttNm") or it.get("excInsttNm") or "",
                                source=tag,
                                summary=it.get("bsnsSumryCn") or it.get("cn") or "",
                                deadline=end,
                                url=it.get("detailUrl") or it.get("pblancUrl") or "https://www.bizinfo.go.kr",
                            ))
                        break
            except Exception as e:
                print(f"[{tag}] data.go.kr{path} 오류: {e}")

    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 2. 보조금24 API
# ══════════════════════════════════════════════════════
def fetch_gov():
    tag = "보조금24"
    if not GOV_KEY:
        print(f"[{tag}] 키 없음")
        return []
    results = []
    # 여러 엔드포인트 시도
    bases = [
        "https://apis.data.go.kr/1741000/publicServiceInfo/getPublicServiceInfoList",
        "https://apis.data.go.kr/1741000/svcOferInfo/getSvcOferInfoList",
    ]
    for base in bases:
        try:
            r = requests.get(base, params={
                "serviceKey": GOV_KEY, "pageNo": 1,
                "numOfRows": 100, "dataType": "json",
            }, timeout=30)
            print(f"[{tag}] {base.split('/')[-1]} → HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            txt = r.text[:500]
            if "SERVICE_KEY_IS_NOT_REGISTERED" in txt:
                print(f"[{tag}] 키 미등록")
                continue
            data = r.json()
            body = data
            for k in ("response", "body"):
                if isinstance(body, dict) and k in body:
                    body = body[k]
            items = body.get("items", body.get("item", []))
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = []
            if not items:
                print(f"[{tag}] 응답 키: {list(data.keys())[:5] if isinstance(data, dict) else 'not dict'}")
                continue
            print(f"[{tag}] {len(items)}건 발견!")
            for it in items:
                title = it.get("servNm") or it.get("sj") or it.get("title") or ""
                if not title.strip():
                    continue
                results.append(prog(
                    title=title,
                    org=it.get("jurMnofNm") or it.get("insttNm") or "",
                    source=tag,
                    summary=it.get("servDgst") or it.get("cn") or "",
                    url=it.get("servDtlLink") or "https://www.gov.kr",
                ))
            break
        except Exception as e:
            print(f"[{tag}] 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 3. 창업진흥원 K-Startup API
# ══════════════════════════════════════════════════════
def fetch_kised():
    tag = "K-Startup"
    if not KISED_KEY:
        print(f"[{tag}] 키 없음")
        return []
    results = []
    bases = [
        "https://apis.data.go.kr/1840000/NationalStartupList/getStartupList",
        "https://apis.data.go.kr/1840000/kcStartup/getStartupPblancList",
    ]
    for base in bases:
        try:
            r = requests.get(base, params={
                "serviceKey": KISED_KEY, "pageNo": 1,
                "numOfRows": 200, "dataType": "json",
            }, timeout=30)
            print(f"[{tag}] {base.split('/')[-1]} → HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            txt = r.text[:500]
            if "SERVICE_KEY_IS_NOT_REGISTERED" in txt:
                continue
            data = r.json()
            body = data
            for k in ("response", "body"):
                if isinstance(body, dict) and k in body:
                    body = body[k]
            items = body.get("items", body.get("item", []))
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = []
            if not items:
                print(f"[{tag}] 응답 키: {list(data.keys())[:5] if isinstance(data, dict) else 'not dict'}")
                continue
            print(f"[{tag}] {len(items)}건 발견!")
            for it in items:
                title = it.get("pblancNm") or it.get("title") or ""
                if not title.strip():
                    continue
                end = parse_date(it.get("endDt") or it.get("reqstEndDe") or "")
                if end and not is_active(end):
                    continue
                results.append(prog(
                    title=title,
                    org=it.get("excInsttNm") or it.get("insttNm") or "창업진흥원",
                    source=tag,
                    summary=it.get("cn") or it.get("bsnsSumryCn") or "",
                    deadline=end,
                    url=it.get("detailUrl") or it.get("pblancUrl") or "https://www.k-startup.go.kr",
                ))
            break
        except Exception as e:
            print(f"[{tag}] 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 4. 소상공인마당 (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_sbiz():
    tag = "소상공인마당"
    results = []
    urls = [
        "https://www.sbiz.or.kr/sup/policy/livePolicyList.do",
        "https://www.sbiz24.kr/sup/policy/livePolicyList.do",
        "https://www.semas.or.kr/web/board/webBoardList.kmdc?bCd=notice",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            r.encoding = "utf-8"
            if r.status_code != 200:
                print(f"[{tag}] {url} → HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # 다양한 게시판 셀렉터 시도
            rows = (soup.select("table.board_list tbody tr") or
                    soup.select("table.tbl_list tbody tr") or
                    soup.select("div.board_list li") or
                    soup.select("ul.board_list li") or
                    soup.select("table tbody tr"))
            if not rows:
                # 테이블이 아닌 경우 모든 링크 중 공고성 링크 필터
                for a in soup.select("a[href]"):
                    txt = a.get_text(strip=True)
                    href = a.get("href", "")
                    if len(txt) >= 10 and ("지원" in txt or "공고" in txt or "모집" in txt or "사업" in txt):
                        if not href.startswith("http"):
                            href = urljoin(url, href)
                        if not any(r["title"] == txt for r in results):
                            results.append(prog(txt, "소상공인시장진흥공단", tag, url=href))
            else:
                for row in rows[:30]:
                    a = row.select_one("a")
                    if not a:
                        continue
                    txt = a.get_text(strip=True)
                    if len(txt) < 8:
                        continue
                    href = a.get("href", "")
                    if not href.startswith("http"):
                        href = urljoin(url, href)
                    if not any(r["title"] == txt for r in results):
                        results.append(prog(txt, "소상공인시장진흥공단", tag, url=href))
            if results:
                break
        except Exception as e:
            print(f"[{tag}] {url} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 5. KPIPA (스크래핑) — [결과공고] 필터 추가
# ══════════════════════════════════════════════════════
def fetch_kpipa():
    tag = "KPIPA"
    results = []
    SKIP = ["결과공고", "선정 결과", "선정결과", "합격자", "선정 공고", "선정공고",
            "취소 공고", "취소공고", "철회 공고", "철회공고"]

    for domain in ["https://new.kpipa.or.kr", "https://www.kpipa.or.kr"]:
        url = f"{domain}/p/g1_2"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.encoding = "utf-8"
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                title = a.get_text(strip=True)
                if not title or len(title) < 10:
                    continue
                href = a.get("href", "")
                if "/p/g1_2/" not in href and "/g1_2/" not in href:
                    continue
                # ★ 결과공고 필터
                if any(kw in title for kw in SKIP):
                    continue
                if not href.startswith("http"):
                    href = urljoin(domain, href)
                if not any(r["title"] == title for r in results):
                    results.append(prog(title, "한국출판문화산업진흥원(KPIPA)", tag, url=href))
            if results:
                break
        except Exception as e:
            print(f"[{tag}] {domain} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 6. BEPA (스크래핑) — 정확한 게시판 URL
# ══════════════════════════════════════════════════════
def fetch_bepa():
    tag = "BEPA"
    results = []
    pages = [
        ("https://www.bepa.kr/kor/view.do?no=1502", "소상공인"),
        ("https://www.bepa.kr/kor/view.do?no=1505", "지역기업"),
        ("https://www.bepa.kr/kor/view.do?no=1504", "산업인력"),
        ("https://www.bepa.kr/kor/view.do?no=1508", "공고공지"),
    ]
    for url, cat in pages:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.encoding = "utf-8"
            if r.status_code != 200:
                print(f"[{tag}] {cat} HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # BEPA 게시판 링크 패턴: /kor/view.do?...idx=NNN...view=view
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if "idx=" not in href or "view=view" not in href:
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 8:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.bepa.kr", href)
                if not any(r["title"] == title for r in results):
                    results.append(prog(title, "부산경제진흥원(BEPA)", tag, url=href))
        except Exception as e:
            print(f"[{tag}] {cat} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 7. BIPA (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_bipa():
    tag = "BIPA"
    results = []
    urls = [
        "https://www.bipa.or.kr/board/list.do?boardId=BBS_0000010",
        "https://www.bipa.or.kr/board/list.do?boardId=BBS_0000002",
        "https://www.bipa.or.kr/main/board/list.do?boardId=BBS_0000010",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            r.encoding = "utf-8"
            if r.status_code != 200:
                print(f"[{tag}] {url} → HTTP {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title or len(title) < 8:
                    continue
                if "board" not in href and "view" not in href and "idx" not in href:
                    continue
                # 메뉴/헤더 링크 제외
                if any(kw in title for kw in ["로그인", "회원가입", "홈", "메뉴", "사이트맵"]):
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.bipa.or.kr", href)
                if not any(r["title"] == title for r in results):
                    results.append(prog(title, "부산정보산업진흥원(BIPA)", tag, url=href))
            if results:
                break
        except Exception as e:
            print(f"[{tag}] {url} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results


# ══════════════════════════════════════════════════════
# 후처리 + HTML 생성
# ══════════════════════════════════════════════════════
def dedupe(programs):
    seen = set()
    out = []
    for p in programs:
        key = p["title"][:40]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

def sort_programs(programs):
    def key(p):
        d = p["days_left"]
        return d if d is not None else 99999
    return sorted(programs, key=key)

def make_html(programs):
    total = len(programs)
    urgent = sum(1 for p in programs if p["days_left"] is not None and 0 <= p["days_left"] <= 7)
    cards = ""
    for p in programs:
        dl = p["days_left"]
        if dl is None:
            dday, cls_d, cls_c = "상시", "g", "always"
        elif dl <= 0:
            dday, cls_d, cls_c = "오늘 마감", "r", "urgent"
        elif dl <= 7:
            dday, cls_d, cls_c = f"D-{dl}", "r", "urgent"
        else:
            dday, cls_d, cls_c = f"D-{dl}", "y", "normal"
        sm = f'<div class="desc">{p["summary"]}</div>' if p["summary"] else ""
        cards += f"""<div class="card {cls_c}" data-source="{p['source']}" data-title="{p['title'].lower()}" data-org="{p['org'].lower()}">
<div class="top"><span class="field">{p['source']}</span><span class="dday {cls_d}">{dday}</span></div>
<h3>{p['title']}</h3><div class="org">{p['org']}</div>{sm}
<a class="link" href="{p['url']}" target="_blank" rel="noopener">공고 원문 →</a></div>\n"""

    srcs = sorted(set(p["source"] for p in programs))
    opts = "".join(f'<option value="{s}">{s}</option>' for s in srcs)
    src_str = " · ".join(srcs) if srcs else "없음"

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>받소 — 소상공인 지원사업</title><style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
*{{box-sizing:border-box;margin:0}}body{{font-family:'Pretendard',sans-serif;background:#FBFAF6;color:#1C1B17;padding:20px;max-width:720px;margin:0 auto}}
.hd h1{{font-size:22px;font-weight:800;color:#117A56}}.hd p{{font-size:13px;color:#6E6A60;margin-top:4px}}
.stats{{display:flex;gap:12px;font-size:13px;color:#6E6A60;margin:12px 0}}.stats b{{color:#1C1B17}}.stats .hot{{color:#C0392B}}
.bar{{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}}.bar input,.bar select{{padding:10px 12px;border:1px solid #EAE6DB;border-radius:9px;font-family:inherit;font-size:13px;background:#fff}}.bar input{{flex:1;min-width:200px}}.bar input:focus,.bar select:focus{{outline:2px solid #117A56}}
.card{{background:#fff;border:1px solid #EAE6DB;border-radius:12px;padding:14px 16px;margin-bottom:10px}}.card.urgent{{border-left:4px solid #C0392B}}.card.normal{{border-left:4px solid #B57500}}.card.always{{border-left:4px solid #117A56}}.card.hide{{display:none}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}.field{{font-size:11px;font-weight:700;color:#6E6A60}}
.dday{{font-size:12px;font-weight:700;padding:2px 8px;border-radius:99px}}.dday.r{{background:#FBEAE5;color:#C0392B}}.dday.y{{background:#FBF1DC;color:#B57500}}.dday.g{{background:#E6F2EC;color:#117A56}}
.card h3{{font-size:15px;font-weight:700;margin-bottom:3px;line-height:1.4}}.org{{font-size:12px;color:#6E6A60;margin-bottom:6px}}.desc{{font-size:13px;line-height:1.5;color:#3a382f;margin-bottom:6px}}
a.link{{display:inline-block;margin-top:6px;font-size:12px;font-weight:700;color:#117A56;text-decoration:none}}a.link:hover{{text-decoration:underline}}
.empty{{text-align:center;padding:40px;color:#6E6A60;font-size:14px;display:none}}.ft{{margin-top:24px;font-size:11px;color:#6E6A60;text-align:center;line-height:1.6}}
</style></head><body>
<div class="hd"><h1>받소</h1><p>마지막 수집: {TIMESTAMP} (KST)</p></div>
<div class="stats"><span>총 <b>{total}</b>건</span><span>마감 임박 <b class="hot">{urgent}</b>건</span></div>
<div class="bar"><input type="text" id="q" placeholder="검색" oninput="ft()">
<select id="src" onchange="ft()"><option value="">전체</option>{opts}</select></div>
<div id="list">{cards}</div><div class="empty" id="empty">조건에 맞는 공고가 없어요</div>
<div class="ft">출처: {src_str}</div>
<script>function ft(){{var q=document.getElementById("q").value.toLowerCase(),s=document.getElementById("src").value,c=document.querySelectorAll(".card"),n=0;c.forEach(function(e){{var ok=true;if(q){{var t=e.getAttribute("data-title")+e.getAttribute("data-org");if(t.indexOf(q)<0)ok=false}}if(s&&e.getAttribute("data-source")!==s)ok=false;e.classList.toggle("hide",!ok);if(ok)n++}});document.getElementById("empty").style.display=n?"none":"block"}}</script>
</body></html>"""


# ══════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"=== 받소 v2 수집 시작: {TIMESTAMP} ===\n")
    all_programs = []
    collectors = [
        ("기업마당", fetch_bizinfo),
        ("보조금24", fetch_gov),
        ("K-Startup", fetch_kised),
        ("소상공인마당", fetch_sbiz),
        ("KPIPA", fetch_kpipa),
        ("BEPA", fetch_bepa),
        ("BIPA", fetch_bipa),
    ]
    for name, fn in collectors:
        print(f"\n--- {name} ---")
        try:
            all_programs.extend(fn())
        except Exception as e:
            print(f"[{name}] 치명적: {e}")
            traceback.print_exc()
    all_programs = sort_programs(dedupe(all_programs))
    print(f"\n=== 총 {len(all_programs)}건 ===")
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(make_html(all_programs))
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": TIMESTAMP, "total": len(all_programs), "programs": all_programs}, f, ensure_ascii=False, indent=2)
    print("완료")
