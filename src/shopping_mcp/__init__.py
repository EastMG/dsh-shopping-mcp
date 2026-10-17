"""Unified affiliate-product MCP server for Chinese e-commerce platforms.

Only official open-platform APIs are used:
  * Taobao  -> 淘宝联盟 / TOP  (eco.taobao.com/router/rest)
  * JD      -> 京东联盟        (api.jd.com/routerjson)
  * Pinduoduo -> 多多进宝       (gw-api.pinduoduo.com/api/router)

No third-party proxy, no remote config fetch, no credential forwarding.
"""

__version__ = "0.1.0"
