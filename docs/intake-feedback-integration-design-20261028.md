# 新机型录入、审核、发布与评论审核整合

项目为 rui0505/zhixuan-phone-recommender-v2，基线262665d。用户授权整合朋友交接包、已有API和评论审核，并上传GitHub、更新云端。交接包只写JSON队列；不执行其覆盖脚本，保留现有AISM、独立手机库、评论和旧接口。复用录入结构与表单，修复并发队列、草稿编辑、静默截断、严格校验与HTTPS管理等边界。

管理员使用同一维护密钥，管理入口包含新增机型与评论审核。录入采用SQLite持久队列，状态draft/submitted/rejected/published；支持编辑、提交、来源确认、发布和导出。发布从当前经过验证的目录建立新不可变SQLite快照，只追加机型、版本、配置、价格和有来源的规格，不改已有业务值；生成新版本/哈希/清单，验证后原子更新活动指针。活动目录位于反馈数据库旁catalog目录，代码发布后仍保留；原data/catalog.sqlite不改。图片与买家评价随新快照重新绑定指纹，已有图片可用，新机型无图则显示占位。接口自动读取活动版本，旧确认推荐失效，前端通过广播/恢复焦点重新读取元数据与全库。

输入沿用 model{brand,display_name,release_month,source_url}, variant{variant_name,region,satellite_support,is_default}, skus[{ram_gb,storage_gb,memory_type,price{amount,price_kind,condition,observed_at,source_url,source_note}}], specifications[{key,value_num,unit,raw_value}], source_note；model.source_url为新增规格出处，草稿可空，发布前带规格时必须提供。价格完整性沿用交接包，未知数值保留null，不从博主名次推断客观参数。

管理接口（Bearer密钥，与反馈一致；可选兼容显式PHONE_ADMIN_TOKEN的X-Intake-Token；HTTP只允许本机）：GET /api/v1/intake/queue?page=1&page_size=20 返回{records,total,page,page_size}；GET /intake/{id} 返回{record}；POST /intake/draft 或 /submit 接收payload，返回{ok:true,record,warnings}；PUT /intake/{id} 接收{payload,status:draft|submitted,expected_revision}；POST /intake/{id}/reject 接收{expected_revision}；POST /intake/{id}/publish 接收{expected_revision,expected_release,sources_verified:true}，返回{ok:true,record,release}；GET /intake/export返回{records}的完整JSON。record字段包含record_id,status,revision,created_at,updated_at,payload,warnings；并发修改返回409；published不再编辑，重复发布不追加。管理页读取/meta取得expected_release。

公开页面保持原布局，在数据说明/反馈下方提供管理入口；/admin.html录入与队列，/feedback-admin.html原评论审核可互相跳转，均不公开队列。新资料缺少评测时不生成实测成绩或主观适用性结论。Excel是博主推荐及视频来源资料，具体是否导入候选待用户选择，不能直接填成已核实规格。

验证：合成目录上的真实API录入→编辑→提交→发布→搜索/详情/推荐/对比/导出，旧哈希保持、版本冲突、重启持久、发布失败不切换、重复/并发不增量、已有图片/评价不失效、反馈审核不受影响；再验证本地界面与云端隔离环境。真实生产只发布代码功能，不写测试机型或测试评论。
