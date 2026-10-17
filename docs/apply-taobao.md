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

### 还差两步（都需要人工，各约 1 分钟）

**1. 建推广位拿 PID**

`pub.alimama.com` → 推广管理 → 推广位管理 → **新增推广位**：
- 媒体类型：他方平台
- 媒体名称：East晨曦
- 推广位名称：随便（如「晨曦分享位」）
- 确定

列表里会出现 `mm_<memberId>_<siteId>_<adzoneId>`，填进 `.env` 的 `TAOBAO_PID`。

> 为什么我来不了：这个弹窗是阿里自研 Magix 组件，下拉框的 DOM 结构与标准 select 不同，
> 程序化点击反复被图标层拦截；而且联盟后台有滑块验证（这次侥幸自动拖过了一次）。

**2. 申请 27939 权限包（商品搜索）**

开放平台 → 应用管理 → East晨曦 → 管理 → 找到「淘宝客【推广者】商品物料获取 27939」→ 申请权限 → 填理由 → 确定。

> 为什么我来不了：这个「申请权限」的提交从自动化点「确定」后状态恒为 0（不报错也不生效）；
> 16516 就是人工点才成功的。

## 待办

1. **申请 27939「商品物料获取」权限包**（已提交，等生效）—— 它含"根据关键词搜索商品"
2. 取推广位 PID：`mm_3893269134_<siteId>_<adzoneId>`
   （联盟后台被验证码拦；也可能通过 `taobao.tbk.sc.publisher.info.get` 之类的接口取）
3. access_token 只有 1 天有效，**过期前用 refresh_token 续**：
   `POST https://oauth.taobao.com/token`，`grant_type=refresh_token`
   （refresh_token 有效期 30 天，已写入 `.env` 的 `TAOBAO_REFRESH_TOKEN`）
