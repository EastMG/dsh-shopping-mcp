# 京东联盟：接入进展与真实门槛（实测记录）

2026-10-18 在真实账号（联盟ID 2038694924 / East-Morning）上走了一遍。

> **结论（当前）**：开放平台入驻已完成、导购媒体已建好、技术路线已确认可行，
> **但「创建应用」入口目前仍会跳回媒体管理页**，疑似要等推客身份/导购媒体审核通过。
> 审核期 1-3 个工作日。
>
> 另有一处产品级限制：京东的基础权限里**没有按关键词搜商品的接口**，只有精选/榜单/推荐类。

## 已经完成的（都实测过）

| 步骤 | 结果 |
|---|---|
| 京东商城账号登录联盟 | ✅ |
| 签署《京东联盟开放平台服务协议》 | ✅ |
| 开放平台入驻（签署协议 → 身份认证） | ✅ **已完成**，页面显示「您已完成开放平台入驻流程」 |
| 开通联盟推客（个人） | ✅ 已提交，**审核中（1-3 工作日）** |
| 实名信息 | 姓名/身份证/手机号由京东账号带入；邮箱、联系地址已填 |
| 身份证正反面 + 《授权委托书》 | ✅ 已上传/同意 |
| **创建导购媒体** | ✅ **成功**：ID `4109212769`，名称「晨曦Toolkit」，导购类型 聊天工具，媒体类型 微信，提交状态 **提交成功** |
| 开放平台基础权限 | ✅ 19 个接口「已开通」，含 `promotion.common.get`、`promotion.bysubunionid.get`、`user.pid.get`、`goods.bigfield.query`、`goods.jingfen.query` |

## 关键发现：转链不需要 siteId 的那条路

`jd.union.open.promotion.common.get`（网站/APP 转链）**必填 `siteId`**，而 `siteId` 只接受
「已 ICP 备案的网站 / 已上架 APP / 真实流量媒体」，且导购媒体 ID 被明确禁用。所以那条路对个人工具走不通。

但另一套接口不需要：

```
jd.union.open.promotion.bysubunionid.get   （社交媒体获取推广链接）
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `materialId` | **是** | 商品链接 / 联盟商品ID |
| `sceneId` | **是** | 1 或 2（2 需向 cps@jd.com 申请权限） |
| `subUnionId` | 否 | 子渠道标识（订单查询要看到需申请） |
| `positionId` | 否 | 推广位 ID |
| `chainType` | 否 | 1 长链 / 2 短链 / 3 长链+短链 |

**没有 `siteId`。** 返回 `shortURL`（`https://u.jd.com/XXXXX`），正是社交分享用的短链。
这就是没有备案域名时的合法路径 —— 走的「社交媒体」角色，不是「网站媒体」。

## 卡点：创建应用

- 应用管理页显示「我的应用(0) → 创建应用」。
- 但点击后**会被跳转到 `/manager/socialMediaMng`（导购媒体管理）**，无法进入应用创建表单。
- 直接猜 URL `/openplatform/console/appCreate` 返回 404。
- 推测：导购媒体状态是「提交成功」，需要变成审核通过；或推客身份审核通过后才解锁应用创建。

### 审核通过后从这里继续

1. 开放平台 → **应用管理 → 创建应用** → 拿 **Appkey / AppSecret**
2. 推广管理 → **推广位管理** → 创建推广位 → 拿 **推广位ID**（`positionId`）
3. 填 `.env`：`JD_APP_KEY` / `JD_APP_SECRET` / `JD_POSITION_ID`
   （`JD_SITE_ID` 可以留空——我们走 `bysubunionid`，不用 siteId）
4. 代码侧需要改 `adapters/jd.py`：
   - `convert_link` 改用 `jd.union.open.promotion.bysubunionid.get`，传 `materialId` + `sceneId=1` + `chainType`
   - `detail` 用 `jd.union.open.goods.bigfield.query`
5. 跑 `uv run python scripts\probe_creds.py --platform jd`

## 产品级限制：京东没有关键词搜索接口

基础权限清单里与商品相关的只有：

| 接口 | 能力 | 支持关键词？ |
|---|---|---|
| `jd.union.open.goods.jingfen.query` | 联盟精选商品池，按频道/类目取 | ❌ |
| `jd.union.open.goods.material.query` | 猜你喜欢，按用户设备信息推荐 | ❌ |
| `jd.union.open.goods.bigfield.query` | 按 skuId 查详情 | — |
| `jd.union.open.goods.promotiongoodsinfo.query` | 按 skuId 查推广商品信息 | — |
| ~~`jd.union.open.goods.query`~~ | 关键词商品查询 | **不在基础权限内** |

所以就算应用创建成功，`search_products(platform="jd", keyword=...)` 也无法用同一套语义实现。
可行的是：**京东侧只做「精选/榜单 → 转链」**，关键词搜索留给拼多多。

## 代码层面顺手修掉的两个坑

1. **响应 key 拼错的历史遗留**：京东网关返回 `jd_union_open_promotion_common_get_responce`
   —— 是 `responce` 不是 `response`。原实现只认 `_response`，必然匹配不到。现已两种都认。
2. **业务错误藏在 result 里**：京东有时把 `{"code":..,"message":..}` 作为 JSON 字符串塞进 `result`，
   原实现当成"没有 data"，于是搜索返回空列表——表现为"没商品"，实际是报错。现已抛出。

## 时间线

- 2026-10-18：签协议、提交个人推客审核、建导购媒体 `4109212769`
- 待办：等审核 → 建应用 → 取 PID → 改适配器 → 联调
