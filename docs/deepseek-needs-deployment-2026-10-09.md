# DeepSeek 自然语言需求入口

2026-10-09 已发布至 https://zhixuanji.cn/，新香港服务器 47.86.39.2。
当前发布 `/opt/zhixuan/releases/deepseek-needs-20261009-r2`；回退版本
`/opt/zhixuan/releases/hk-migration-20261008`。旧版本、原数据库和数据发布指纹保留。

## 接口与确认

`POST /api/v1/needs` 接受 `{"text":"3000元以内，至少256GB，轻一点，不要苹果"}`。
只接受 1—2000 字文本；拒绝额外字段、查询参数和客户端密钥。
响应包含原抽取契约 `extraction`、可编辑 `draft`、说明 `notice`。
草稿 `confirmed=false`；返回草稿不计算排名。
模型输出五个偏好等级，程序使用 `schemas.LEVEL_MAP` 转换权重。
未提及的基础偏好默认 1；没有目标尺寸时尺寸权重为 0；照片、游戏不自动计权。
`2000元左右` 保留 `budget.reference=2000`，不自动生成预算上下限。
页面要求填写预算上限或明确勾选不限；重新修改字段清除确认和旧结果。
含糊预算的模型边界还须原文证据核验：禁止推断不同数值、忽略“左右”尾词、
把存储单位当预算。复合中文数词无法精确核验时清空边界并要求手动确认。
品牌通过目录名称和别名核验；无法匹配时保留抽取提示并回退手动填写，不放宽条件。

## 服务配置

沿用 `/etc/zhixuan.env` 的服务端凭据，不公开其内容。
自然语言入口必须同时满足 `PHONE_ENABLE_LLM=true`、
`PHONE_ENABLE_NEEDS_EXTRACTION=true`、已有 `LLM_API_KEY` 和可用额度状态路径。
模型配置 `LLM_MODEL=deepseek-flash`，固定官方 HTTPS 地址；18 秒超时，无自动重试，单并发。
部署模板默认不启用自然语言功能；正式服务器已增加 `needs.conf` drop-in：

```ini
[Service]
StateDirectory=zhixuan
StateDirectoryMode=0700
Environment=PHONE_ENABLE_NEEDS_EXTRACTION=true
Environment=PHONE_NEEDS_QUOTA_PATH=/var/lib/zhixuan/needs-quota.sqlite
```

系统账号仅可写自己的状态目录，程序和目录数据仍只读。
需求抽取每 UTC 日最多 20 次尝试，调用前先写入 SQLite；失败也计数。
额度跨重启、发布切换保留；数据库不可写时拒绝付费调用。
Nginx 对新接口单独设置每 IP 每分钟 1 请求，burst=2；超限 429 后仍可手动推荐。
已有推荐解释接口保留原来独立的每进程每天 20 次计数；此部署没有改变该接口。
这些是请求限制，不是服务商账单硬上限。

## 验证记录

- 最终完整 Python 回归 650 项通过（108.573 秒）；前端 Node 测试 15 项通过。
- 源值防护修正后 76 项相关抽取、接口与打包测试通过；正式服务器 Starlette 1.7.0 导入成功。
- 真实模型返回 `provider=deepseek`、`model=deepseek-flash`、`status=ok`。
  已核验 3000 元上限、256GB 最低存储、Apple 排除、未自动确认。
- 正式页面的含糊预算保留为参考；未澄清时即使勾选总确认也无法推荐。
  手动填写 3000 元上限并再次确认后生成 20 个可比配置的 TOPSIS 推荐。
- 服务重启前需求次数 2，重启后仍为 2；`readyz` 正常，zhixuan/nginx active。
- 窄屏及 1280px 桌面预览均无水平溢出。临时桌面尺寸随后恢复。

总体数据验收仍为 PENDING，WPS 人工检查为 NOT_CHECKED；未宣称数据全面验收通过。
真实模型调用只使用合成购机需求，没有发送用户私密信息。
旧 Streamlit 抽取入口与网页入口的功能开关不同，本文仅描述新版网页。
