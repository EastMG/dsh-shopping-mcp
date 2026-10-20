# 淘宝联盟接入指南（实测记录）

个人开发者接淘宝客 API 的实际流程与坑。**不需要备案域名、不需要营业执照**，
但需要注册淘宝联盟（推广者）账户并完成推广者备案。

## 结论先看

| 项 | 结果 |
|---|---|
| 开发者资质 | 填 3 个字段，**秒过** |
| 建应用 | 需要「联盟备案身份」，没有推广者备案时该下拉是空的 |
| OAuth | **回调必须是「非淘系域名」**，`www.taobao.com` 会被拒，`example.com` 可以 |
| 关键词搜索 | 需要权限包 **27939**，且用 **`.upgrade`** 变体方法 |
| 商品详情 | 需要权限包 **16189** |
| 转链 | `privilege.get` 要单独权限包；没有时可用 **11655 淘口令** 替代 |

## 流程

### 1. 注册淘宝联盟（推广者）

访问 <https://pub.alimama.com/>，按提示注册。要点：

- 身份由**淘宝账户绑定的支付宝实名**确定，一个真实身份只能注册一个联盟账户
- 注册前会做反作弊校验

### 2. 推广者备案

联盟后台 → 推广管理 → **媒体备案管理** → 新增媒体备案：

- 媒体类型：他方平台 / 社交平台（微信、公众号这类不需要域名）
- 备案通过后，才能在第 4 步的「联盟备案身份」里选到它

### 3. 开放平台开发者资质

<https://aff-open.taobao.com/developer/index.htm> → 新建应用 → 联盟开放 →
一级类目「联盟合作」→ 二级类目「联盟通用应用」→ 申请资质。

联系人姓名/电话/邮箱是从账号里预填的**只读**字段，点提交即可，通常秒过。

### 4. 创建应用

同一个入口进「创建应用」，填：

| 字段 | 取值 |
|---|---|
| 应用名称 | 自定义 |
| 联盟推广阵地 | 他方平台 |
| 联盟备案类型 | 社交平台 |
| 联盟备案身份 | 选第 2 步备案的媒体 |

创建成功后应用状态是**已上线**，管理页能拿到 `AppKey` / `AppSecret`（点眼睛图标显示明文）。

### 5. 配置回调地址

应用管理 → 应用设置 → 基本信息。**注意这个表单在 iframe 里**，父页面有 JS 报错时看起来像白屏，
可以直接打开 iframe 地址：

```
https://console.open.taobao.com/app/app.htm?hiddenLayout=true&appkey=<AppKey>&appId=<appId>#/config
```

回调地址填一个**非淘系域名**，例如 `https://example.com`。

> ⚠️ 保存必须用**真实键入**。阿里 Fusion 组件不接受程序赋值（`el.value = x` + dispatchEvent 会被吞），
> 而且「先赋值再键入」会拼成重复 URL，保存进库的就是非法值。

### 6. 申请权限包

应用管理主页 → 权限包列表 → 对需要的包点「申请权限」，填理由。

常用三个：

| 权限包 | ID | 用途 |
|---|---|---|
| 淘宝客【推广者】商品物料获取 | **27939** | 关键词搜索（实际接口是 `.upgrade` 变体） |
| 淘宝客【公用】物料信息查询 | **16189** | 商品详情 |
| 淘宝客【公用】淘口令生成 | **11655** | 转链替代方案 |

### 7. OAuth 拿 session

```
https://oauth.taobao.com/authorize?response_type=code&client_id=<AppKey>&redirect_uri=<回调地址>&state=xxx
```

授权后用 code 换 token：

```python
httpx.post("https://oauth.taobao.com/token", data={
    "grant_type": "authorization_code",
    "code": code,
    "client_id": APP_KEY,
    "client_secret": APP_SECRET,
    "redirect_uri": CALLBACK,
})
```

返回里有 `access_token`（1 天）、`refresh_token`（30 天）、`taobao_user_id`。
续期用 `scripts/taobao_refresh_token.py`（`grant_type=refresh_token`）。

### 8. 取 PID

联盟后台 → 推广管理 → 推广位管理 → 新增推广位。

PID 形如 `mm_<memberId>_<siteId>_<adzoneId>`，`site_id` 就是媒体备案的媒体ID，
`adzone_id` 是推广位ID。本程序会自动从 PID 解析出 `site_id` / `adzone_id`。

## 接口与坑

### 搜索必须用 `.upgrade` 变体

权限包 27939 拿到后，经典的 `taobao.tbk.dg.material.optional` **仍然报
`isv.permission-api-package-limit`**。真正被这个包打开的是：

```
taobao.tbk.dg.material.optional.upgrade
```

返回是**嵌套结构**，而且直接带推广链接，所以搜索即转链：

```
result_list.map_data[]:
  item_basic_info        title / pict_url / shop_title / tk_total_sales
  item_id                不透明 token（不是数字 num_iid）
  price_promotion_info   final_promotion_price(券后价) / zk_final_price / reserve_price
  publish_info           click_url(带 PID) / coupon_share_url / income_info
```

### 佣金字段口径

`publish_info.income_rate` **本身就是百分比**（`0.9` 表示 0.9%），
可用 `commission_amount / price` 交叉验证。`income_info.commission_rate` 是它的 ×100，仅作兜底。

### 详情不含券和佣金

`taobao.tbk.item.info.get` 只给折后价，没有券额和佣金率。若同一商品先搜过，
适配器会用搜索缓存补齐，避免同一商品两次返回不同价格。

### 转链走淘口令

`taobao.tbk.privilege.get` 需要单独的转链权限包。没申请时用 11655：

```
输入：https://s.click.taobao.com/t?e=...            （搜索返回的 click_url）
输出：<口令>￥<分享码>￥ https://m.tb.cn/<短链>  商品标题
```

> 淘口令接口**只接受规范商品链接**，不接受 `uland.taobao.com/item/edetail`
> 落地页（会报「链接不符合规范」）。所以 `convert_link` 优先复用搜索时缓存的 `click_url`。

### 其他

- `taobao.tbk.shop.get`（店铺搜索）在 16516 包里，与商品搜索是两回事
- `taobao.tbk.order.details.get`（订单查询）是邀请制，要单独申请
- access_token 只有 1 天；过期后的报错很像「权限不足」，先看 token 是否过期

## 数据结构备忘

```json
{
  "item_basic_info": {
    "title": "...",
    "pict_url": "https://img.alicdn.com/...",
    "shop_title": "...",
    "tk_total_sales": "1000"
  },
  "item_id": "<不透明 token>",
  "price_promotion_info": {
    "final_promotion_price": "6.88",
    "zk_final_price": "11.88",
    "reserve_price": "99"
  },
  "publish_info": {
    "click_url": "//s.click.taobao.com/t?e=...",
    "coupon_share_url": "//uland.taobao.com/coupon/edetail?e=...",
    "income_rate": 0.9,
    "income_info": { "commission_amount": "0.06", "commission_rate": 90 }
  }
}
```
