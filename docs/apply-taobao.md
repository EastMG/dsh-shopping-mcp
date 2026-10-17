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

## 待办

1. **申请 27939「商品物料获取」权限包**（已提交，等生效）—— 它含"根据关键词搜索商品"
2. 取推广位 PID：`mm_3893269134_<siteId>_<adzoneId>`
   （联盟后台被验证码拦；也可能通过 `taobao.tbk.sc.publisher.info.get` 之类的接口取）
3. access_token 只有 1 天有效，**过期前用 refresh_token 续**：
   `POST https://oauth.taobao.com/token`，`grant_type=refresh_token`
   （refresh_token 有效期 30 天，已写入 `.env` 的 `TAOBAO_REFRESH_TOKEN`）
