# 淘宝联盟：OAuth 已通，API 已真实调通（2026-10-18）

## 结论先行

> **不需要备案域名。** 之前判断错了。
>
> OAuth 失败的真实原因是：**淘宝禁止把自己的域名（`www.taobao.com`）当作回调地址**。
> 换成任意中性域名（我用 `https://example.com`）立刻通过，授权页正常弹出。

## 已打通的部分

| 项 | 结果 |
|---|---|
| 应用 | East晨曦，已上线，AppKey `35450610` |
| 回调URL | `https://example.com`（必须是**非淘宝域名**） |
| OAuth 授权 | ✅ 授权页正常，返回 code |
| token 交换 | ✅ 拿到 access_token（1 天）+ refresh_token（30 天） |
| 签名 | ✅ 真实网关接受 |
| **`taobao.tbk.shop.get`（店铺搜索）** | ✅ **真实调通**，返回真实店铺列表 |
| 权限包 16516「物料搜索」 | 已获得，但**实际只含 `taobao.tbk.shop.get`**（店铺搜索），不含商品搜索 |
| `taobao.tbk.dg.material.optional`（商品搜索） | ❌ `isv.permission-api-package-limit`，需另一个权限包 |

### 关键数据

```
app_key        = 35450610
taobao_user_id = 3893269134   ← PID 前缀 mm_<memberId>_
taobao_open_uid= AAFMzam2AOeLLiWB5eooKnDj
```

均已写入 `.env`（含 refresh_token 与 member_id）。

### 真实调通证据

`taobao.tbk.shop.get` 返回：

```json
{"tbk_shop_get_response": {"results": {"n_tbk_shop": [
  {"seller_nick": "悦音数码店", "shop_title": "悦音数码店", "user_id": 216314767562771122},
  {"seller_nick": "慕翎大码女装 ML家定制大码女装", ...}
]}}}
```

## 踩过的坑（按发现顺序）

1. **应用基本设置页面在 iframe 里**，父页面有 JS 报错所以看着像白屏：
   ```
   https://console.open.taobao.com/app/app.htm?hiddenLayout=true&appkey=35450610&appId=15172069#/config
   ```
   直接打开 iframe 地址才有表单。

2. **回调URL 的保存必须用真实键入**。用程序赋值（`el.value = x` + dispatchEvent）在阿里 Fusion 组件上会被吞掉；
   而且先赋值再键入会**拼接成重复 URL**（`https://www.taobao.comhttps://www.taobao.com`），
   导致保存进库的是非法值。要用 Playwright 的 `pressSequentially` 先清空再输入。

3. **`www.taobao.com` 作回调会被拒**（`app call back is invalidate / invalid_client`）。
   这是最迷惑的一点——错误信息看起来像"回调地址格式不对"，实际是"不允许用淘宝自己的域名"。

4. **权限包名不副实**：16516 描述写「根据关键词搜索商品、店铺」，实际 scope 里只有
   `taobao.tbk.shop.get`（店铺搜索）。商品搜索要另外的包（27939「商品物料获取」或 16518「物料精选」）。

## 当前确切状态（2026-10-18 晚）

### 已打通

| 项 | 值 / 结果 |
|---|---|
| AppKey | `35450610` |
| AppSecret | 已写入 `.env` |
| 回调URL | `https://example.com`（**必须是非淘系域名**） |
| access_token | 已写入 `.env`，有效期 1 天 |
| refresh_token | 已写入 `.env`，有效期 30 天 |
| `taobao_user_id` | `3893269134` |
| 阿里妈妈会员ID | `7057489931`（后台账户显示） |
| 媒体备案 | **审核通过**，媒体ID `3442300186`，社交微信公众号，主营 二次元/动漫 |
| `taobao.tbk.shop.get` | ✅ **真实调通**，返回真实店铺 |
| 权限包 16516 | 已获得（实测只含店铺搜索） |
| 权限包 27939（商品物料获取） | ❌ 我的自动化提交不生效，需人工点 |

### 还差一步：27939 权限包（商品搜索）

**PID 已到手**：`mm_7057489931_3442300186_116334800076`
（memberId `7057489931` / siteId `3442300186` = 媒体ID / adzoneId `116334800076`）

**权限包 27939「商品物料获取」状态：申请中**（等审核）。

目前各接口实测：

| 接口 | 状态 |
|---|---|
| `taobao.tbk.shop.get`（店铺搜索） | ✅ 可用 |
| `taobao.tbk.dg.material.optional`（商品搜索） | ❌ 需 27939 |
| `taobao.tbk.privilege.get`（商品转链） | ❌ 需对应权限包 |
| `taobao.tbk.dg.optimus.material`（物料精选） | ❌ 需 16518 |

