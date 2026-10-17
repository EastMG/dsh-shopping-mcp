# shopping-mcp

一个 MCP server，把**淘宝 / 京东 / 拼多多**的商品搜索、详情、转链（含优惠券）收成同一套工具，直接接进 DSH。

**只调官方开放平台接口。** 没有第三方代理、没有远程配置下发、没有凭证转发。

```
淘宝   淘宝联盟 / 淘宝客  →  https://eco.taobao.com/router/rest
京东   京东联盟            →  https://api.jd.com/routerjson
拼多多 多多进宝            →  https://gw-api.pinduoduo.com/api/router
```

## 为什么不用现成的 taoke-mcp

调研过 `liuliang520530/taoke-mcp`（125 星，功能对口），放弃的原因写在 [`docs/why-not-taoke-mcp.md`](docs/why-not-taoke-mcp.md)。一句话：它的授权走作者的 OAuth 应用，启动时还会从 `config.sinataoke.cn` 拉配置。本项目把这两条都换成了「你自己的 appkey + 本地 .env」。

## 快速开始

```powershell
# 1. 装依赖
uv sync

# 2. 先看状态（此时三家都是 mock 模式，没有任何凭证也能跑）
uv run shopping-mcp
```

`shopping-mcp` 是 **stdio** 传输的 MCP server，正常用法是被 DSH 拉起来，而不是手动运行。

### 挂进 DSH

叠加层文件已经写好并做过结构校验：[`dsh/mcp-shopping.patch.yml`](dsh/mcp-shopping.patch.yml)。

**注意：注册这一步必须在 DSH 会话之外执行。** 在会话内（沙箱内）写 `~/.dsh` 会被文件沙箱拒绝，提权也不放行；`dsh --dump-config` 同样因为要写 profile 目录而失败。所以它没被自动装上，需要你手动跑一次。

**最省事：双击 [`scripts/register-mcp.cmd`](scripts/register-mcp.cmd)** —— 在 DSH 沙箱外的普通上下文执行，会先备份再追加，写完校验 YAML，最后提示你重启。

或者在 PowerShell 里手动跑（等价）：

```powershell
cd F:\DeepSeek\DeepSeek-Workplace\DSH-Workplace-01\shopping-mcp
$env:UV_CACHE_DIR="$PWD\..\.uv-cache-probe"
uv run --with pyyaml python scripts\register_mcp.py
```

其它两种姿势：

**A. 手动粘贴** — 把 `dsh/mcp-shopping.patch.yml` 里 `- insert:` 那一段追加到
`C:\Users\<你>\.dsh\profiles\desktop\cordis.patch.yml` 末尾，重启 DSH。

**B. 临时挂载** — 不写任何配置，每次启动带参数：

```powershell
dsh --profile desktop --patch "F:\DeepSeek\DeepSeek-Workplace\DSH-Workplace-01\shopping-mcp\dsh\mcp-shopping.patch.yml" web
```

凭证**不写在这些配置里**。server 启动时自己读项目根目录的 `.env`，所以配置文件里看不到密钥，也不会进 config dump。

启动后调用 `shopping_status` 应能看到 `serverName: shopping` 下的四个工具。

## 配置凭证

```powershell
Copy-Item .env.example .env
# 然后编辑 .env，只填你拿到的那几家
```

未填的平台会自动降级成 **mock 模式**：工具照样能用，返回的是 `mock.local` 域名下的假数据，字段和真实数据完全一致。这样在等审核期间就能把链路和 prompt 调好。

随时调用 `shopping_status` 看当前哪家是 live、哪家是 mock、缺哪个变量。

### 填完先自检

拿到 appkey 后别急着让 agent 搜商品，先跑一次凭证自检——它真的发一次请求，很便宜：

```powershell
uv run python scripts\probe_creds.py              # 验证所有已配置的平台
uv run python scripts\probe_creds.py --platform pdd
```

它会区分这几类失败（它们的报错长得不一样，脚本会把 code 和原始返回一起打出来）：

| 现象 | 含义 |
|---|---|
| 签名错误 | secret 填错，或首尾有多余空格/引号 |
| 应用凭证无效 / 应用未上线 | key 复制错，或应用还在审核、没发布 |
| 权限不足 / 不支持的接口 | **最常见**：多多进宝没绑定、淘宝客权限没批下来 |
| PID / 推广位无效 | PID 取错 |
| 授权 session 失效 | OAuth token 过期，要重新授权 |

