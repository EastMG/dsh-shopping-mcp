"""拼多多无糖饮料「券后单价」横向比价。

为什么需要脚本：官方搜索接口不能按「元/升」排序，必须先拿到「券后价」和「总容量」，
自己做除法——而总容量藏在商品标题里（`330ml*24罐`、`1L*12瓶` 这类写法）。

用法：
    uv run python scripts\\pdd_unit_price.py
    uv run python scripts\\pdd_unit_price.py --pages 2
    uv run python scripts\\pdd_unit_price.py --keywords 无糖茶,无糖可乐

输出：按券后单价（元/升）从低到高排序的表，并保存 JSON 明细。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shopping_mcp.adapters.pdd import PddAdapter  # noqa: E402
from shopping_mcp import config  # noqa: E402

# 覆盖碳酸 / 非碳酸两大类
DEFAULT_KEYWORDS = [
    # 碳酸
    "无糖可乐",
    "0糖气泡水",
    "无糖苏打水",
    "无糖碳酸饮料",
    "元气森林",
    "无糖雪碧",
    # 非碳酸
    "无糖茶",
    "无糖乌龙茶",
    "无糖冰红茶",
    "无糖茉莉花茶",
    "无糖绿茶饮料",
    "无糖电解质水",
    # 通用
    "无糖饮料",
]

CARBONATED_HINTS = ("可乐", "气泡", "苏打", "碳酸", "汽水", "雪碧", "芬达")

# 明确不是「无糖饮料」的品类（酸奶/酒/果汁浓浆等）
EXCLUDE_HINTS = ("酸奶", "酒", "啤酒", "醪糟", "米酒", "果酱", "奶粉", "浓浆", "糖浆", "咖啡豆")

SUGAR_FREE = re.compile(r"无糖|0糖|零糖|０糖|0蔗糖|零蔗糖|无蔗糖")

# 330ml*24罐 / 1L×12瓶 / 500毫升*6
SPEC_UNIT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|毫升|ML|Ml|mL|l|L|升)\s*[*×xX✕]\s*(\d+)",
    re.IGNORECASE,
)
# 只有容量、没有件数：330ml
SPEC_ONLY = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|毫升|ML|Ml|mL|l|L|升)", re.IGNORECASE)
# 件数单独写：24罐 / 12瓶
COUNT_ONLY = re.compile(r"(\d+)\s*(罐|瓶|盒|袋|包|支|听|桶)")

# 多 SKU 标题：「12/24瓶」「12-24瓶」——低价往往对应小规格，必须按**小的**算，
# 否则会把「12 瓶 ¥35.8」误算成 24 瓶，单价直接砍半。
MULTI_COUNT = re.compile(r"(\d+)\s*[/\-~～]\s*(\d+)\s*(罐|瓶|盒|袋|包|支|听|桶)")
# 买 N 送 M：实际到手 N+M
BUY_GIFT = re.compile(r"买\s*(\d+)\s*送\s*(\d+)")

# 风险标记
RISK_HINTS = {
    "临期": "临期",
    "近效期": "临期",
    "含糖": "混合SKU(含糖)",
}


def parse_total_ml(title: str) -> tuple[float | None, str, bool]:
    """从标题解析总容量(ml)。

    返回 (总ml, 规格说明, 是否多SKU歧义)。
    多 SKU 时**保守取小规格**——宁可低估容量，不要给出虚低的单价。
    """
    m_multi = MULTI_COUNT.search(title)
    m_gift = BUY_GIFT.search(title)

    m = SPEC_UNIT.search(title)
    if m:
        qty = float(m.group(1))
        unit = m.group(2).lower()
        count = int(m.group(3))
        ml = qty * (1000 if unit in ("l", "升") else 1)
        ambiguous = False
        if m_multi:
            a, b = int(m_multi.group(1)), int(m_multi.group(2))
            count = min(a, b)
            ambiguous = True
        elif m_gift:
            count = int(m_gift.group(1)) + int(m_gift.group(2))
        return ml * count, f"{m.group(1)}{m.group(2)}×{count}", ambiguous

    m2 = SPEC_ONLY.search(title)
    if m2:
        qty = float(m2.group(1))
        unit = m2.group(2).lower()
        ml = qty * (1000 if unit in ("l", "升") else 1)
        if m_multi:
            count = min(int(m_multi.group(1)), int(m_multi.group(2)))
            return ml * count, f"{m2.group(1)}{m2.group(2)}×{count}", True
        if m_gift:
            count = int(m_gift.group(1)) + int(m_gift.group(2))
            return ml * count, f"{m2.group(1)}{m2.group(2)}×{count}", False
        m3 = COUNT_ONLY.search(title)
        if m3:
            count = int(m3.group(1))
            return ml * count, f"{m2.group(1)}{m2.group(2)}×{count}", False
        return ml, f"{m2.group(1)}{m2.group(2)}（件数未知）", False

    return None, "容量未知", False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", default="", help="逗号分隔，默认用内置的一组")
    ap.add_argument("--pages", type=int, default=1, help="每个关键词翻几页（每页50）")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--json", default="", help="把明细写到这个路径")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] or DEFAULT_KEYWORDS
    config.load_dotenv()
    adapter = PddAdapter()

    seen: dict[str, dict] = {}
    for kw in keywords:
        for page in range(1, args.pages + 1):
            try:
                items = adapter.search(kw, page=page, page_size=50, sort="default", has_coupon=False)
            except Exception as exc:  # noqa: BLE001
                print(f"  [跳过] {kw} p{page}: {exc}", file=sys.stderr)
                continue
            for it in items:
                if it.item_id in seen:
                    continue
                title = it.title or ""
                if not SUGAR_FREE.search(title):
                    continue
                if any(h in title for h in EXCLUDE_HINTS):
                    continue
                total_ml, spec, ambiguous = parse_total_ml(title)
                if not total_ml or not it.price:
                    continue
                unit_price = it.price / (total_ml / 1000)  # 元/升
                risks = [label for hint, label in RISK_HINTS.items() if hint in title]
                seen[it.item_id] = {
                    "title": title,
                    "price": it.price,               # 券后价
                    "coupon": it.coupon_amount or 0,
                    "original": it.original_price,
                    "spec": spec,
                    "ambiguous": ambiguous,
                    "risks": risks,
                    "total_l": round(total_ml / 1000, 3),
                    "unit_price": round(unit_price, 2),
                    "sales": it.sales,
                    "commission_rate": it.commission_rate,
                    "carbonated": any(h in title for h in CARBONATED_HINTS),
                    "keyword": kw,
                    "goods_sign": it.extra.get("goods_sign"),
                }

    rows = sorted(seen.values(), key=lambda r: r["unit_price"])

    print(f"\n共命中 {len(rows)} 个「无糖 + 可算单价」的商品，按券后单价（元/升）升序\n")
    header = f"{'#':>3}  {'券后单价':>9}  {'券后价':>8}  {'规格':<16} {'销量':>8}  {'佣金':>5}  {'类型':<4} 标记 / 商品"
    print(header)
    print("-" * (len(header) + 20))
    for i, r in enumerate(rows[: args.top], 1):
        sales = r["sales"]
        sales_s = f"{sales/10000:.0f}万" if isinstance(sales, int) and sales >= 10000 else str(sales)
        rate = f"{r['commission_rate']:.1f}%" if r["commission_rate"] is not None else "-"
        kind = "碳酸" if r["carbonated"] else "无气"
        marks = []
        if r["ambiguous"]:
            marks.append("多SKU")
        marks.extend(r["risks"])
        mark_s = ("[" + ",".join(marks) + "] ") if marks else ""
        print(
            f"{i:>3}  ¥{r['unit_price']:>7.2f}/L  ¥{r['price']:>6.2f}  {r['spec']:<16} "
            f"{sales_s:>8}  {rate:>5}  {kind:<4} {mark_s}{r['title'][:30]}"
        )

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