> 注意：物料搜索接口返回的 `click_url` / `coupon_share_url` **本身就是推广链接**，
> 所以 27939 批下来后，搜索+转链一步到位，可能不需要单独的转链权限包。

### token 续期已脚本化

`scripts\taobao_refresh_token.py`（已实测通过）：

```bat
uv run python scripts\taobao_refresh_token.py           :: 续期并写回 .env
uv run python scripts\taobao_refresh_token.py --check   :: 只看当前 token
```

access_token 1 天有效、refresh_token 30 天。脚本会原地改写 `.env` 里的
`TAOBAO_SESSION` 和 `TAOBAO_REFRESH_TOKEN`，其它行（含注释）原样保留。

## 待办

1. **申请 27939「商品物料获取」权限包**（已提交，等生效）—— 它含"根据关键词搜索商品"
2. 取推广位 PID：`mm_3893269134_<siteId>_<adzoneId>`
   （联盟后台被验证码拦；也可能通过 `taobao.tbk.sc.publisher.info.get` 之类的接口取）
3. access_token 只有 1 天有效，**过期前用 refresh_token 续**：
   `POST https://oauth.taobao.com/token`，`grant_type=refresh_token`
   （refresh_token 有效期 30 天，已写入 `.env` 的 `TAOBAO_REFRESH_TOKEN`）


## 最终状态（2026-10-20）：淘宝全链路已打通

用户反馈权限包 27939 已通过。重测后淘宝**完全可用**。

### 关键发现：接口名是 `.upgrade` 变体

权限包 27939 拿到后，`taobao.tbk.dg.material.optional`（经典版）**仍然报权限不足**。
真正被 27939 打开的是：

```
taobao.tbk.dg.material.optional.upgrade
```

它返回的是**嵌套结构**，而且**直接带推广链接**，所以搜索即转链：

```
result_list.map_data[]:
  item_basic_info      title / pict_url / shop_title / tk_total_sales / brand_name
  item_id              不透明 token（不是数字 num_iid）
  price_promotion_info final_promotion_price(券后价) / zk_final_price / reserve_price
  publish_info         click_url(s.click，带 PID 追踪) / coupon_share_url / income_info
```

### 佣金字段校准

`publish_info.income_rate` **本身就是百分比**（0.9 = 0.9%），已用
`commission_amount / price` 交叉验证：

| 商品 | 券后价 | 佣金额 | 推导% | income_rate |
|---|---|---|---|---|
| 东方树叶 | 6.88 | 0.06 | 0.872 | 0.9 |
| 可口可乐 | 38 | 0.57 | 1.5 | 1.5 |
| 外星人电解质 | 68 | 0.92 | 1.353 | 1.35 |

`income_info.commission_rate` 是 `income_rate × 100`，仅作兜底。

### 三个接口都能用

| 接口 | 权限包 | 用途 |
|---|---|---|
| `taobao.tbk.dg.material.optional.upgrade` | 27939 | 关键词搜索 + 带追踪推广链接 |
| `taobao.tbk.item.info.get` | 16189 | 商品详情（接受不透明 item_id） |
| `taobao.tbk.tpwd.create` | 11655 | **淘口令生成**（转链替代方案） |

### 转链方案：`privilege.get` 没权限，改用淘口令

`taobao.tbk.privilege.get` 依然 403（没申请转链权限包），但 11655 淘口令可用：

```
输入：https://s.click.taobao.com/t?e=...
输出：38￥ HU591 dHtgTF3x3Jb￥ https://m.tb.cn/h.j2k2Ob9  商品标题
```

注意：淘口令接口**只接受规范商品链接**，不接受 `uland.taobao.com/item/edetail`
落地页（会报「链接不符合规范」）。所以适配器里加了 item_id → 推广链接的缓存，
`convert_link` 优先复用搜索时已生成的 `click_url`。

### 实测结果

```
search("无糖饮料")  → 10 条，例：倍思 BC1 耳机 ¥136 / 原价400 / 券87 / 佣金1.8% / 销量1000
convert_link(item)  → https://m.tb.cn/h.j2k2Ob9
detail(item)        → 商品详情
smoke               → 43/43 ALL GREEN
```

### 仍需注意

- access_token **1 天**有效，用 `scripts\taobao_refresh_token.py` 续（已脚本化，refresh 30 天）
- 转链权限包（privilege.get）没申请，淘口令是可用的替代
- MCP 服务需要**重启**才能加载新适配器（它用项目 `.venv` 跑，editable 安装）
