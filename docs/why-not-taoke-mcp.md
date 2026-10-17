# 为什么没用现成的 taoke-mcp

调研对象：[`liuliang520530/taoke-mcp`](https://github.com/liuliang520530/taoke-mcp)
（125 星 / 19 fork / ISC / 2026-10-04 仍有提交。功能确实对口：淘宝+拼多多商品搜索、转链、优惠券、订单查询。）

放弃的理由是四条实证问题，不是"自己写更好玩"。

## 1. 授权走的是作者的 OAuth 应用，不是你的 appkey

README 给出的授权链接：

```
https://oauth.taobao.com/authorize?response_type=token&client_id=34297717&state=1212&view=web
https://jinbao.pinduoduo.com/open.html?client_id=313cc43a30cf487da0a336d9f2df7de2
    &response_type=code&redirect_uri=http%3A%2F%2Fddk.weiququ.cn%2FApi%2Fget_access_token
```

`client_id=34297717` 是**作者的**淘宝开放平台应用 ID；拼多多的 `redirect_uri` 指向 **`ddk.weiququ.cn`**，一个第三方域名。

后果：授权得到的 session token 绑定在作者的推广身份下。你的推广位、佣金归属、订单数据都记在他的应用上，而且你把一个能读你联盟账户的 token 交给了第三方。

## 2. 启动时从作者服务器拉配置

```
ENV_URL=https://config.sinataoke.cn/api/mcp/secret
ENV_SECRET=url:mcp.sinataoke.cn
ENV_OVERRIDE=false
```

每次启动会去 `config.sinataoke.cn` 取一份 "secret" 环境变量。`ENV_OVERRIDE=false` 表示不覆盖你已设的变量，但**运行时行为仍由远程下发内容决定** —— 作者改服务端就能改你的运行逻辑，不需要你更新代码。

依赖里同时存在 `jsonwebtoken` + `ws`(websocket) + 已废弃的 `request`，攻击面偏大。

## 3. 包身份链是断的

| 位置 | 值 |
|---|---|
| 代码仓库 | `liuliang520530/taoke-mcp` |
| package.json `repository` / `homepage` | `liuliang520500/sinataoke_cn` |
| README 声称的包名 | `@liuliang520500/sinataoke_cn@latest` |
| npm 上的转发包 | `@iflow-mcp/taoke-mcp`、`n8n-nodes-taoke-mcp` |

两个账号名差一位（`530` / `500`），npm 上还有第三个发布者在转发。无法确认是同一个人。

## 4. 几乎没有社区验证

npm 月下载 39 / 40 次。出问题没有别人踩过坑。

## 另外

京东已下线（`package.json` 的 description 自己写明「京东已下线」），README 保留着 JD 的环境变量说明属于残留。

## 结论

功能设计可以借鉴，实现不复用。本项目采取的策略：

- 你自己去官方开放平台申请 appkey，佣金归你
- 凭证只存本地 `.env`，不进 DSH 配置
- 无远程配置拉取，无第三方域名
- 代码量小到可以一眼读完（约 1000 行），字段映射可审计

代价是要走平台审核。这正是为什么本项目做了 mock 模式：审核期间链路和 prompt 都能先调好，凭证下来直接切真实数据。
