#!/usr/bin/env python3
"""
받소 (batso) — 소상공인 지원사업 자동 수집기
매일 오후 6시(KST) GitHub Actions로 실행.
7개 소스에서 공고를 수집 → 마감 안 된 것만 index.html로 정리.
"""

import os, json, re, sys, traceback
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, quote
import requests
from bs4 import BeautifulSoup

# ── 시간 ──────────────────────────────────────────────
KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = NOW.date()
TIMESTAMP = NOW.strftime("%Y-%m-%d %H:%M")

# ── API 키 (GitHub Secrets) ───────────────────────────
BIZINFO_KEY = os.environ.get("BIZINFO_JIWON_KEY", "")
GOV_KEY = os.environ.get("GOV_HYETAEK_KEY", "")
KISED_KEY = os.environ.get("KISED_JIWON_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

# ── 유틸 ──────────────────────────────────────────────
def parse_date(s):
    if not s or not s.strip():
        return None
    s = s.strip().replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).date()
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

# ══════════════════════════════════════════════════════
# 소스 1: 기업마당 API
# ══════════════════════════════════════════════════════
def fetch_bizinfo():
    tag = "기업마당"
    if not BIZINFO_KEY:
        print(f"[{tag}] API 키 없음, 건너뜀")
        return []
    results = []
    base = "https://apis.data.go.kr/1421000/bizinfo/getSupportBizList"
    for page in range(1, 6):
        try:
            r = requests.get(base, params={
                "serviceKey": BIZINFO_KEY,
                "pageNo": page,
                "numOfRows": 100,
                "dataType": "json",
            }, timeout=30)
            if r.status_code != 200:
                print(f"[{tag}] HTTP {r.status_code} (p{page})")
                # 응답 본문 일부 출력 (디버깅)
                print(f"[{tag}] 응답: {r.text[:300]}")
                break
            data = r.json()
            # data.go.kr 표준 응답 구조 탐색
            body = data
            for k in ("response", "body"):
                if isinstance(body, dict) and k in body:
                    body = body[k]
            items = body.get("items", body.get("item", []))
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            if not items:
                break
            for it in items:
                title = it.get("pblancNm") or it.get("sj") or it.get("title") or ""
                if not title.strip():
                    continue
                end = parse_date(it.get("reqstEndDe") or it.get("endDt") or "")
                if not is_active(end):
                    continue
                results.append({
                    "title": safe(title),
                    "org": safe(it.get("jrsdInsttNm") or it.get("excInsttNm") or ""),
                    "source": tag,
                    "summary": safe(it.get("bsnsSumryCn") or it.get("cn") or "", 150),
                    "deadline": end.isoformat() if end else None,
                    "days_left": days_left(end),
                    "url": it.get("detailUrl") or it.get("rceptUrl") or it.get("pblancUrl") or "https://www.bizinfo.go.kr",
                })
        except Exception as e:
            print(f"[{tag}] 오류 p{page}: {e}")
            break
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 2: 보조금24 API
# ══════════════════════════════════════════════════════
def fetch_gov():
    tag = "보조금24"
    if not GOV_KEY:
        print(f"[{tag}] API 키 없음, 건너뜀")
        return []
    results = []
    base = "https://apis.data.go.kr/1741000/publicServiceInfo/getPublicServiceInfoList"
    for page in range(1, 4):
        try:
            r = requests.get(base, params={
                "serviceKey": GOV_KEY,
                "pageNo": page,
                "numOfRows": 100,
                "dataType": "json",
            }, timeout=30)
            if r.status_code != 200:
                print(f"[{tag}] HTTP {r.status_code}")
                print(f"[{tag}] 응답: {r.text[:300]}")
                break
            data = r.json()
            body = data
            for k in ("response", "body"):
                if isinstance(body, dict) and k in body:
                    body = body[k]
            items = body.get("items", body.get("item", []))
            if isinstance(items, dict):
                items = items.get("item", [])
            if not isinstance(items, list):
                items = [items] if items else []
            if not items:
                break
            for it in items:
                title = it.get("servNm") or it.get("sj") or ""
                if not title.strip():
                    continue
                results.append({
                    "title": safe(title),
                    "org": safe(it.get("jurMnofNm") or it.get("insttNm") or ""),
                    "source": tag,
                    "summary": safe(it.get("servDgst") or it.get("cn") or "", 150),
                    "deadline": None,
                    "days_left": None,
                    "url": it.get("servDtlLink") or "https://www.gov.kr",
                })
        except Exception as e:
            print(f"[{tag}] 오류: {e}")
            break
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 3: 창업진흥원 K-Startup API
# ══════════════════════════════════════════════════════
def fetch_kised():
    tag = "K-Startup"
    if not KISED_KEY:
        print(f"[{tag}] API 키 없음, 건너뜀")
        return []
    results = []
    base = "https://apis.data.go.kr/1840000/NationalStartupList/getStartupList"
    try:
        r = requests.get(base, params={
            "serviceKey": KISED_KEY,
            "pageNo": 1,
            "numOfRows": 200,
            "dataType": "json",
        }, timeout=30)
        if r.status_code != 200:
            print(f"[{tag}] HTTP {r.status_code}")
            print(f"[{tag}] 응답: {r.text[:300]}")
            return []
        data = r.json()
        body = data
        for k in ("response", "body"):
            if isinstance(body, dict) and k in body:
                body = body[k]
        items = body.get("items", body.get("item", []))
        if isinstance(items, dict):
            items = items.get("item", [])
        if not isinstance(items, list):
            items = [items] if items else []
        for it in items:
            title = it.get("pblancNm") or it.get("title") or it.get("sj") or ""
            if not title.strip():
                continue
            end = parse_date(it.get("endDt") or it.get("reqstEndDe") or "")
            if not is_active(end):
                continue
            results.append({
                "title": safe(title),
                "org": safe(it.get("excInsttNm") or it.get("insttNm") or "창업진흥원"),
                "source": tag,
                "summary": safe(it.get("cn") or it.get("bsnsSumryCn") or "", 150),
                "deadline": end.isoformat() if end else None,
                "days_left": days_left(end),
                "url": it.get("detailUrl") or it.get("pblancUrl") or "https://www.k-startup.go.kr",
            })
    except Exception as e:
        print(f"[{tag}] 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 4: 소상공인마당 (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_sbiz():
    tag = "소상공인마당"
    results = []
    url = "https://www.sbiz.or.kr/sup/policy/livePolicyList.do"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href]"):
            title = a.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            href = a.get("href", "")
            # 지원사업 공고 링크만 필터
            if "policy" not in href and "sup" not in href:
                continue
            if href and not href.startswith("http"):
                href = urljoin("https://www.sbiz.or.kr", href)
            if any(r["title"] == title for r in results):
                continue
            results.append({
                "title": safe(title),
                "org": "소상공인시장진흥공단",
                "source": tag,
                "summary": "",
                "deadline": None,
                "days_left": None,
                "url": href or url,
            })
    except Exception as e:
        print(f"[{tag}] 오류: {e}")
        traceback.print_exc()
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 5: KPIPA (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_kpipa():
    tag = "KPIPA"
    results = []
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
                if not title or len(title) < 8:
                    continue
                # 결과공고 제외
                if any(kw in title for kw in ["선정 결과", "선정결과", "합격자", "결과 공고"]):
                    continue
                href = a.get("href", "")
                if "/p/g1_2/" not in href and "/g1_2/" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin(domain, href)
                if any(r["title"] == title for r in results):
                    continue
                results.append({
                    "title": safe(title),
                    "org": "한국출판문화산업진흥원(KPIPA)",
                    "source": tag,
                    "summary": "",
                    "deadline": None,
                    "days_left": None,
                    "url": href,
                })
            if results:
                break
        except Exception as e:
            print(f"[{tag}] {domain} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 6: BEPA (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_bepa():
    tag = "BEPA"
    results = []
    pages = [
        ("https://www.bepa.kr/kor/view.do?no=1502", "소상공인"),
        ("https://www.bepa.kr/kor/view.do?no=1505", "지역기업"),
    ]
    for url, cat in pages:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.encoding = "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a[href]"):
                title = a.get_text(strip=True)
                if not title or len(title) < 8:
                    continue
                href = a.get("href", "")
                if "view.do" not in href and "idx=" not in href:
                    continue
                if not href.startswith("http"):
                    href = urljoin("https://www.bepa.kr", href)
                if any(r["title"] == title for r in results):
                    continue
                results.append({
                    "title": safe(title),
                    "org": "부산경제진흥원(BEPA)",
                    "source": tag,
                    "summary": "",
                    "deadline": None,
                    "days_left": None,
                    "url": href,
                })
        except Exception as e:
            print(f"[{tag}] {cat} 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 소스 7: BIPA (스크래핑)
# ══════════════════════════════════════════════════════
def fetch_bipa():
    tag = "BIPA"
    results = []
    url = "https://www.bipa.or.kr/board/list.do?boardId=BBS_0000010"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href]"):
            title = a.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            href = a.get("href", "")
            if "board" not in href and "view" not in href:
                continue
            if not href.startswith("http"):
                href = urljoin("https://www.bipa.or.kr", href)
            if any(r["title"] == title for r in results):
                continue
            results.append({
                "title": safe(title),
                "org": "부산정보산업진흥원(BIPA)",
                "source": tag,
                "summary": "",
                "deadline": None,
                "days_left": None,
                "url": href,
            })
    except Exception as e:
        print(f"[{tag}] 오류: {e}")
    print(f"[{tag}] {len(results)}건")
    return results

# ══════════════════════════════════════════════════════
# 후처리
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
        if d is None:
            return 99999
        return d
    return sorted(programs, key=key)

# ══════════════════════════════════════════════════════
# HTML 생성
# ══════════════════════════════════════════════════════
def make_html(programs):
    total = len(programs)
    urgent = sum(1 for p in programs if p["days_left"] is not None and 0 <= p["days_left"] <= 7)

    cards = ""
    for p in programs:
        dl = p["days_left"]
        if dl is None:
            dday = "상시"
            cls_dday = "g"
            cls_card = "always"
        elif dl <= 0:
            dday = "오늘 마감"
            cls_dday = "r"
            cls_card = "urgent"
        elif dl <= 7:
            dday = f"D-{dl}"
            cls_dday = "r"
            cls_card = "urgent"
        else:
            dday = f"D-{dl}"
            cls_dday = "y"
            cls_card = "normal"

        summary_html = f'<div class="desc">{p["summary"]}</div>' if p["summary"] else ""

        cards += f"""<div class="card {cls_card}" data-source="{p['source']}" data-title="{p['title'].lower()}" data-org="{p['org'].lower()}">
<div class="top"><span class="field">{p['source']}</span><span class="dday {cls_dday}">{dday}</span></div>
<h3>{p['title']}</h3>
<div class="org">{p['org']}</div>
{summary_html}
<a class="link" href="{p['url']}" target="_blank" rel="noopener">공고 원문 →</a>
</div>
"""

    sources_used = sorted(set(p["source"] for p in programs))
    sources_str = " · ".join(sources_used) if sources_used else "(수집된 소스 없음)"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>받소 — 소상공인 지원사업 모음</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css');
*{{box-sizing:border-box;margin:0}}
body{{font-family:'Pretendard',sans-serif;background:#FBFAF6;color:#1C1B17;padding:20px;max-width:720px;margin:0 auto}}
.hd h1{{font-size:22px;font-weight:800;color:#117A56}}
.hd p{{font-size:13px;color:#6E6A60;margin-top:4px}}
.stats{{display:flex;gap:12px;font-size:13px;color:#6E6A60;margin:12px 0}}
.stats b{{color:#1C1B17}} .stats .hot{{color:#C0392B}}
.bar{{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}}
.bar input,.bar select{{padding:10px 12px;border:1px solid #EAE6DB;border-radius:9px;font-family:inherit;font-size:13px;background:#fff}}
.bar input{{flex:1;min-width:200px}}
.bar input:focus,.bar select:focus{{outline:2px solid #117A56}}
.card{{background:#fff;border:1px solid #EAE6DB;border-radius:12px;padding:14px 16px;margin-bottom:10px;display:block}}
.card.urgent{{border-left:4px solid #C0392B}}
.card.normal{{border-left:4px solid #B57500}}
.card.always{{border-left:4px solid #117A56}}
.card.hide{{display:none}}
.top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
.field{{font-size:11px;font-weight:700;color:#6E6A60}}
.dday{{font-size:12px;font-weight:700;padding:2px 8px;border-radius:99px}}
.dday.r{{background:#FBEAE5;color:#C0392B}}
.dday.y{{background:#FBF1DC;color:#B57500}}
.dday.g{{background:#E6F2EC;color:#117A56}}
.card h3{{font-size:15px;font-weight:700;margin-bottom:3px;line-height:1.4}}
.org{{font-size:12px;color:#6E6A60;margin-bottom:6px}}
.desc{{font-size:13px;line-height:1.5;color:#3a382f;margin-bottom:6px}}
a.link{{display:inline-block;margin-top:6px;font-size:12px;font-weight:700;color:#117A56;text-decoration:none}}
a.link:hover{{text-decoration:underline}}
.empty{{text-align:center;padding:40px;color:#6E6A60;font-size:14px;display:none}}
.ft{{margin-top:24px;font-size:11px;color:#6E6A60;text-align:center;line-height:1.6}}
</style>
</head>
<body>
<div class="hd">
<h1>받소</h1>
<p>마지막 수집: {TIMESTAMP} (KST)</p>
</div>
<div class="stats">
<span>신청 가능 <b>{total}</b>건</span>
<span>마감 임박 <b class="hot">{urgent}</b>건 (7일 이내)</span>
</div>
<div class="bar">
<input type="text" id="q" placeholder="검색 (사업명, 기관, 키워드)" oninput="ft()">
<select id="src" onchange="ft()">
<option value="">전체 소스</option>
{"".join(f'<option value="{s}">{s}</option>' for s in sources_used)}
</select>
</div>
<div id="list">{cards}</div>
<div class="empty" id="empty">조건에 맞는 공고가 없어요</div>
<div class="ft">출처: {sources_str}<br>마감된 공고는 자동으로 제외됩니다</div>
<script>
function ft(){{
var q=document.getElementById("q").value.toLowerCase();
var s=document.getElementById("src").value;
var cards=document.querySelectorAll(".card");
var n=0;
cards.forEach(function(c){{
var ok=true;
if(q){{var t=c.getAttribute("data-title")+c.getAttribute("data-org");if(t.indexOf(q)<0)ok=false;}}
if(s&&c.getAttribute("data-source")!==s)ok=false;
c.classList.toggle("hide",!ok);
if(ok)n++;
}});
document.getElementById("empty").style.display=n?"none":"block";
}}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"=== 받소 수집 시작: {TIMESTAMP} ===\n")

    all_programs = []

    collectors = [
        ("기업마당 API", fetch_bizinfo),
        ("보조금24 API", fetch_gov),
        ("K-Startup API", fetch_kised),
        ("소상공인마당", fetch_sbiz),
        ("KPIPA", fetch_kpipa),
        ("BEPA", fetch_bepa),
        ("BIPA", fetch_bipa),
    ]

    for name, fn in collectors:
        print(f"\n--- {name} ---")
        try:
            items = fn()
            all_programs.extend(items)
        except Exception as e:
            print(f"[{name}] 치명적 오류: {e}")
            traceback.print_exc()

    # 후처리
    all_programs = dedupe(all_programs)
    all_programs = sort_programs(all_programs)

    print(f"\n=== 총 {len(all_programs)}건 (중복 제거 후) ===")

    # HTML 생성
    html = make_html(all_programs)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html 생성 완료")

    # JSON도 같이 저장 (디버깅/백업용)
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": TIMESTAMP,
            "total": len(all_programs),
            "programs": all_programs,
        }, f, ensure_ascii=False, indent=2)
    print("data.json 생성 완료")
