# 京东联盟接入指南（实测记录）

按官方流程走完一遍之后的记录，账号信息已脱敏。

## 结论先看

**京东联盟 API 对「没有备案域名的个人工具」不可行**，但原因不在资质：

- 入驻、推客审核、导购媒体、应用、AppKey/AppSecret、PID **全都能拿到**
- 卡在**接口权限**：基础权限包不含关键词搜索，也不含导购媒体的转链接口

| 接口 | 结果 |
|---|---|
| `jd.union.open.goods.jingfen.query`（京粉精选） | ✅ 可用，按 `eliteId` 频道取；传 `keyword` 报**参数错误** |
| `jd.union.open.goods.query`（关键词商品查询） | ❌ `403 无访问权限` |
| `jd.union.open.promotion.bysubunionid.get`（导购媒体转链） | ❌ `403 无访问权限` |
| `jd.union.open.promotion.common.get`（网站/APP 转链） | ⚠️ `2001701 不支持 siteId 用于此种方式推广,只支持网站/APP` |
| `jd.union.open.goods.bigfield.query`（详情） | ⚠️ 需要 `sceneId` 参数 |

所以京东侧只能做「精选/榜单 → 展示」，做不了关键词搜索和社交转链。

## 流程

1. <https://union.jd.com/> 登录 → 签署《京东联盟开放平台服务协议》→ 完成开放平台入驻
2. 开通联盟推客（个人）→ 实名信息 + 身份证正反面 + 《授权委托书》→ **审核 1-3 个工作日**
3. 推广管理 → **导购媒体管理** → 创建导购媒体（聊天工具 / 微信这类社交场景）
4. 开放平台 → 应用管理 → 创建应用 → 拿 `AppKey` / `AppSecret`（点眼睛图标看明文）
5. 推广管理 → 推广位管理 → 创建推广位 → 拿 `positionId`，PID 形如
   `<联盟ID>_<媒体ID>_<推广位ID>`
6. 填 `.env`：`JD_APP_KEY` / `JD_APP_SECRET` / `JD_SITE_ID` / `JD_POSITION_ID`
   （`JD_SITE_ID` 填导购媒体ID）

## 接口按等级发放

开放平台 → 接口管理 → 「开放平台等级调用规则」：

| 等级 | 子订单量 | GMV / UV |
|---|---|---|
| V0 | ≥0 | ≥0 |
| V1 | ≥50 | GMV ≥1000 或 UV ≥1000 |
| V2 | ≥900 | GMV ≥3万 或 UV ≥3万 |
| V3 | ≥3000 | GMV ≥150万 或 UV ≥30万 |
| V4 | ≥3万 | GMV ≥3000万 或 UV ≥300万 |

创建应用后系统自动开通**基础权限接口包**（19 个接口）。关键词搜索
`goods.query` 和导购媒体转链 `bysubunionid` **不在这个包里**，需要更高等级。

**死锁**：V0→V1 需要 ≥50 个真实子订单，但拿不到转链权限就产生不了订单。

## 一条看似可行、实际走不通的路

`jd.union.open.promotion.common.get`（网站/APP 转链）必填 `siteId`，而 `siteId` 只接受
「已 ICP 备案的网站 / 已上架 APP / 真实流量媒体」，导购媒体 ID 被明确禁用。

理论上导购媒体该用 `bysubunionid`，它不需要 `siteId`：

| 参数 | 必填 | 说明 |
|---|---|---|
| `materialId` | 是 | 商品链接 / 联盟商品ID |
| `sceneId` | 是 | 1 或 2（2 需向 cps@jd.com 申请权限） |
| `subUnionId` | 否 | 子渠道标识 |
| `positionId` | 否 | 推广位 ID |
| `chainType` | 否 | 1 长链 / 2 短链 / 3 长链+短链 |

返回 `shortURL`（`https://u.jd.com/XXXXX`）。但该接口在基础包外，实测 `403`。

## 产品级限制：京东基础权限没有关键词搜索

与商品相关的基础接口只有：

| 接口 | 能力 | 支持关键词？ |
|---|---|---|
| `jd.union.open.goods.jingfen.query` | 联盟精选商品池，按频道/类目取 | ❌ |
| `jd.union.open.goods.material.query` | 猜你喜欢，按设备信息推荐 | ❌ |
| `jd.union.open.goods.bigfield.query` | 按 skuId 查详情 | — |
| `jd.union.open.goods.promotiongoodsinfo.query` | 按 skuId 查推广商品信息 | — |
| `jd.union.open.goods.query` | 关键词商品查询 | **不在基础权限内** |

## 代码侧踩到的两个坑（已修）

1. **响应 key 拼写**：京东网关返回 `jd_union_open_promotion_common_get_responce`
   —— 是 `responce` 不是 `response`。适配器现在两种都认。
2. **业务错误藏在 result 里**：京东有时把 `{"code":..,"message":..}` 作为 JSON 字符串
   塞进 `result`，若只判断「没有 data」就会当成空结果，表现为「没商品」。适配器现在会抛出。

## 如果将来条件变化

- **有备案域名**：注册成网站媒体，改用 `common.get` 转链
- **愿意先攒业绩**：在京粉 APP 手动推广攒够 V1（子订单 ≥50），再回头用 API

届时 `.env` 里的 `JD_APP_KEY` / `JD_APP_SECRET` / `JD_POSITION_ID` 可直接复用。
