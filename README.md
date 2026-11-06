# New Books Tracker

按 **类别 / 出版社 / 作者** 从公开免费 API 追踪新书，每天自动更新一次，托管在 GitHub Pages 上。零服务器、零数据库、零费用。

```
watch.yaml  ──►  scripts/fetch_books.py  ──►  data/books.json + feed.xml  ──►  index.html
 (追踪范围)       (GitHub Actions 每天跑)         (静态数据)                   (GitHub Pages)
```

## 快速开始

1. 把这个仓库推到 GitHub。
2. **Settings → Pages → Source** 选 `Deploy from a branch`，分支 `main`，目录 `/ (root)`。
3. **Actions** 标签页里找到 `Update new books`，点 `Run workflow` 手动跑第一次。
4. 几分钟后打开 `https://<你的用户名>.github.io/<仓库名>/`。

之后每天 05:17 UTC 自动更新；也可以随时手动 `Run workflow`。

## 改追踪范围

只改 `watch.yaml`：

```yaml
categories: [machine learning, optimization]
publishers: [Springer, MIT Press]
authors: [Kevin P. Murphy]
```

不用动代码。三个维度各自独立查询，合并后去重；前端的筛选器是从数据里动态生成的，加了新规则下次更新后就会出现。

## 本地运行

```bash
pip install -r requirements.txt
python scripts/fetch_books.py --dry-run   # 只打印今天会新增哪些，不写文件
python scripts/fetch_books.py             # 写入 data/books.json 和 feed.xml
python -m http.server 8000                # 打开 http://localhost:8000
```

可选：设置 `GOOGLE_BOOKS_API_KEY`（在 GitHub 仓库 Settings → Secrets 里加同名 secret）可以提高 Google Books 配额，不设也能跑。

## 数据源

| 源 | 用途 | 限制 |
|---|---|---|
| [Google Books API](https://developers.google.com/books/docs/v1/using) | 主力：类别 / 出版社 / 作者三种查询都支持，有封面和简介 | 无 key 时配额较低；中文书覆盖一般 |
| [Open Library](https://openlibrary.org/dev/docs/api/search) | 补充：同样支持三种查询，作者数据干净 | 响应慢，出版日期有时不准 |

## 数据格式

`data/books.json`：

```json
{
  "updated": "2026-11-06T05:20:00+00:00",
  "count": 123,
  "books": [{
    "id": "isbn:9780262048644",
    "title": "...", "subtitle": "...", "authors": ["..."], "publisher": "...",
    "categories": ["..."], "published": "2026-10-28", "isbn13": "...",
    "cover": "https://...", "description": "...", "language": "en", "url": "https://...",
    "sources": ["google_books", "open_library"],
    "matched": { "categories": ["machine learning"], "publishers": [], "authors": [] },
    "first_seen": "2026-11-06"
  }]
}
```

- `id`：有 ISBN-13 用 ISBN，否则用书名+作者的 hash，用于跨源去重。
- `matched`：这本书是因为 `watch.yaml` 里哪几条规则被抓到的，前端筛选器就靠它。
- `first_seen`：系统第一次见到它的日期。"今日新增"看这个，比 API 的出版日期可靠。

## 待办 / 可扩展方向

见 `CLAUDE.md` 的 Roadmap。
