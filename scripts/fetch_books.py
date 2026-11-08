#!/usr/bin/env python3
"""
每天跑一次：按 watch.yaml 里的类别 / 出版社 / 作者，从公开免费 API 拉新书，
归一化、去重、与已有 data/books.json 合并，并生成 feed.xml (RSS)。

用法:
    python scripts/fetch_books.py            # 正常运行
    python scripts/fetch_books.py --dry-run  # 只打印，不写文件

环境变量（可选）:
    GOOGLE_BOOKS_API_KEY   提高 Google Books 配额；没有也能跑
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
WATCH_FILE = ROOT / "watch.yaml"
DATA_FILE = ROOT / "data" / "books.json"
FEED_FILE = ROOT / "feed.xml"

UA = "newbooks-tracker/0.1 (+https://github.com/)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = UA

TODAY = date.today()


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def get_json(url: str, params: dict, retries: int = 3, pause: float = 1.0) -> dict | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            log(f"  ! {url} 失败 ({attempt + 1}/{retries}): {e}")
            time.sleep(pause * (attempt + 1))
    return None


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_date(s: str | None) -> date | None:
    """接受 'YYYY', 'YYYY-MM', 'YYYY-MM-DD'，或 'March 3, 2026' 之类；失败返回 None。"""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(19|20)\d{2}", s)
    return date(int(m.group(0)), 1, 1) if m else None


def book_id(isbn13: str | None, title: str, authors: list[str]) -> str:
    if isbn13:
        return f"isbn:{isbn13}"
    key = norm_text(title).lower() + "|" + "|".join(sorted(a.lower() for a in authors))
    return "h:" + hashlib.sha1(key.encode()).hexdigest()[:16]


def to_isbn13(isbns: list[str]) -> str | None:
    for i in isbns:
        i = re.sub(r"[^0-9Xx]", "", i)
        if len(i) == 13:
            return i
    for i in isbns:
        i = re.sub(r"[^0-9Xx]", "", i)
        if len(i) == 10:
            core = "978" + i[:9]
            total = sum(int(c) * (1 if k % 2 == 0 else 3) for k, c in enumerate(core))
            return core + str((10 - total % 10) % 10)
    return None


# --------------------------------------------------------------------------- #
# 数据源 1: Google Books
# --------------------------------------------------------------------------- #
GB_URL = "https://www.googleapis.com/books/v1/volumes"


def google_books(query: str, cfg: dict, api_key: str | None) -> list[dict]:
    out: list[dict] = []
    langs = cfg.get("languages") or [None]
    for lang in langs:
        params = {
            "q": query,
            "orderBy": "newest",
            "printType": "books",
            "maxResults": min(int(cfg.get("max_results_per_query", 40)), 40),
        }
        if lang:
            params["langRestrict"] = lang
        if api_key:
            params["key"] = api_key
        data = get_json(GB_URL, params)
        if not data:
            continue
        for item in data.get("items", []):
            v = item.get("volumeInfo", {})
            isbns = [x.get("identifier", "") for x in v.get("industryIdentifiers", [])]
            out.append(
                {
                    "title": norm_text(v.get("title", "")),
                    "subtitle": norm_text(v.get("subtitle", "")),
                    "authors": [norm_text(a) for a in v.get("authors", [])],
                    "publisher": norm_text(v.get("publisher", "")),
                    "categories": [norm_text(c) for c in v.get("categories", [])],
                    "published": v.get("publishedDate"),
                    "isbn13": to_isbn13(isbns),
                    "cover": (v.get("imageLinks") or {}).get("thumbnail", "").replace("http://", "https://"),
                    "description": norm_text(v.get("description", ""))[:600],
                    "language": v.get("language"),
                    "url": v.get("infoLink") or v.get("canonicalVolumeLink"),
                    "source": "google_books",
                }
            )
        time.sleep(0.3)
    return out


# --------------------------------------------------------------------------- #
# 数据源 2: Open Library
# --------------------------------------------------------------------------- #
OL_URL = "https://openlibrary.org/search.json"
OL_FIELDS = "key,title,subtitle,author_name,publisher,isbn,first_publish_year,publish_date,cover_i,subject,language"


def open_library(params: dict, cfg: dict) -> list[dict]:
    params = {
        **params,
        "sort": "new",
        "limit": int(cfg.get("max_results_per_query", 40)),
        "fields": OL_FIELDS,
    }
    data = get_json(OL_URL, params)
    time.sleep(1.0)  # Open Library 没有硬配额，但请求慢，礼貌一点
    if not data:
        return []
    out: list[dict] = []
    for d in data.get("docs", []):
        pub_dates = d.get("publish_date") or []
        published = None
        for pd in pub_dates:  # 取能解析的最晚日期
            p = parse_date(pd)
            if p and (published is None or p > published):
                published = p
        if published is None and d.get("first_publish_year"):
            published = date(int(d["first_publish_year"]), 1, 1)
        out.append(
            {
                "title": norm_text(d.get("title", "")),
                "subtitle": norm_text(d.get("subtitle", "")),
                "authors": [norm_text(a) for a in d.get("author_name", [])],
                "publisher": norm_text((d.get("publisher") or [""])[0]),
                "categories": [norm_text(s) for s in (d.get("subject") or [])[:8]],
                "published": published.isoformat() if published else None,
                "isbn13": to_isbn13(d.get("isbn") or []),
                "cover": f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg" if d.get("cover_i") else "",
                "description": "",
                "language": (d.get("language") or [None])[0],
                "url": f"https://openlibrary.org{d['key']}" if d.get("key") else None,
                "source": "open_library",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# 数据源 3: 豆瓣「新书速递」（无官方 API，解析公开 HTML；只取前几页，请求很少）
# --------------------------------------------------------------------------- #
DB_URL = "https://book.douban.com/latest"
# 豆瓣分类 → watch.yaml 里的 categories 名；只有 watch.yaml 里写了这个类别才会命中
DB_SUBCAT_TO_CATEGORY = {"文学": "文学", "小说": "文学", "艺术设计": "艺术"}
_DB_ITEM = re.compile(r'<li class="media clearfix">(.*?)</li>', re.S)
_DB_TITLE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DB_ABSTRACT = re.compile(r'<p class="subject-abstract[^"]*">(.*?)</p>', re.S)
_DB_COVER = re.compile(r'<img class="subject-cover"[^>]*src="([^"]+)"')
_DB_DATE = re.compile(r"^\d{4}(-\d{1,2}){0,2}$")


def _db_parse(html_text: str, subcat: str) -> list[dict]:
    out: list[dict] = []
    for m in _DB_ITEM.finditer(html_text):
        block = m.group(1)
        t, ab = _DB_TITLE.search(block), _DB_ABSTRACT.search(block)
        if not t or not ab:
            continue
        parts = [norm_text(html.unescape(x)) for x in ab.group(1).split("/")]
        di = next((i for i, x in enumerate(parts) if _DB_DATE.match(x)), None)
        if di is None:
            continue  # 没有出版日期的条目无法判断新旧，丢弃
        authors = [re.sub(r"^[\[\(（【].*?[\]\)）】]\s*", "", a) for a in parts[:di] if a]
        cover = _DB_COVER.search(block)
        out.append(
            {
                "title": norm_text(html.unescape(re.sub(r"<[^>]+>", "", t.group(2)))),
                "subtitle": "",
                "authors": authors[:1],  # 后面的通常是译者，只留第一作者以便去重和筛选
                "publisher": parts[di + 1] if di + 1 < len(parts) else "",
                "categories": [] if subcat == "全部" else [subcat],
                "published": parts[di],
                "isbn13": None,  # 列表页没有 ISBN → id 走 title+作者 的 hash
                "cover": cover.group(1) if cover else "",
                "description": "",
                "language": "zh",
                "url": t.group(1),
                "source": "douban",
            }
        )
    return out


def douban(cfg: dict) -> list[dict]:
    dcfg = cfg.get("douban") or {}
    subcats = ["全部"] + list(dcfg.get("subcats", ["文学", "小说", "艺术设计"]))
    out: list[dict] = []
    for sc in subcats:
        try:
            r = SESSION.get(DB_URL, params={"subcat": sc}, timeout=30)
            if r.status_code != 200:
                log(f"  ! 豆瓣 {sc}: HTTP {r.status_code}（可能被反爬拦截，跳过）")
                continue
            items = _db_parse(r.text, sc)
            log(f"  豆瓣 {sc}: {len(items)} 本")
            out.extend(items)
        except Exception as e:  # noqa: BLE001
            log(f"  ! 豆瓣 {sc} 失败: {e}")
        time.sleep(3.0)  # 礼貌：每天总共只有几个请求
    return out


def _same_publisher(a: str, b: str) -> bool:
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    return bool(a and b and (a in b or b in a))


def collect_douban(cfg: dict) -> list[tuple[dict, dict]]:
    """豆瓣是「全站新书」而不是按查询返回，所以要在本地按 watch.yaml 打 matched 标签。"""
    keep_all = bool((cfg.get("douban") or {}).get("keep_all", False))
    watch_pubs = cfg.get("publishers", [])
    watch_cats = set(cfg.get("categories", []))
    res: list[tuple[dict, dict]] = []
    for b in douban(cfg):
        matched: dict = {}
        pubs = [p for p in watch_pubs if _same_publisher(p, b["publisher"])]
        cats = sorted({DB_SUBCAT_TO_CATEGORY[c] for c in b["categories"] if DB_SUBCAT_TO_CATEGORY.get(c) in watch_cats})
        if pubs:
            matched["publishers"] = pubs
        if cats:
            matched["categories"] = cats
        if matched or keep_all:
            res.append((b, matched))
    return res


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def collect(cfg: dict, api_key: str | None) -> list[tuple[dict, dict]]:
    """返回 [(raw_book, matched)]，matched 记录它是因为哪条 watch 规则被抓到的。"""
    results: list[tuple[dict, dict]] = []
    src = cfg.get("sources", {})

    def add(books: list[dict], kind: str, value: str) -> None:
        for b in books:
            results.append((b, {kind: value}))

    for c in cfg.get("categories", []):
        log(f"[类别] {c}")
        if src.get("google_books", True):
            add(google_books(f'subject:"{c}"', cfg, api_key), "categories", c)
        if src.get("open_library", True):
            add(open_library({"subject": c}, cfg), "categories", c)

    for p in cfg.get("publishers", []):
        log(f"[出版社] {p}")
        if src.get("google_books", True):
            add(google_books(f'inpublisher:"{p}"', cfg, api_key), "publishers", p)
        if src.get("open_library", True):
            add(open_library({"publisher": p}, cfg), "publishers", p)

    for a in cfg.get("authors", []):
        log(f"[作者] {a}")
        if src.get("google_books", True):
            add(google_books(f'inauthor:"{a}"', cfg, api_key), "authors", a)
        if src.get("open_library", True):
            add(open_library({"author": a}, cfg), "authors", a)

    if src.get("douban", False):
        log("[豆瓣] 新书速递")
        results.extend(collect_douban(cfg))

    return results


def merge(existing: dict[str, dict], found: list[tuple[dict, dict]], cfg: dict) -> dict[str, dict]:
    within = timedelta(days=int(cfg.get("published_within_days", 120)))
    ahead = timedelta(days=int(cfg.get("max_future_days", 180)))
    retain = timedelta(days=int(cfg.get("retain_days", 365)))
    today_s = TODAY.isoformat()

    books: dict[str, dict] = {}
    # 保留旧记录（未过期的）
    for bid, b in existing.items():
        fs = parse_date(b.get("first_seen"))
        if fs and TODAY - fs <= retain:
            books[bid] = b

    for raw, matched in found:
        if not raw["title"]:
            continue
        pub = parse_date(raw.get("published"))
        if pub is None or TODAY - pub > within or pub - TODAY > ahead:
            continue  # 太旧、没日期、或日期离谱地在未来（API 脏数据）→ 丢
        bid = book_id(raw.get("isbn13"), raw["title"], raw["authors"])
        b = books.get(bid)
        if b is None:
            b = {
                "id": bid,
                "title": raw["title"],
                "subtitle": raw.get("subtitle", ""),
                "authors": raw["authors"],
                "publisher": raw.get("publisher", ""),
                "categories": raw.get("categories", []),
                "published": pub.isoformat(),
                "isbn13": raw.get("isbn13"),
                "cover": raw.get("cover", ""),
                "description": raw.get("description", ""),
                "language": raw.get("language"),
                "url": raw.get("url"),
                "sources": [raw["source"]],
                "matched": {"categories": [], "publishers": [], "authors": []},
                "first_seen": today_s,
            }
            books[bid] = b
        else:
            # 补全缺失字段
            for k in ("subtitle", "publisher", "cover", "description", "url", "isbn13", "language"):
                if not b.get(k) and raw.get(k):
                    b[k] = raw[k]
            for c in raw.get("categories", []):
                if c and c not in b["categories"]:
                    b["categories"].append(c)
            if raw["source"] not in b["sources"]:
                b["sources"].append(raw["source"])
        for k, vs in matched.items():
            for v in (vs if isinstance(vs, list) else [vs]):
                if v not in b["matched"][k]:
                    b["matched"][k].append(v)
    return books


def write_feed(books: list[dict], limit: int = 50) -> None:
    items = []
    for b in books[:limit]:
        authors = ", ".join(b["authors"]) or "未知作者"
        desc = f"{authors} · {b.get('publisher') or ''} · {b['published']}"
        if b.get("description"):
            desc += "<br/><br/>" + escape(b["description"])
        link = b.get("url") or ""
        items.append(
            f"""  <item>
    <title>{escape(b['title'])}</title>
    <link>{escape(link)}</link>
    <guid isPermaLink="false">{escape(b['id'])}</guid>
    <pubDate>{datetime.fromisoformat(b['first_seen']).replace(tzinfo=timezone.utc).strftime('%a, %d %b %Y 00:00:00 +0000')}</pubDate>
    <description><![CDATA[{desc}]]></description>
  </item>"""
        )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>New Books Tracker</title>
  <link>./</link>
  <description>按类别 / 出版社 / 作者追踪的新书</description>
  <lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>
"""
    FEED_FILE.write_text(xml, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import os

    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY") or None
    cfg = yaml.safe_load(WATCH_FILE.read_text(encoding="utf-8")) or {}

    existing: dict[str, dict] = {}
    if DATA_FILE.exists():
        old = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        existing = {b["id"]: b for b in old.get("books", [])}

    found = collect(cfg, api_key)
    log(f"共抓到 {len(found)} 条原始记录")

    books = merge(existing, found, cfg)
    ordered = sorted(books.values(), key=lambda b: (b["first_seen"], b["published"]), reverse=True)
    new_today = [b for b in ordered if b["first_seen"] == TODAY.isoformat()]
    log(f"合并后 {len(ordered)} 本，其中今天新增 {len(new_today)} 本")

    if args.dry_run:
        for b in new_today[:20]:
            print(f"- {b['published']}  {b['title']}  / {', '.join(b['authors'])}  [{b['publisher']}]")
        return 0

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(
            {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(ordered), "books": ordered},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    write_feed(ordered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
