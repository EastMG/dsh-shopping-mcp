"""严格同款对比：只在「品牌 + 品名 + 规格」完全一致的商品之间比价。

和 cross_platform_compare 的区别：
* 那个是「同品类谁更便宜」，容易被包装规格、长尾链接干扰
* 这个是「**同一件商品**两个平台各卖多少」，不做跨规格推断

做法：把标题归一化成签名 `品牌 | 品名 | 规格`，只保留两边都出现的签名，
每个签名各取两边最低价，算差价。

用法：
    uv run python scripts\\same_item_compare.py --preset drinks
    uv run python scripts\\same_item_compare.py --preset milk
    uv run python scripts\\same_item_compare.py --preset drinks --keywords 无糖可乐

局限（务必注意）：
* 品牌识别靠预设关键词表，覆盖不到的小众品牌会漏
* 规格解析目前只认**容量型**（ml/L × 件数），纸巾这种「3层100抽×24包」
  需要另写解析器，不在本脚本范围
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

# --------------------------------------------------------------------- presets
PRESETS: dict[str, dict] = {
    "drinks": {
        "require_sugar_free": True,
        "brands": [
            "元气森林", "可口可乐", "百事可乐", "百事", "屈臣氏", "圣培露", "巴黎水",
            "健力宝", "王老吉", "东鹏", "外星人", "名仁", "冰峰", "北冰洋", "崂山",
            "雪碧", "芬达", "七喜", "美年达", "大窑",
        ],
        "products": [
            ("无糖可乐", ("无糖可乐", "零度可乐", "生可乐", "零卡可乐")),
            ("无糖雪碧", ("无糖雪碧", "零卡雪碧", "雪碧")),
            ("无糖芬达", ("无糖芬达", "芬达")),
            ("气泡水", ("气泡水",)),
            ("电解质水", ("电解质",)),
            ("苏打水", ("苏打水",)),
            ("维C饮料", ("维c",)),
            ("植物饮料", ("植物饮料", "红豆薏米", "绿豆")),
        ],
        "bad": ("原味", "含糖", "有糖", "茶", "果汁", "酒"),
        "keywords": [
            "无糖可乐", "无糖雪碧", "无糖芬达", "百事无糖", "元气森林气泡水",
            "无糖气泡水", "无糖碳酸饮料", "大窑无糖",
        ],
    },
    "milk": {
        "require_sugar_free": False,
        "brands": [
            "认养一头牛", "伊利", "蒙牛", "光明", "三元", "君乐宝", "新希望",
            "完达山", "德亚", "安佳", "纽仕兰", "圣牧", "现代牧业",
        ],
        "products": [
            ("脱脂纯牛奶", ("脱脂",)),
            ("高钙奶", ("高钙",)),
            ("低脂纯牛奶", ("低脂",)),
            ("有机纯牛奶", ("有机",)),
            ("纯牛奶", ("纯牛奶", "全脂")),
        ],
        "bad": ("酸奶", "乳饮料", "风味", "早餐奶", "巧克力", "咖啡", "临期", "雪糕"),
        "keywords": [
            "伊利纯牛奶整箱", "蒙牛纯牛奶整箱", "光明纯牛奶整箱",
            "脱脂牛奶整箱", "高钙奶整箱", "纯牛奶250ml整箱",
        ],
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="drinks", choices=sorted(PRESETS))
    ap.add_argument("--keywords", default="")
    ap.add_argument("--max-gap", type=float, default=2.0,
                    help="两边单价差异超过这个倍数就跳过（防止签名对错）")
    args = ap.parse_args()

    preset = PRESETS[args.preset]
    brands: list[str] = preset["brands"]
    products: list[tuple[str, tuple[str, ...]]] = preset["products"]
    bad: tuple[str, ...] = preset["bad"]
    require_sf: bool = preset["require_sugar_free"]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] or preset["keywords"]

    config.load_dotenv()

    def brand_of(title: str) -> str | None:
        for b in brands:  # 长名在前，避免「百事可乐」被「百事」截胡
            if b in title:
                return b
        return None

    def product_of(title: str) -> str | None:
        low = title.lower()
        for name, keys in products:
            if any(k.lower() in low for k in keys):
                return name
        return None

    def collect(adapter) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        dropped_price = 0
        for kw in keywords:
            try:
                items = adapter.search(kw, page_size=50)
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] {adapter.platform}/{kw}: {exc}", file=sys.stderr)
                continue
            for it in items:
                title = it.title or ""
                if not it.price:
                    continue
                if require_sf and not SUGAR_FREE.search(title):
                    continue
                if any(b in title for b in bad):
                    continue
                brand = brand_of(title)
                product_name = product_of(title)
                if not brand or not product_name:
                    continue
                total_ml, spec, ambiguous, count = parse_total_ml(title)
                if not total_ml or ambiguous or not count:
                    continue
                per_piece = it.price / count
                per_ml = total_ml / count
                floor = 1.5 if per_ml >= 300 else 0.9
                if per_piece < floor:
                    dropped_price += 1
                    continue
                sig = f"{brand} | {product_name} | {spec}"
                out.setdefault(sig, []).append(
                    {
                        "platform": adapter.platform,
                        "title": title,
                        "price": it.price,
                        "unit": round(it.price / (total_ml / 1000), 2),
                        "sales": it.sales,
                        "item_id": it.item_id,
                    }
                )
        if dropped_price:
            print(f"  [{adapter.platform}] 剔除 {dropped_price} 条单件价格不合理的", file=sys.stderr)
        return out

    print(f"严格同款对比 — preset={args.preset}")
    print(f"关键词：{', '.join(keywords)}\n")

    tb = collect(TaobaoAdapter())
    pdd = collect(PddAdapter())
    both = sorted(set(tb) & set(pdd))
    print(f"淘宝签名 {len(tb)}，拼多多签名 {len(pdd)}，两边都有 {len(both)}\n")

    if not both:
        print("没有两边规格完全一致的商品。放宽 --keywords 再试。")
        return 0

    rows = []
    for sig in both:
        t = min(tb[sig], key=lambda r: r["price"])
        p = min(pdd[sig], key=lambda r: r["price"])
        gap = max(t["price"], p["price"]) / min(t["price"], p["price"])
        if gap > args.max_gap:
            continue
        rows.append((sig, t, p))

    rows.sort(key=lambda x: -(abs(x[1]["price"] - x[2]["price"]) / max(x[1]["price"], x[2]["price"])))

    hdr = f"{'规格（品牌 | 品名 | 包装）':<40} {'淘宝':>9} {'拼多多':>9} {'差价':>8}  便宜方"
    print(hdr)
    print("-" * (len(hdr) + 6))
    t_win = p_win = 0
    for sig, t, p in rows:
        diff = t["price"] - p["price"]
        pct = abs(diff) / max(t["price"], p["price"]) * 100
        cheaper = "拼多多" if diff > 0 else "淘宝"
        if diff > 0:
            p_win += 1
        else:
            t_win += 1
        print(f"{sig:<40} ¥{t['price']:>7.2f} ¥{p['price']:>7.2f} {pct:>7.1f}%  {cheaper}")

    print(f"\n可比同款 {len(rows)} 组：拼多多便宜 {p_win} 组，淘宝便宜 {t_win} 组")
    return 0


if __name__ == "__main__":
    sys.exit(main())
