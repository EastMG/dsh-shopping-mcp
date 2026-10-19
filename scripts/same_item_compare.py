"""严格同款对比：只在「品牌 + 品名 + 规格」完全一致的商品之间比价。

和 cross_platform_compare 的区别：
* 那个是「同品类谁更便宜」，容易被包装规格、长尾链接干扰
* 这个是「**同一件商品**两个平台各卖多少」，不做跨规格推断

做法：把标题归一化成签名 `品牌 | 品名 | 规格`，只保留两边都出现的签名，
每个签名各取两边最低价，算差价。

用法：
    uv run python scripts\\same_item_compare.py
    uv run python scripts\\same_item_compare.py --keywords 无糖可乐,无糖雪碧

局限（务必注意）：
* 品牌识别靠关键词表，覆盖不到的小众品牌会漏
* 拼多多只给最低 SKU 价，若该链接有更小规格，签名可能对错
* 淘宝销量分档截断，销量列只能当数量级参考
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdd_unit_price import SUGAR_FREE, parse_total_ml  # noqa: E402

from shopping_mcp import config  # noqa: E402
from shopping_mcp.adapters.pdd import PddAdapter  # noqa: E402
from shopping_mcp.adapters.taobao import TaobaoAdapter  # noqa: E402

# 品牌表：长的放前面，避免「可口可乐」被「可乐」截胡
BRANDS = [
    "元气森林", "可口可乐", "百事可乐", "百事", "屈臣氏", "圣培露", "巴黎水",
    "健力宝", "王老吉", "东鹏", "外星人", "名仁", "冰峰", "北冰洋", "崂山",
    "雪碧", "芬达", "七喜", "美年达", "大窑",
]

# 品名归一化：命中即用这个标准名
PRODUCTS = [
    ("无糖可乐", ("无糖可乐", "零度可乐", "生可乐", "零卡可乐")),
    ("无糖雪碧", ("无糖雪碧", "零卡雪碧", "雪碧")),
    ("无糖芬达", ("无糖芬达", "芬达")),
    ("气泡水", ("气泡水",)),
    ("电解质水", ("电解质",)),
    ("苏打水", ("苏打水",)),
    ("维C饮料", ("维c", "维C")),
    ("植物饮料", ("植物饮料", "红豆薏米", "绿豆")),
]

# 这些词出现就说明是含糖/混合/非本次对比对象
BAD = ("原味", "含糖", "有糖", "茶", "果汁", "酒")

DEFAULT_KEYWORDS = [
    "无糖可乐", "无糖雪碧", "无糖芬达", "百事无糖", "元气森林气泡水",
    "无糖气泡水", "无糖碳酸饮料", "大窑无糖",
]


def brand_of(title: str) -> str | None:
    for b in BRANDS:
        if b in title:
            return b
    return None


def product_of(title: str) -> str | None:
    low = title.lower()
    for name, keys in PRODUCTS:
        if any(k.lower() in low for k in keys):
            return name
    return None


def collect(adapter, keywords: list[str]) -> dict[str, list[dict]]:
    """签名 -> 候选列表。"""
    out: dict[str, list[dict]] = {}
    for kw in keywords:
        try:
            items = adapter.search(kw, page_size=50)
        except Exception as exc:  # noqa: BLE001
            print(f"  [跳过] {adapter.platform}/{kw}: {exc}", file=sys.stderr)
            continue
        for it in items:
            title = it.title or ""
            if not SUGAR_FREE.search(title) or not it.price:
                continue
            if any(b in title for b in BAD):
                continue
            brand = brand_of(title)
            product = product_of(title)
            if not brand or not product:
                continue
            total_ml, spec, ambiguous, count = parse_total_ml(title)
            if not total_ml or ambiguous:
                continue
            if count not in (1, 2, 3, 5, 6, 12, 15, 18, 24):
                continue
            sig = f"{brand} | {product} | {spec}"
            per_piece = it.price / count if count else None
            per_ml = total_ml / count if count else None
            # 单件价格合理性：饮料单罐/单瓶不可能低到这个程度。
            # 低于阈值的基本都是「标 24 瓶、实际 12 瓶」的最低 SKU 价，
            # 它们的元/L 会虚低一半以上，必须剔掉，否则会得出「淘宝更便宜」的假结论。
            if per_piece is not None and per_ml is not None:
                floor = 1.5 if per_ml >= 300 else 0.9
                if per_piece < floor:
                    continue
            out.setdefault(sig, []).append(
                {
                    "platform": adapter.platform,
                    "title": title,
                    "price": it.price,
                    "unit": round(it.price / (total_ml / 1000), 2),
                    "sales": it.sales,
                    "rate": it.commission_rate,
                    "shop": it.shop_name,
                    "item_id": it.item_id,
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", default="")
    ap.add_argument("--max-gap", type=float, default=1.0,
                    help="两边单价差异超过这个倍数就跳过（防止签名对错）")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] or DEFAULT_KEYWORDS
    config.load_dotenv()

    print(f"严格同款对比 — 关键词：{', '.join(keywords)}\n")

    tb = collect(TaobaoAdapter(), keywords)
    pdd = collect(PddAdapter(), keywords)

    both = sorted(set(tb) & set(pdd))
    print(f"淘宝签名数 {len(tb)}，拼多多签名数 {len(pdd)}，两边都有 {len(both)} 个\n")

    if not both:
        print("没有找到两边规格完全一致的商品。可以把 --keywords 放宽一点再试。")
        return 0

    rows = []
    for sig in both:
        t = min(tb[sig], key=lambda r: r["price"])
        p = min(pdd[sig], key=lambda r: r["price"])
        if t["unit"] <= 0 or p["unit"] <= 0:
            continue
        gap = max(t["unit"], p["unit"]) / min(t["unit"], p["unit"])
        if gap > args.max_gap:
            continue
        rows.append((sig, t, p, gap))

    rows.sort(key=lambda x: -abs(x[1]["unit"] - x[2]["unit"]))

    hdr = f"{'规格':<34} {'淘宝':>10} {'拼多多':>10} {'差价':>9}  {'便宜方'}"
    print(hdr)
    print("-" * (len(hdr) + 14))
    t_win = p_win = 0
    for sig, t, p, gap in rows:
        diff = t["unit"] - p["unit"]
        cheaper = "拼多多" if diff > 0 else "淘宝"
        pct = abs(diff) / max(t["unit"], p["unit"]) * 100
        if diff > 0:
            p_win += 1
        else:
            t_win += 1
        print(
            f"{sig:<34} ¥{t['price']:>7.2f} ¥{p['price']:>7.2f} "
            f"{pct:>7.1f}%  {cheaper}"
        )
        print(f"{'':<34} (¥{t['unit']:.2f}/L) (¥{p['unit']:.2f}/L)")

    print(f"\n可比同款 {len(rows)} 组：拼多多便宜 {p_win} 组，淘宝便宜 {t_win} 组")
    return 0


if __name__ == "__main__":
    sys.exit(main())
