"""淘宝 vs 拼多多 同品类比价。

同一组关键词在两个平台各拉一批，统一解析规格算「券后单价（元/升）」，
再横向对比，回答「同样的东西哪边买更划算」。

用法：
    uv run python scripts\\cross_platform_compare.py
    uv run python scripts\\cross_platform_compare.py --keywords 无糖汽水,0糖气泡水,无糖可乐
    uv run python scripts\\cross_platform_compare.py --min-sales 1000

为什么默认用**多关键词**：
单关键词会漏掉大量商品。淘宝/拼多多的标题里不一定写「无糖汽水」四个字，
比如销量 1 万的元气森林写的是「气泡水280ml*12瓶」。只搜一个词就会得出
「淘宝无糖汽水销量都个位数」这种错误结论——实测过一次，教训。

为什么有 `--min-sales`：
按元/升排序天然偏爱长尾低价链接（低价往往对应小规格/低销量/引流价）。
设一个销量门槛再排序，结果才接近「正常人会买的东西」。

注意：
* 两边的 price 都是**券后价**。
* 拼多多只返回「最低 SKU 价」，多规格链接的单价是**下限**，看「元/件」自检。
* 淘宝「原价」多为商家虚标，只看券后价。
* 淘宝销量是分档的（100/500/1000…），只能当数量级参考。
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
    SUGAR_FREE,
    parse_total_ml,
    sugary_component,
)

from shopping_mcp import config  # noqa: E402
from shopping_mcp.adapters.pdd import PddAdapter  # noqa: E402
from shopping_mcp.adapters.taobao import TaobaoAdapter  # noqa: E402

EXCLUDE_DEFAULT = "茶,苏打水,临期"

# 标题里出现这些词 + 无糖 → 多半是含糖/混合 SKU 链接（最低价那个是原味）
SUSPECT = re.compile(r"原味|含糖")


def suspicious_mixed(title: str) -> bool:
    """判断是不是「用无糖堆关键词、最低价却是含糖原味」的混合链接。

    不能见到「原味」就杀——淘宝大量合法标题写的是「无糖原味」。
    规则：出现「含糖」直接可疑；出现「原味」时，只有它附近（±4 字）**没有**
    无糖类标记才算可疑。
    """
    if "含糖" in title:
        return True
    for m in re.finditer("原味", title):
        window = title[max(0, m.start() - 4) : m.end() + 4]
        if not SUGAR_FREE.search(window):
            return True
    return False

DEFAULT_KEYWORDS = (
    "无糖汽水,0糖气泡水,无糖可乐,无糖碳酸饮料,"
    "元气森林,芬达无糖,雪碧无糖,百事无糖"
)


def collect(
    adapter,
    keywords: list[str],
    pages: int,
    excludes: list[str],
    min_sales: int = 0,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    dropped = {"suspect": 0, "weak": 0, "notsugar": 0}

    for keyword in keywords:
        for page in range(1, pages + 1):
            try:
                items = adapter.search(keyword, page=page, page_size=50)
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] {adapter.platform}/{keyword} p{page}: {exc}", file=sys.stderr)
                continue
            for it in items:
                title = it.title or ""
                if it.item_id in seen:
                    continue
                if not SUGAR_FREE.search(title):
                    dropped["notsugar"] += 1
                    continue
                if suspicious_mixed(title):
                    dropped["suspect"] += 1
                    continue
                if sugary_component(title):
                    dropped["suspect"] += 1
                    continue
                if any(h in title for h in EXCLUDE_HINTS):
                    continue
                if any(e in title for e in excludes):
                    continue
                if min_sales and (it.sales is None or it.sales < min_sales):
                    dropped["weak"] += 1
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
                        "keyword": keyword,
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

    if any(dropped.values()):
        detail = "，".join(f"{k} {v}" for k, v in dropped.items() if v)
        print(f"  [{adapter.platform}] 过滤掉 {detail}", file=sys.stderr)
    return rows


def show(platform: str, rows: list[dict], top: int) -> None:
    rows = sorted(rows, key=lambda r: r["unit"])[:top]
    print(f"\n{'=' * 86}")
    print(f"  {platform}  —  券后单价最低 {len(rows)} 条（元/升）")
    print(f"{'=' * 86}")
    print(f"{'#':>3}  {'元/L':>7}  {'元/件':>7}  {'券后价':>8}  {'规格':<13} {'销量':>7}  {'佣金':>5}  商品")
    for i, r in enumerate(rows, 1):
        s = f"{r['sales']/10000:.0f}万" if isinstance(r["sales"], int) and r["sales"] >= 10000 else str(r["sales"])
        rate = f"{r['rate']:.1f}%" if r["rate"] is not None else "-"
        pp = f"¥{r['per_piece']:.2f}" if r["per_piece"] else "-"
        mark = " ⚠多SKU" if r["ambiguous"] else ""
        print(
            f"{i:>3}  ¥{r['unit']:>5.2f}  {pp:>7}  ¥{r['price']:>6.2f}  {r['spec']:<13} "
            f"{s:>7}  {rate:>5}  {r['title'][:28]}{mark}"
        )


def summary(taobao: list[dict], pdd: list[dict]) -> None:
    print(f"\n{'=' * 86}")
    print("  平台对比")
    print(f"{'=' * 86}")
    print(f"{'指标':<18} {'淘宝':>18} {'拼多多':>18}")

    def stat(rows: list[dict], label: str) -> str:
        if not rows:
            return "-"
        if label == "命中商品数":
            return f"{len(rows)} 条"
        if label == "最低元/L":
            return f"¥{min(r['unit'] for r in rows):.2f}"
        if label == "中位元/L":
            vals = sorted(r["unit"] for r in rows)
            return f"¥{vals[len(vals)//2]:.2f}"
        if label == "平均佣金率":
            vals = [r["rate"] for r in rows if r["rate"] is not None]
            return f"{sum(vals)/len(vals):.2f}%" if vals else "-"
        if label == "中位销量":
            vals = sorted(r["sales"] for r in rows if r["sales"])
            return str(vals[len(vals)//2]) if vals else "-"
        return "-"

    for label in ("命中商品数", "最低元/L", "中位元/L", "平均佣金率", "中位销量"):
        print(f"{label:<18} {stat(taobao, label):>18} {stat(pdd, label):>18}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", default="", help="逗号分隔，默认用内置无糖饮料关键词组")
    ap.add_argument("--keyword", default="", help="单个关键词（兼容旧用法）")
    ap.add_argument("--pages", type=int, default=1, help="每个关键词翻几页")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-sales", type=int, default=500, help="最低销量门槛，0=不过滤")
    ap.add_argument("--exclude", default=EXCLUDE_DEFAULT)
    args = ap.parse_args()

    if args.keyword:
        keywords = [args.keyword]
    elif args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        keywords = [k.strip() for k in DEFAULT_KEYWORDS.split(",") if k.strip()]

    excludes = [e.strip() for e in args.exclude.split(",") if e.strip()]
    config.load_dotenv()

    print(f"关键词：{', '.join(keywords)}")
    print(f"每平台每词 {args.pages} 页 × 50，销量门槛 {args.min_sales or '无'}")

    taobao = collect(TaobaoAdapter(), keywords, args.pages, excludes, args.min_sales)
    pdd = collect(PddAdapter(), keywords, args.pages, excludes, args.min_sales)

    show("淘宝", taobao, args.top)
    show("拼多多", pdd, args.top)
    summary(taobao, pdd)

    print("\n提示：拼多多多规格链接只给最低 SKU 价，「元/件」明显偏低的要回商品页核实。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