成功时会打印**平台真实返回的字段名**和**我们的归一化结果**并排对照，同时把完整原始返回写到 `_probe_raw_<platform>.json`。把这个输出贴给 agent，就能据此校准字段映射——现在的映射是照文档推的，只有对过真实返回才算数。

拼多多的完整申请流程见 [docs/apply-pdd.md](docs/apply-pdd.md)（含最容易漏的「多多进宝绑定」那一步）。

## 三家平台的申请门槛

### 拼多多多多进宝（门槛最低，建议先做这家）

1. 注册 [拼多多开放平台](https://open.pinduoduo.com/) 应用 → 拿 `client_id` / `client_secret`
2. 登录 [多多进宝](https://jinbao.pinduoduo.com/) → 推广管理 → 创建推广位 → 拿 `PID`
3. 给应用申请 `pdd.ddk.*`（多多进宝）接口权限

个人可注册，需实名。

### 京东联盟

1. [京东联盟](https://union.jd.com/) 注册推广者 → 拿网站 ID（`SITE_ID`）和推广位 ID（`POSITION_ID`）
2. 在京东开放平台创建应用 → 拿 `app_key` / `app_secret` → 申请 `jd.union.open.*` 权限

### 淘宝联盟（门槛最高）

1. [阿里妈妈](https://www.alimama.com/) 注册淘宝客 → 实名认证 + 推广媒体备案
2. [淘宝开放平台](https://open.taobao.com/) 创建应用 → 申请淘宝客 API 权限（审核通常 1-3 工作日）
3. 通过 `oauth.taobao.com` 授权拿 `session`
4. PID 形如 `mm_<memberId>_<siteId>_<adzoneId>`，`site_id` 与 `adzone_id` 由本程序自动从 PID 解析

三家具体权限目录以平台当期审核为准，本 README 不替代官方文档。

## 工具

| 工具 | 作用 |
|---|---|
| `shopping_status` | 三家凭证状态、当前模式、申请指引 |
| `search_products(platform, keyword, ...)` | 关键词搜索；支持 `has_coupon`、排序 |
| `get_product(platform, item_id)` | 按 id 查详情 |
| `convert_link(platform, item, sub_pid)` | 商品 id/链接 → 自己的推广链接 |

`platform` 取 `taobao` / `jd` / `pdd`。

统一后的商品字段：

```json
{
  "platform": "pdd",
  "item_id": "1234567890",
  "title": "...",
  "price": 179.0,              // 券后价 / 到手价
  "original_price": 259.0,
  "coupon_amount": 80.0,
  "commission_rate": 5.5,      // 百分比
  "commission_amount": 9.85,   // 预估佣金
  "sales": 12000,
  "shop_name": "...",
  "item_url": "...",
  "promotion_url": "...",
  "image_url": "..."
}
```

## 单位归一化（踩过的坑）

| 平台 | 坑 | 本项目怎么处理 |
|---|---|---|
| 三大平台通用 | `commission_rate` 历史上有过小数/百分比/万分比几种口径 | `_normalize_rate()` 按量级判断，原始值保留在 `raw` |
| 拼多多 | 所有金额单位是**分** | `_cents()` 统一除以 100 |
| 拼多多 | `promotion_rate` 是**千分比**（100 = 10%） | `_per_mille()` 除以 10 |
| 京东 | 业务结果藏在 `<method>_response.result` 里，而且是 **JSON 字符串** | 二次解析 |
| 淘宝 | `has_coupon` 要传字符串 `"true"` 不是布尔 | adapter 内转换 |

任何平台返回的原始 payload 都在 `raw` 字段（`to_dict()` 默认不输出，调试时可用）。**归一化一旦有偏差，用 `raw` 对照官方文档核对**——不要直接相信换算结果。

## 安全边界

- 凭证只从本地 `.env` 或进程环境读取，**不写进 DSH 配置**，所以不会出现在配置 dump 或共享 patch 里
- 不抓网页、不模拟登录、不碰支付环节
- 只读性质：搜索、查详情、生成推广链接。没有下单、没有改价、没有动账户设置
- `.env` 必须留在本机，不要提交到 git

## 当前状态

### 已验证

| 项 | 方式 | 结果 |
|---|---|---|
| 签名算法（三平台共用） | 与手算 MD5 对齐 | 通过 |
| 单位归一化（分→元、千分比→百分比、汇率三口径） | 单元断言 | 通过 |
| 淘宝 PID 解析 / 拼多多 `sales_tip` 解析 / `goods_sign` 判别 | 单元断言 | 通过 |
| 五个工具的 MCP 协议往返 | 内存传输握手 + `tools/list` + `tools/call` | 43/43 通过 |
| 商品字段一致性（价格 = 原价 − 券） | 断言 | 通过 |
| `has_coupon` 确实只返回有券商品 | 断言 | 通过 |
| 非法 platform 不崩、返回 `ok:false` | 断言 | 通过 |
| DSH 叠加层结构与 YAML 合法性 | 结构校验 | 通过 |
| smoke 的机器无关性 | 灌入假凭证后重跑 | 仍 43/43，不依赖本机 `.env` |
| **拼多多真实接口全链路** | **真实 appkey + 已完成授权备案** | **✅ 通过** |

**拼多多 live 实核（2026-10-18）**：

| 能力 | 结果 |
|---|---|
| 签名 | ✅ 真实网关接受（走到业务校验层，不再报签名错） |
| `pdd.ddk.goods.search` | ✅ 返回真实商品：`【原道】弦雪入耳DSP有线耳机` ¥49.99/¥60.99、券 ¥10、佣金率 3.0%、销量 4076 |
| `pdd.ddk.goods.detail` | ✅ 用 `goods_sign` 查到详情 |
| `pdd.ddk.goods.promotion.url.generate` | ✅ 生成真实推广链接 + 短链 `https://p.pinduoduo.com/lL4kjhCQ?sc=EFAC` |
| `pdd.ddk.member.authority.query` | ✅ `bind=1`（授权备案已完成） |
| 传数字 goods_id | ✅ 按预期拒绝，并给出「改用 extra.goods_sign」的指引 |

跑法：`uv run python scripts\smoke.py`（43 项，全 mock，无需凭证）

### 未验证

- **淘宝 / 京东从未调通** —— 还没申请这两家
- **DSH 里的 stdio 启动** 未验证。会话内沙箱禁止创建子进程双向管道（WinError 5），所以协议层是用内存传输测的；真实 stdio 拉起要等你注册并重启 DSH 后确认
- 拼多多 `ddk.goods.search` 对新应用有节流，同样关键词有时返回 0 条、有时 3 条。这不是 bug，重试即可

### 踩过的坑（已修，都是真实接口才暴露的）

| 坑 | 表现 | 修法 |
|---|---|---|
| `commission_rate` 走 `to_float()` | 该函数为金额舍入到 2 位，把 `0.155` 变成 `0.15` → 报 15.0% 而非 15.5% | 改用原始 `float()` |
| Windows 管道 stdout 默认 cp936 | 中文标题乱码 / `UnicodeEncodeError` | `main()` 里 `reconfigure(encoding="utf-8")` |
| 拼多多响应 key 不总镜像方法名 | `member.authority.query` 实际返回 `authority_query_response`，被误报 "unexpected response shape" | 回退到唯一的 `*_response` key |
| `pageSize` 必须 10-100 | 传 2 报 `50001 / 3000000` | adapter 内 clamp |
| `goods_id_list` 已下线 | 转链报 `40005`，详情报 `40004` | 改用 `goods_sign`（单数）/ `goods_sign_list`（转链） |
| `pdd.ddk.rp.prom.url.generate` 要 `p_id_list` | 报「请求参数不能为空：p_id_list」 | 传数组 |
| `promotion_url` 被误赋成图片地址 | 归一化结果里推广链接变成了商品图 | 置 `None`，只有转链后才有 |
| `sales` 是字符串 `"7万+"` | 直接 `to_int` 得到 None | 新增 `_parse_sales_tip()` |
| 拼多多搜索不返回店铺名 | `mall_name` 恒为空字符串 | 如实返回 `null`，不用品牌名冒充 |

## 开发

```powershell
uv sync                                              # 装依赖
uv run python scripts\smoke.py                       # 36 项检查，全部走 mock，不需要任何凭证
uv run --with pyyaml python scripts\check_patch.py    # 校验 DSH 叠加层结构
uv run python scripts\probe_creds.py                 # 用真实凭证验证字段映射
```

`scripts/register_mcp.py` 负责把服务注册进 DSH profile，幂等、自动备份、写后校验。
`scripts/register-mcp.cmd` 是它的双击入口。

`smoke.py` 是 **hermetic** 的：它会忽略真实 `.env` 并清掉环境里所有凭证变量，所以无论你机器上配没配 appkey，结果都一样。填了真凭证之后它也不会误报。

## License

[MIT](LICENSE) © 2026 EastMG

本项目只调用各平台官方开放平台 API，不包含任何平台的数据或代码。使用前请自行确认符合各平台的开放平台协议与推广规则。

