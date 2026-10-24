# DeepSeek 单次真实调用与启用记录

日期：2026-10-07（北京时间）。公网入口：http://47.243.189.12/ 。

## 真实调用

用户确认一次真实测试，预算 0.10 元，不自动重试。服务器在独立进程中使用部署版本的 `ExplanationService`，网页解释在测试期间关闭。实际发起 1 次请求；模型 `deepseek-flash`、非思考模式、输出上限 500 token、重试次数 0。费用保护在发出请求前检查，单次记录防止误重复执行。

- 调用时间：2026-10-07 20:22:21（北京时间）。
- 输入 362 token，输出 88 token，总计 450 token；缓存命中 0。
- 返回模式 `deepseek_verified_facts`，选择 5 个经过现有程序校验的合法事实 ID；文字仍由程序生成。
- 51 个配置的 SKU 次序、分数、决策指纹和排名指纹在调用前后相同。
- 首名 S0462，分数 0.8583227811355777。
- 决策指纹：`3ef65bfa4fa8db48c0177a42bb5596209e1f79726d3ad34fe1b7ab5689fd45d6`。
- 排名指纹：`d096455fd8f7a913356522712329e3d6e435295974bd0f0703c57e6a7c3a5a85`。

按本轮核验的[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，高峰单价上界估算为 0.001428 元；空闲单价估算为 0.000714 元。以上是依据响应 token 用量的计算，不是账户账单核对结果。服务器的脱敏测试报告为 `/root/zhixuan-llm-test-20261007.json`（root，600），其中不含密钥和原始供应商响应。

## 启用与验证

仅将 `/etc/zhixuan.env` 中 `PHONE_ENABLE_LLM=false` 改为 `true`，保持 root:root、600，重启 `zhixuan`；不读取或输出密钥。未修改应用代码、前端排版、图片映射、数据库数值或排名算法。

- `zhixuan` 和 Nginx 为 active；公网 `/healthz` 为 alive，`/readyz` 为 ready。
- `/api/v1/meta` 的 `features.llm=true`；网页状态显示“DeepSeek 已配置（仅解释）”。
- 浏览器生成默认推荐，显示 51 个配置；展开首名证据后出现“DeepSeek 辅助解释”按钮。未点击该付费按钮，不追加第二次调用。网页 POST 解释接口的第二次付费调用因此未单独实测。
- 公网推荐和 S0462 详情正常；首名 WebP 返回 200、image/webp、37,958 字节。
- CSV、XLSX、JSON 导出均实际获取并解析，包含 51 条排名，SKU 次序及决策/排名指纹一致；XLSX 分数与 API 分数一致。
- 服务器 `ss` 确认 API 仅监听 `127.0.0.1:8600`，Nginx 监听 80，未监听 443。回环健康接口成功；通过服务器私网 IP 访问 8600 被拒绝。外部直连 8600 的 HTTP 请求超时且收到 0 字节。一次外部 TCP 握手探测曾显示连接完成，不能单独据此判定服务可公开访问，以上述监听地址和实际 HTTP 探测为验证边界。
- 公网 `/data/catalog.sqlite` 返回 404。
- 数据库指纹仍为 `128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8`。

## 关闭解释的回退步骤

在服务器管理员终端执行（不输出密钥）：

```bash
sed -i 's/^PHONE_ENABLE_LLM=true$/PHONE_ENABLE_LLM=false/' /etc/zhixuan.env
chmod 600 /etc/zhixuan.env
systemctl restart zhixuan
```

随后核验健康接口和 `features.llm=false`。此操作保留现有数据、排名和程序证据解释。

## 仍待完成

域名尚未确定，DNS、HTTPS、HTTP 跳转、证书续期均未配置或验收；未新增 443、安全组规则或资源采购，未合并 PR。开始 DNS 变更和开放 443 前仍需指定变更确认。

WPS 与 20 条人工数据抽查仍待完成，总体验收仍为 PENDING。解释调用默认最多 20 次尝试/进程/日，重启会重置，不能作为供应商账单硬上限。用户选择继续使用已保存密钥；本记录和代码均不保存密钥。
