#!/usr/bin/env python3
"""合规抓取东方财富股吧帖子标题（寒武纪 688256）。

功能：
1. 在给定日期区间内抓取帖子标题。
2. 按 A 股交易日过滤（内置 2024/2025 节假日）。
3. 每个交易日随机抽样最多 100 条。
4. 输出 CSV / JSONL。

合规说明：
- 默认速率限制，避免高频请求。
- 请求头中标注用途，建议仅用于研究/学习。
- 请在使用前自行确认目标站点 robots 与服务条款。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 "
    "(research crawler; contact: local-script)"
)

# 2024-2025 中国大陆法定节假日（A股休市日覆盖）
CN_HOLIDAYS_2024_2025 = {
    # 2024
    "2024-01-01",
    "2024-02-09", "2024-02-10", "2024-02-11", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16", "2024-02-17",
    "2024-04-04", "2024-04-05", "2024-04-06",
    "2024-05-01", "2024-05-02", "2024-05-03", "2024-05-04", "2024-05-05",
    "2024-06-10",
    "2024-09-15", "2024-09-16", "2024-09-17",
    "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-05", "2024-10-06", "2024-10-07",
    # 2025
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04", "2025-04-05", "2025-04-06",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-05-31", "2025-06-01", "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04", "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
}


@dataclass
class Post:
    post_id: str
    title: str
    published_at: dt.datetime
    url: str

    @property
    def trade_date(self) -> dt.date:
        return self.published_at.date()


def parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def is_cn_trading_day(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return False
    return day.isoformat() not in CN_HOLIDAYS_2024_2025


def iter_dates(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += dt.timedelta(days=1)


def http_get(url: str, params: dict[str, str], timeout: int = 15) -> str:
    query = urllib.parse.urlencode(params)
    full = f"{url}?{query}"
    req = urllib.request.Request(
        full,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/javascript,*/*;q=0.9",
            "Referer": "https://guba.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def try_parse_jsonp(payload: str) -> dict:
    payload = payload.strip()
    if payload.startswith("{"):
        return json.loads(payload)

    m = re.match(r"^[^(]+\((.*)\);?$", payload, flags=re.S)
    if not m:
        raise ValueError("响应不是 JSON/JSONP")
    return json.loads(m.group(1))


def parse_post_time(raw: str) -> dt.datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def fetch_posts_api(code: str, max_pages: int, sleep_sec: float) -> list[Post]:
    """通过东方财富股吧常见接口拉取帖子。

    说明：该接口为站点内部接口，参数偶尔会更新；若失效可根据浏览器抓包调整。
    """
    endpoint = "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist"
    posts: list[Post] = []

    for page in range(1, max_pages + 1):
        params = {
            "code": code,
            "sorttype": "1",
            "ps": "100",
            "pn": str(page),
            "from": "CommonBaPost",
            "deviceid": "local-crawler",
            "version": "200",
            "product": "Guba",
        }
        try:
            payload = http_get(endpoint, params)
            data = try_parse_jsonp(payload)
        except Exception as exc:
            print(f"[WARN] page={page} 请求失败：{exc}")
            break

        article_block = data.get("re") or data.get("result") or data
        if not isinstance(article_block, dict):
            print(f"[WARN] page={page} 返回结构异常，停止")
            break

        rows = article_block.get("list") or article_block.get("article") or article_block.get("items") or []
        if not rows:
            print(f"[INFO] page={page} 无更多数据，停止")
            break

        for row in rows:
            title = (row.get("post_title") or row.get("title") or "").strip()
            raw_time = row.get("post_publish_time") or row.get("post_last_time") or row.get("display_time") or ""
            ts = parse_post_time(raw_time)
            if not title or not ts:
                continue
            post_id = str(row.get("post_id") or row.get("id") or "")
            url = row.get("post_url") or row.get("url") or f"https://guba.eastmoney.com/news,{code},{post_id}.html"
            posts.append(Post(post_id=post_id, title=title, published_at=ts, url=url))

        time.sleep(sleep_sec)

    return posts


def filter_and_sample(
    posts: list[Post],
    start_date: dt.date,
    end_date: dt.date,
    per_day: int,
    seed: int,
) -> list[Post]:
    trading_days = {d for d in iter_dates(start_date, end_date) if is_cn_trading_day(d)}
    grouped: dict[dt.date, list[Post]] = defaultdict(list)
    for p in posts:
        d = p.trade_date
        if d in trading_days:
            grouped[d].append(p)

    rng = random.Random(seed)
    picked: list[Post] = []
    for day in sorted(grouped):
        items = grouped[day]
        if len(items) <= per_day:
            picked.extend(items)
        else:
            picked.extend(rng.sample(items, per_day))
    return picked


def write_csv(path: Path, posts: list[Post]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["trade_date", "published_at", "post_id", "title", "url"])
        for p in sorted(posts, key=lambda x: x.published_at):
            w.writerow([
                p.trade_date.isoformat(),
                p.published_at.strftime("%Y-%m-%d %H:%M:%S"),
                p.post_id,
                p.title,
                p.url,
            ])


def write_jsonl(path: Path, posts: list[Post]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for p in sorted(posts, key=lambda x: x.published_at):
            row = {
                "trade_date": p.trade_date.isoformat(),
                "published_at": p.published_at.strftime("%Y-%m-%d %H:%M:%S"),
                "post_id": p.post_id,
                "title": p.title,
                "url": p.url,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="合规抓取东方财富股吧标题并按交易日随机抽样")
    parser.add_argument("--code", default="688256", help="股票代码，默认 688256")
    parser.add_argument("--start", default="2024-09-01", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default="2025-09-30", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--per-day", type=int, default=100, help="每个交易日抽样条数，默认 100")
    parser.add_argument("--seed", type=int, default=688256, help="随机种子，保证可复现")
    parser.add_argument("--max-pages", type=int, default=800, help="最多抓取页数")
    parser.add_argument("--sleep", type=float, default=0.25, help="每页请求间隔秒数")
    parser.add_argument("--out-csv", default="output/cambricon_688256_titles_sampled.csv", help="CSV 输出路径")
    parser.add_argument("--out-jsonl", default="output/cambricon_688256_titles_sampled.jsonl", help="JSONL 输出路径")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    start_date = parse_date(args.start)
    end_date = parse_date(args.end)

    if start_date > end_date:
        raise SystemExit("--start 不能晚于 --end")

    print("[INFO] 开始抓取，请确保你有权访问目标站点并遵守 robots/服务条款")
    posts = fetch_posts_api(args.code, max_pages=args.max_pages, sleep_sec=args.sleep)
    print(f"[INFO] 原始帖子数：{len(posts)}")

    sampled = filter_and_sample(
        posts,
        start_date=start_date,
        end_date=end_date,
        per_day=args.per_day,
        seed=args.seed,
    )
    print(f"[INFO] 抽样后帖子数：{len(sampled)}")

    out_csv = Path(args.out_csv)
    out_jsonl = Path(args.out_jsonl)
    write_csv(out_csv, sampled)
    write_jsonl(out_jsonl, sampled)
    print(f"[INFO] 已输出 CSV: {out_csv}")
    print(f"[INFO] 已输出 JSONL: {out_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
