# 知选四页浅色界面与七维推荐发布记录

## 已发布结果

2026-10-27，现有公网站点 <https://zhixuanji.cn/> 已切换到本地实现提交 `ccd0413` 对应的发布包。当前 `/opt/zhixuan/current` 指向 `/opt/zhixuan/releases/alpine-ui-20261027-ccd0413`。GitHub 分支和 main 本次未推送、未合并；云端发布与 GitHub 集成分别记录。

- 四页按用户参考图重做：智能选机、手机库、我的对比、数据说明。保留浅蓝山水、白色半透明卡片、横向需求输入、主卡与右侧偏好、底部机型条等主要结构。
- 预算独立；存储、重量默认显示，电池、处理器、屏幕、拍照、游戏放在同一个“更多偏好（5 项）”折叠条。品牌仍是硬筛选。
- 保留七维五档偏好、正的未提及初值和百分制规格参考分。游戏采用处理器与电池容量代理；容量不包装为续航实测，规格代理不包装为真实性能认证。
- “为什么适合你”改为面向顾客的自然段，说明具体优点与一项取舍。技术分数和计算证据保留在展开说明，避免无依据的外观、帧率或使用承诺。
- 数据说明使用真实目录数量及来源记录类型分布，未使用示意图的虚构比例。

用户后续提供的 HTML/ZIP 经检查是 ChatGPT 会话网页存档与通用预览运行器，不包含独立 UI 应用源码或原始设计素材。未执行其中脚本，未将会话元数据或附件原文放入发布包；视觉实现依据用户的四张截图。

## 发布对象与校验

本次重新核对 DNS 指向 `47.86.39.2`，Workbench 连接同一现有实例 `i-j6c8jqxomk685dgmtuc2`，没有新增机器或修改 DNS、TLS、网络端口、系统服务配置。

| 对象 | 值 |
| --- | --- |
| 实现提交 | `ccd0413` |
| 发布包 | `local_evidence/zhixuan-alpine-20261027.zip`，7,679,489 bytes，221 个运行文件 |
| 发布包 SHA256 | `72cdb939c1c0103cf38459a45768afa86178ab549906cae8dbdeecc4ac580330` |
| 操作脚本 | `local_evidence/deploy-alpine-20261027-r2.py` |
| 脚本 SHA256 | `990a2fd147c129feff1423d29ba3535204da1bb1d727efd2e4154736bc5fe62c` |
| 上一发布 | `/opt/zhixuan/releases/consumer-ux-20261024-b30c796` |
| 当前发布 | `/opt/zhixuan/releases/alpine-ui-20261027-ccd0413` |
| 服务器验证记录 | `/root/zhixuan-alpine-20261027-backup/alpine-ui-20261027-ccd0413.json` |
| 数据库 SHA256 | `128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8`，未变 |
| 数据 manifest SHA256 | `d7d899e07c540deab0ff01ae19555765cd159809dd7c7bce4b3499b87b45ee22` |
| 规格参考规则 | `spec-reference-1.0.0-774e84a695b7` |

旧 Workbench 会话上传返回 `NoSuchKey`，服务器未落盘，未据此切换。重新建立同实例会话后，两个文件实际落盘且服务器 SHA256 与本地一致。先解压到新目录、逐文件校验、检查 SQLite 完整性与外键，再以现有 Python 环境在 `127.0.0.1:18671` 临时验证。通过后原子切换 current 并重启 zhixuan；zhixuan 与 nginx 均为 active，现有配置文件哈希保持一致。未安装软件、未输出或改写密钥、未调用付费模型。

## 验证证据

- Python 全量 773/773 通过；最终文案调整后相关 42/42 通过。日志：`local_evidence/alpine-full-final-tests-20261027.log`、`alpine-copy-final-tests-20261027.log`。
- Node 前端 45/45 通过：`local_evidence/alpine-node-final-20261027.log`。
- 四页与四张参考图成对比对；1586×992 和 390×844 本地浏览器验证，导航、搜索、对比、偏好折叠、确认失效与推荐流程正常，无 JS 错误。详见根目录 `design-qa.md` 和 `local_evidence/alpine-visual-comparison*.png`。
- 服务器隔离验证、切换后接口验证均通过；默认完整排名 40 配置，新机目录 51 配置，三格式导出一致。实际切换结果：`local_evidence/alpine-server-activation-20261027.json`。
- 公网 HTTPS 使用 Windows curl 传输，证书校验保持启用。首页、全部模块/CSS、当前 PNG 装饰资源及图标共 26 个资源的 SHA256 均与本地一致。公网 `/readyz` 与新 meta 正常；默认推荐 40 配置，预算最高 3000 元且存储至少 256GB 返回 20 配置；CSV/XLSX/JSON 排名和签名一致，机型图片可读取。证据：`local_evidence/alpine-public-acceptance-20261027.json`。

当前内置浏览器访问公网出现 `ERR_CONNECTION_CLOSED`，Python 默认 TLS 传输亦曾超时；因此不宣称已完成公网浏览器交互验收。系统 curl 的 HTTPS 公网接口与资源核验通过，截图是同一已部署文件的本地预览。未绕过证书检查或改变网络配置。

数据集通用验收状态仍为既有 `PENDING`，WPS 人工检查仍为 `NOT_CHECKED`；它们与此次机器发布检查通过分别记录。图片角度、真实配置、来源类别及新增顾客文案与参考示意图存在内容差异；没有伪造账号、收藏或价格走势能力。

## 回退

上一发布目录完整保留。若发现生产问题，在服务器检查当前指针后，可创建指向上一发布的临时符号链接，再用 `os.replace` 原子替换 `/opt/zhixuan/current`，重启 `zhixuan` 并核对 `/readyz`。本次操作脚本已内置切换失败时的同样回退逻辑，但实际切换成功，未触发回退。回退不需要改动数据库、配置、DNS 或证书。
