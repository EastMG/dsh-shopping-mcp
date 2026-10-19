"""淘宝 vs 拼多多 同关键词比价。

同一关键词在两个平台各拉一批，统一解析规格算「券后单价（元/升）」，
再按品类横向对比，回答「同样的东西哪边买更划算」。

用法：
    uv run python scripts\\cross_platform_compare.py
    uv run python scripts\\cross_platform_compare.py --keyword 无糖饮料 --pages 2
    uv run python scripts\\cross_platform_compare.py --keyword 纸巾 --exclude 湿巾

注意：
* 两边的 price 都是**券后价**（拼多多 = 拼团价-券；淘宝 = final_promotion_price）。
* 拼多多只返回「最低 SKU 价」，多规格链接的单价是**下限**，看「元/件」列自检。
* 淘宝「原价」多为商家虚标，只看券后价。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdd_unit_price import (  # noqa: E402
    EXCLUDE_HINTS,
    RISK_HINTS,
    SUGAR_FREE,
    parse_total_ml,
    sugary_component,
)

from shopping_mcp import config  # noqa: E402
from shopping_mcp.adapters.pdd import PddAdapter  # noqa: E402
from shopping_mcp.adapters.taobao import TaobaoAdapter  # noqa: E402

EXCLUDE_DEFAULT = "苏打水,临期"

# 标题里同时出现这些词 + 无糖 → 多半是含糖/混合 SKU 链接
SUSPECT = re.compile(r"原味|含糖")


def collect(adapter, keyword: str, pages: int, excludes: list[str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    dropped_suspect = 0
    for page in range(1, pages + 1):
        try:
            items = adapter.search(keyword, page=page, page_size=50)
        except Exception as exc:  # noqa: BLE001
            print(f"  [跳过] {adapter.platform} p{page}: {exc}", file=sys.stderr)
            continue
        for it in items:
            title = it.title or ""
            if it.item_id in seen:
                continue
            # 必须真的无糖：搜索关键词会带出「原味/经典」这类含糖款
            if not SUGAR_FREE.search(title):
                continue
            # 标题同时出现「原味 / 含糖」→ 典型的多 SKU 混合链接：
            # 商家用「无糖」堆关键词，但最低价那个 SKU 往往是含糖原味
            if SUSPECT.search(title):
                dropped_suspect += 1
                continue
            # 混装里只要有一款含糖（如「雪碧芬达无糖可乐」）就排除
            if sugary_component(title):
                continue
            if any(h in title for h in EXCLUDE_HINTS):
                continue
            if any(e in title for e in excludes):
                continue
            total_ml, spec, ambiguous, count = parse_total_ml(title)
            if not total_ml or not it.price:
                continue
            seen.add(it.item_id)
            rows.append(
                {
                    "platform": adapter.platform,
                    "item_id": it.item_id,
                    "title": title,
                    "price": it.price,
                    "original": it.original_price,
                    "coupon": it.coupon_amount,
                    "spec": spec,
                    "count": count,
                    "per_piece": round(it.price / count, 2) if count else None,
                    "unit": round(it.price / (total_ml / 1000), 2),
                    "rate": it.commission_rate,
                    "sales": it.sales,
                    "shop": it.shop_name,
                    "ambiguous": ambiguous,
                }
            )
    if dropped_suspect:
        print(
            f"  [{adapter.platform}] 已剔除 {dropped_suspect} 条「原味/含糖」混合链接",
            file=sys.stderr,
        )
    return rows


def show(platform: str, rows: list[dict], top: int) -> None:
    rows = sorted(rows, key=lambda r: r["unit"])[:top]
    print(f"\n{'=' * 78}")
    print(f"  {platform}  —  券后单价最低 {len(rows)} 条（元/升）")
    print(f"{'=' * 78}")
    print(f"{'#':>3}  {'元/L':>8}  {'元/件':>7}  {'券后价':>8}  {'规格':<14} {'销量':>7}  {'佣金':>5}  商品")
    for i, r in enumerate(rows, 1):
        sales = r["sales"]
        s = f"{sales/10000:.0f}万" if isinstance(sales, int) and sales >= 10000 else str(sales)
        rate = f"{r['rate']:.1f}%" if r["rate"] is not None else "-"
        pp = f"¥{r['per_piece']:.2f}" if r["per_piece"] else "-"
        mark = " ⚠多规格" if r["ambiguous"] else ""
        print(
            f"{i:>3}  ¥{r['unit']:>6.2f}  {pp:>7}  ¥{r['price']:>6.2f}  {r['spec']:<14} "
            f"{s:>7}  {rate:>5}  {r['title'][:26]}{mark}"
        )


def summary(taobao: list[dict], pdd: list[dict]) -> None:
    print(f"\n{'=' * 78}")
    print("  平台对比")
    print(f"{'=' * 78}")
    print(f"{'指标':<16} {'淘宝':>16} {'拼多多':>16}")
    for label, key in (("命中商品数", None), ("最低元/L", "unit"), ("中位元/L", "unit"), ("平均佣金率", "rate")):
        cells = []
        for rows in (taobao, pdd):
            if not rows:
                cells.append("-")
                continue
            vals = sorted(r[key] for r in rows if r.get(key) is not None)
            if key is None:
                cells.append(f"{len(rows)} 条")
            elif key == "rate":
                cells.append(f"{sum(vals)/len(vals):.2f}%" if vals else "-")
            elif label == "最低元/L":
                cells.append(f"¥{vals[0]:.2f}")
            else:
                cells.append(f"¥{vals[len(vals)//2]:.2f}")
        print(f"{label:<16} {cells[0]:>16} {cells[1]:>16}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="无糖饮料")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--exclude", default=EXCLUDE_DEFAULT)
    args = ap.parse_args()

    excludes = [e.strip() for e in args.exclude.split(",") if e.strip()]
    config.load_dotenv()

    print(f"关键词：{args.keyword}   每平台 {args.pages} 页 × 50")
    taobao = collect(TaobaoAdapter(), args.keyword, args.pages, excludes)
    pdd = collect(PddAdapter(), args.keyword, args.pages, excludes)

    show("淘宝", taobao, args.top)
    show("拼多多", pdd, args.top)
    summary(taobao, pdd)

    print("\n提示：两边单价都是「券后价」，但拼多多多规格链接只给最低 SKU 价，")
    print("「元/件」明显偏低的那条要在商品页核实规格。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
