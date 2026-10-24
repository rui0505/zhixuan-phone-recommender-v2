# 知选 HTML 前端与 Python API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 恢复原 HTML 视觉风格，并通过独立 Python API 接入当前混合数据库和推荐核心，先交付可验收的本地版本。

**Architecture:** 原生 HTML/CSS/JavaScript 调用同域 `/api/v1/`，Starlette 托管页面与只读 API。两个网页入口共享纯发布校验、SKU 详情和现有 TOPSIS/证据/导出逻辑；Streamlit 保留为回退入口，不在浏览器重算成绩。

**Tech Stack:** Python 3.12、Starlette、Uvicorn、pandas/numpy/openpyxl、HTML/CSS/ES modules、unittest、httpx、Node 内置测试器。ASGI 依赖使用当前已安装并可验证的 `starlette==1.7.0`、`uvicorn==0.54.0`；实施时须重新通过干净环境验证，不声称现在已完成新 API 的兼容测试。

**Spec:** `docs/superpowers/specs/2026-10-06-html-api-ui-design.md`（用户已确认）。

## Global Constraints

- 独立分支 `codex/html-api-ui`；不推送/合并/修改部署主线，不新建远端仓库或托管项目；公网部署另行确认。
- 基线数据库 `catalog-2026-10-06-v2.1`、规则 `rules-2.3.0`、算法 `topsis-1.0.0`；147 机型、150 版本、484 SKU。
- SQLite SHA-256 必须保持 `128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8`；manifest SHA-256 保持 `d7d899e07c540deab0ff01ae19555765cd159809dd7c7bce4b3499b87b45ee22`，均不得修改。
- 保留整体 PENDING、机器 PASS、WPS NOT_CHECKED、人工抽查 0/20；图片公开数量仍为 0。
- 保留 new/used/unknown、历史价格日期、NULL 与原始 0；不读取新增两份 CSV，不写数据库或重建数据。
- 保留完整可比输入、至少两个不同机型、目标屏幕绝对偏差转换；不换算法、不补造分数、不让 LLM 排序。
- 七项权重仅允许 0–10 有限数且至少一项正值；目标屏幕 3–10 英寸；比较一至四个不同 SKU；请求体最大 32 KiB；列表默认 24 条、最多 100 条。
- 复用 `web/static/index.html` 的 `#394c46 / #f4f3ef / #fffefa`、导航、标题区与卡片；1440/900/390 像素验收，手机端整页不横向溢出。
- 原 HTML 的旧自然语言/问卷接口不接入首版；不新增账号、学习、VIKOR、真实 LLM 或图片发布。
- 数据文本不作为可信 HTML；只输出安全 HTTP/HTTPS 来源；不暴露私人路径、完整摘录、原件或秘密。
- 每个任务先验证失败测试，再最小实现、复测、局部提交；旧测试记录不能代替改版的新验收。

## Review Focus

1. 请求乱序与分页竞态：改条件后的旧响应、另一频道的迟到响应不能恢复过期成绩；Task 5、6 测试。
2. URL 查询与 JSON 解析差异：重复单值字段、空值、中文品牌、布尔值和未知键不得被默默忽略；Task 2、4 测试。
3. 校验后发生数据替换：同版本文字但内容变化、计算期间替换发布文件，必须拒绝混合或过期结果；Task 1、3、4 测试。
4. 数据里的恶意字符串：型号、来源、CSV 公式与换行链接不能执行脚本或泄露凭据；Task 3、4、6 测试。
5. 两名用户交错操作：一个人的条件、权重、对比或导出不能污染另一人的结果；Task 3、5、7 测试。

---

## 文件与职责图

| 文件 | 责任 |
| --- | --- |
| `catalog/release.py` | 纯哈希/发布校验、只读发布缓存；不导入 Streamlit |
| `catalog/read_views.py` | 准确 SKU 详情与安全来源链接；不含 UI |
| `ui/catalog_views.py` | 保留原调用接口，委托共享纯函数，继续渲染 Streamlit |
| `api/__init__.py`、`api/contracts.py` | API 包、严格输入类型、字段白名单、查询解析、统一错误 |
| `api/service.py` | 目录/详情/比较/推荐/导出服务、公开响应投影 |
| `api/app.py` | 应用工厂、HTTP 路由、体积上限、错误处理、同域静态托管 |
| `frontend/index.html`、`frontend/styles.css` | 原 HTML 风格页面、需求表单、响应式布局 |
| `frontend/state.mjs`、`frontend/api.mjs` | 可独立测试的状态转换、过期请求控制、HTTP 客户端 |
| `frontend/render.mjs`、`frontend/app.mjs` | 安全 DOM 渲染、表单/导航/卡片/对比/导出绑定 |
| `requirements-api.txt` | 不依赖 Streamlit 安装结果的五项后端直接依赖 |
| `tests/api_fixture.py` 与新增测试 | 合成只读发布、请求样例、纯层/API/真实 HTTP 回归 |
| `tests/frontend/*.test.mjs` | 无浏览器依赖的状态和网络竞态测试 |
| `.github/workflows/tests.yml`、`README.md`、`docs/html-api-local.md` | 本地与 CI 验证、启动、分工、交付限制 |

`engine.py`、数据库 schema、现有导出函数及派生规则不改。新增 Python 测试沿用项目的 unittest；不另建 pytest 或前端框架。

## 执行环境与检查约定

仅在计划和执行方式得到确认后开始。重用当前 linked worktree，不创建其他项目；先记录 Git 状态和两个发布文件哈希。实验日志/虚拟环境放入已忽略的 `local_evidence/html-api/`。

开发环境建立在 Task 1 内：`py -3.12 -m venv local_evidence/html-api/dev-env`，安装原 `requirements-dev.txt`。以下 PowerShell 命令约定 `$apiPython = '.\local_evidence\html-api\dev-env\Scripts\python.exe'`；Linux 换成该目录下 `bin/python`。命令的预期结果是实施验收条件，不是已运行的声明。

单任务提交必须只暂存其明确文件，提交前 `git diff --check`；不得 `git add .` 纳入私人构建资料。最终审查/外部推送不包含在任务提交授权内。

### Task 1: 提取可供两个入口复用的只读发布与详情逻辑

**Files:** Create `catalog/release.py`、`catalog/read_views.py`、`tests/api_fixture.py`、`tests/test_catalog_read_service.py`；Modify `ui/catalog_views.py` 中 `file_sha / load_release / _load_verified / safe_url / safe_urls / sku_details`。

**Interfaces:**

- Produces `VerifiedRelease(repo: CatalogRepository, manifest: dict, key: dict[str, str])`；key 固定含 `dataset_version`、`feature_rule_version`、`snapshot_sha256`、`manifest_sha256`。
- Produces `file_sha(path: str | Path) -> str`、`verify_release(db_path: Path, manifest_sha: str, snapshot_sha: str) -> VerifiedRelease`、`ReleaseStore(db_path: Path).get() -> VerifiedRelease`。
- Produces `sku_details(repo: CatalogRepository, sku_id: str) -> dict | None`、`sku_sources(repo: CatalogRepository, detail: dict) -> list[dict]`、`safe_url(value: object) -> str | None`、`safe_urls(value: object) -> list[str]`。
- Test helper `make_release(root: Path, *, prepare: Callable[[sqlite3.Connection], None] | None = None) -> Path` 用既有 `catalog_fixture.create_catalog` 与 `scripts.export_snapshot.export_snapshot` 创建临时发布，关闭写连接；不复制或修改正式源文件。为合成 S1/S2 增加 SKU 级 `refresh_rate_hz=90/120`，使 Task 2 的默认请求可评分；prepare 仅在临时源连接上、导出前增加测试场景，不能绕过发布校验。

- [ ] **Step 1:** 按执行环境约定建立开发环境，安装原开发依赖，确认 Python 3.12 与旧入口可导入。
- [ ] **Step 2:** 在新测试中定义失败断言：独立子进程导入纯模块后没有 `streamlit`；`get()` 的双哈希与文件相符；同哈希复用资源、数据库或 manifest 变化重新验证；匹配哈希但坏 SQLite/外键/版本仍拒绝；模拟验证过程中换文件也拒绝。SKU 测试添加另一容量与卫星版，断言详情只保留正确作用域/价格，来源关联无遗漏且换行 URL 逐条校验。首个命名红测试如下，其余测试分别命名为 `test_import_without_streamlit / test_changed_release_revalidated / test_invalid_release_rejected / test_sku_scope_and_sources`：

```python
def test_safe_source_urls(self):
    self.assertIsNone(safe_url('https://user:pass@example.test/'))
    self.assertEqual(safe_urls('https://example.test/a\njavascript:alert(1)'),
                     ['https://example.test/a'])
```

- [ ] **Step 3:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_catalog_read_service.py' -v`，预期因新模块/接口缺失而失败，而不是环境、夹具或导入路径错误。
- [ ] **Step 4:** 按上述签名实现纯函数与发布缓存：每次 `get()` 检查两个实际哈希，昂贵校验仅在匹配的发布 key 上复用；验证前后复查文件哈希，异常不缓存。Streamlit 保留 `load_release(db_path, manifest_sha) -> (repo, manifest)` 兼容接口，缓存包装仍属于 UI。
- [ ] **Step 5:** 运行新测试以及 `& $apiPython -m unittest discover -s tests -p 'test_app_catalog.py' -v`、`& $apiPython -m unittest discover -s tests -p 'test_review_regressions.py' -v`；均退出 0，既有页面无异常。
- [ ] **Step 6:** 仅提交本任务五个文件，消息 `refactor: share verified catalog reads between UI entrypoints`。

### Task 2: 固化严格请求、分页与错误契约

**Files:** Create `api/__init__.py`、`api/contracts.py`、`tests/test_api_contracts.py`；Extend `tests/api_fixture.py`。

**Interfaces:**

- Consumes Task 1 的 `VerifiedRelease.key` 与品牌目录；沿用 `catalog.adapter.resolve_brands` 的已审核别名。
- Produces `ApiError(status: int, code: str, message: str)`、`RecommendationRequest(needs: dict, weights: dict[str, float], target_screen: float | None, mode: str, confirmed: bool, expected_release: dict[str, str])`、`Pagination(page: int, page_size: int)`、`CatalogQuery(needs: dict, mode: str, q: str, pagination: Pagination)`。
- Produces `parse_recommendation(body: dict, known_brands: list[str]) -> RecommendationRequest`、`parse_pagination(items: list[tuple[str, str]]) -> Pagination`、`parse_catalog_query(items: list[tuple[str, str]], known_brands: list[str]) -> CatalogQuery`、`parse_compare(body: dict) -> list[str]`、`parse_export(body: dict, known_brands: list[str]) -> tuple[RecommendationRequest, dict[str, str]]`。
- Test helper `request_payload(release: VerifiedRelease, **updates) -> dict` 返回独立深拷贝的默认请求：预算/最低存储 null、品牌列表空、new、target 6.5、confirmed true；权重 price 5、weight/storage 2、refresh 1、其余 0（使用规范键）。

- [ ] **Step 1:** 写失败测试，固定权重上下界 `0/10`、屏幕 `3/10`、分页 `1/24/100` 和四个对比上限；遍历负数、NaN/Infinity、布尔值、字符串数值、全零、未知键、未知品牌、重复 ID、冲突品牌和缺少 screen 目标，断言明确 ApiError。查询只允许 mode/min_price/max_price/min_storage/include/exclude/q/page/page_size；include/exclude 可重复，单值字段重复或空数值拒绝，中文与字面 `%` 搜索不能变成 SQL/通配指令。

```python
def test_pagination_exact_bounds(self):
    self.assertEqual(parse_pagination([]), Pagination(page=1, page_size=24))
    self.assertEqual(parse_pagination([('page_size', '100')]).page_size, 100)
    with self.assertRaises(ApiError):
        parse_pagination([('page_size', '101')])
    with self.assertRaises(ApiError):
        parse_pagination([('page', '1'), ('page', '2')])
```

- [ ] **Step 2:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_api_contracts.py' -v`，确认缺失新接口导致失败。
- [ ] **Step 3:** 实现以上类型及解析器。JSON 权重只接受七项规范键；查询数字经过完整数值解析与有限性检查；品牌先解析到目录规范名再判冲突。推荐体严格为 `needs / weights / target_screen / mode / confirmed / expected_release` 六个顶层字段；导出体严格为 `request` 和 `fingerprints`，后者只含 decision_sha256/ranking_sha256。比较体仅 `sku_ids`。
- [ ] **Step 4:** 重跑新契约测试及 `test_catalog_read_service.py`，预期全部退出 0；错误信息不得包含原请求秘密或服务器路径。
- [ ] **Step 5:** 仅提交本任务四个文件，消息 `feat: define strict catalog API request contracts`。

### Task 3: 目录、详情、比较、推荐与公开响应服务

**Files:** Create `api/service.py`、`tests/test_api_service.py`；Extend `tests/api_fixture.py`。

**Interfaces:**

- Consumes Task 1 发布和详情、Task 2 请求类型；调用现有 `build_input / rank_decision_input / build_evidence / render_template / export_results`。
- Produces `RecommendationOutcome(di: dict, result: object, bundle: dict | None, release_key: dict[str, str])`。
- Produces `CatalogService(store: ReleaseStore)` 的 `meta() -> dict`、`catalog(query: CatalogQuery) -> dict`、`details(sku_id: str) -> dict`、`compare(sku_ids: list[str]) -> dict`、`recommend(request: RecommendationRequest) -> RecommendationOutcome`、`recommendation_payload(outcome: RecommendationOutcome, pagination: Pagination) -> dict`、`export(request: RecommendationRequest, fingerprints: dict[str, str], format: str) -> bytes`。
- meta 固定输出 `release / counts / brands / metrics / defaults / acceptance / features`；catalog 输出 `items / total / page / page_size / release`；比较输出 `items / release`。详情仅投影 sku/model、作用域规格、该 SKU 价格、评测记录/条件、来源；模型级记录明确标记“可见不等于已参与排名”，不推断适用性。

- [ ] **Step 1:** 写失败测试：同 fixture 的共享核心与服务候选、全量排名/备选/未评分 ID、分数及两个指纹相同；第一页大小 1 不改变全量排名指纹。不同预算交错计算后，第一个结果仍可独立核验；输入 dict 也不被后一次调用修改。生产库对照用当前发布和同一默认请求，不硬编码旧成绩。
- [ ] **Step 2:** 补充断言：零候选、单机型多个容量、游戏条件不足不能出成绩；权重与目标来源真实；未知 SKU 404；没有图片/私有路径/raw_rows/完整摘录；恶意名称仍作为数据字符串而非 HTML；safe source 不输出凭据。计算中模拟发布替换，确认最终结果被拒绝。

测试类 setUp 使用 make_release 创建临时发布，设置 `self.service = CatalogService(ReleaseStore(self.db))`；`self.request` 为 parse_recommendation(request_payload(...)) 的规范请求。首个精确断言（完整核心一致性另命名 `test_same_input_matches_core`）：

```python
def test_pagination_never_changes_ranking_hash(self):
    outcome = self.service.recommend(self.request)
    a = self.service.recommendation_payload(outcome, Pagination(1, 1))
    b = self.service.recommendation_payload(outcome, Pagination(2, 1))
    self.assertEqual(a['ranking_sha256'], b['ranking_sha256'])
    self.assertEqual(a['ranked_total'], b['ranked_total'])
    self.assertNotEqual(a['ranked'][0]['sku_id'], b['ranked'][0]['sku_id'])
```

- [ ] **Step 3:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_api_service.py' -v`，确认新服务缺失的失败。
- [ ] **Step 4:** 实现服务：发布 key 与 expected_release 比较；全量候选先排序/建证据，再截取展示页。推荐响应使用 spec 的稳定字段，并加 `ranked_total/page/page_size/decision_sha256/ranking_sha256/release/explanations`，非 ready 排名为空、bundle null，保留未评分条目的缺失原因/来源。explanations 为本页 sku_id 到文字的映射：用既有 `build_evidence(..., sku_ids=[sku_id])` 构建绑定同次决策的子证据再 render_template，不截断全量解释文本来冒充单卡片解释。使用 `CatalogRepository.as_records` 和字段白名单投影，不直接暴露整个 DB 行或 manifest。JSON 禁止非有限数；字段为真实 NULL 时保留 null。调用结束再次核对当前发布 key，变化为 409，不发回计算中的混合结果。
- [ ] **Step 5:** 实现导出服务：只接受 ready，重新计算完整结果并核验指纹，调用现有导出函数；未知格式 422、状态/指纹/发布冲突 409。目录按已选价格再过滤、字面搜索、稳定 sku_id 排序后分页，不按搜索命中片段排名。
- [ ] **Step 6:** 重跑本任务与 Task 1–2 测试，另跑 `test_engine_decision.py / test_evidence.py / test_exports.py`；全部退出 0，两个发布文件哈希与基线一致。
- [ ] **Step 7:** 仅提交本任务三个文件，消息 `feat: expose canonical catalog recommendation service`。

### Task 4: 可独立启动的 ASGI 接口、体积限制与下载

**Files:** Create `api/app.py`、`requirements-api.txt`、`tests/test_api_http.py`；Modify `requirements-dev.txt`。

**Interfaces:**

- Consumes Task 2 解析器、Task 3 服务。
- Produces `create_app(db_path: Path | None = None, frontend_dir: Path | None = None) -> Starlette` 与模块 `app`；可信服务器配置仅从 PHONE_CATALOG_PATH 读库路径，HTTP 不可指定。默认静态根 `frontend/`，没有构建文件时 API 仍可独立测试，不能回退到旧 HTML。
- 全部路径（含两项健康检查）按 spec。推荐的 `page/page_size` 放 URL 查询，不改变请求体或决策指纹。解析不合法 JSON/UTF-8 为 400，契约违例 422、缺 SKU 404、冲突 409、体积超限 413、发布无效 503；其他内部错误为安全 500。
- 错误体固定 `{"error":{"code":"...","message":"..."}}`；code 对应 `invalid_json / invalid_request / not_found / release_changed / decision_changed / not_ready / body_too_large / release_unavailable / internal_error`，message 为安全可展示文字；前端以 code 判定重新确认或重试，不能依靠中文字符串匹配。

- [ ] **Step 1:** 用 `httpx` 已有开发依赖和 Starlette TestClient 写失败端到端测试。fixture 提供临时发布及静态 index；逐个测试 meta/catalog/details/compare/recommend/export/healthz/readyz，确认规范响应头、中文 JSON、默认分页与四 SKU 上限。比较 Service 和 HTTP 的指纹一致。
- [ ] **Step 2:** 新增 body 边界 `32768/32769` bytes 测试（首个允许进入 JSON 校验、后者 413）；缺少或伪造 Content-Length、流式请求同样累计限制，不能只信声明长度。重复查询、无效 UTF-8、true 数值、NaN、未知路径和安全错误均覆盖；坏库 healthz 可存活、readyz 和数据接口 503，不暴露绝对路径。

HTTP 测试 setUp 创建 self.db，并在 TestClient 上下文中设置 self.client；下面一项红测试需配合 `test_streamed_body_limit / test_invalid_release_health / test_export_matches_full_result / test_real_http_roundtrip`：

```python
def test_request_body_limit(self):
    accepted_for_validation = self.client.post('/api/v1/recommend',
        content=b' ' * 32768, headers={'Content-Type': 'application/json'})
    oversized = self.client.post('/api/v1/recommend',
        content=b' ' * 32769, headers={'Content-Type': 'application/json'})
    self.assertEqual(accepted_for_validation.status_code, 400)
    self.assertEqual(oversized.status_code, 413)
```

- [ ] **Step 3:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_api_http.py' -v`，验证缺新应用入口而失败。
- [ ] **Step 4:** 实现工厂、路由和 ASGI 接收大小限制，阻塞读库/计算通过线程池运行；不共享用户偏好，不开启任意 CORS。启动发布校验失败时保持应用可导入，healthz 可响应、readyz/数据接口 503；发布文件修复后重新校验，不永远缓存失败。只托管指定静态目录、无目录浏览，路径穿越不得读取 data 或 .env。响应 JSON 使用 allow_nan=False，下载 MIME/Content-Disposition 由固定格式产生，不拼入用户文件名。
- [ ] **Step 5:** `requirements-api.txt` 明确写入 `starlette==1.7.0`、`uvicorn==0.54.0`、`pandas==2.3.3`、`numpy==2.5.3`、`openpyxl==3.1.5`；开发依赖追加 `-r requirements-api.txt`，不改 Streamlit requirements。安装新 API 依赖后重跑 HTTP/契约/服务测试。
- [ ] **Step 6:** 测试导出的 BOM、中文 CSV、XLSX 文本 ID/空单元格、JSON null 和公式型文本转义，独立核对完整排名及证据；篡改导出指纹必须 409。按 `tests/test_web_server.py` 的真 Uvicorn+urllib 模式，再验证真实本地 TCP 的推荐与下载，不调用付费模型。
- [ ] **Step 7:** 仅提交本任务四个文件，消息 `feat: serve read-only catalog API and verified exports`。

### Task 5: 可测试的前端状态与网络层

**Files:** Create `frontend/state.mjs`、`frontend/api.mjs`、`tests/frontend/state.test.mjs`、`tests/frontend/api.test.mjs`。

**Interfaces:**

- Produces `createState()`、`setPreferences(state, requestFields)`、`applyMeta(state, meta)`、`beginRequest(state, channel) -> token`、`acceptResponse(state, channel, token, value) -> boolean`、`addComparison(state, skuId) -> boolean`、`removeComparison(state, skuId)`。
- state 明确包含 preference revision、每频道序号、当前 release、confirmedRequest、结果、comparisonIds、loading/errors；至少区分 recommend/catalog/detail/compare 频道，无个人数据持久化。
- Produces `fetchJson(path, {method='GET', body, signal}={}) -> Promise<object>`、`fetchDownload(format, body, {signal}={}) -> Promise<Blob>`、`ApiClientError(code, message, status)`；路径固定同域 API，不接受任意远程地址。

- [ ] **Step 1:** 写 Node 内置 assert 测试，断言 `setPreferences` 清除结果/确认并升级 revision；旧 token 的 `acceptResponse` 为 false，频道间不互相覆盖。发布 key 相同的 meta 不清结果，key 改变则清确认/结果/对比；比较重复不加入，第五个加入失败。
- [ ] **Step 2:** 写网络失败测试，模拟 fetch 乱序、AbortError、非 JSON 错误页和服务器 409/503；错误显示安全固定/服务器契约消息，不把 HTML 错误页塞进 DOM。各用户用独立 state 实例。

```javascript
test('old recommendation cannot overwrite changed preferences', () => {
  const state = createState();
  const oldToken = beginRequest(state, 'recommend');
  setPreferences(state, {mode: 'used'});
  assert.equal(acceptResponse(state, 'recommend', oldToken, {status: 'ready'}), false);
});
```

- [ ] **Step 3:** 运行 `node --test tests/frontend/state.test.mjs tests/frontend/api.test.mjs`，因缺模块失败；若 node 命令不可用，使用已配置的运行时绝对解释器路径，不临时安装另一框架。
- [ ] **Step 4:** 实现以上函数；网络层向每次 fetch 传 signal；输入变动主动 abort，状态层序号仍负责防御不能中断的迟到响应。翻页不是偏好变化，携带相同 confirmedRequest；用频道 token/revision 丢弃过期页。导出只发送确认请求与服务器指纹，不发客户端成绩。
- [ ] **Step 5:** 重跑两个 Node 文件，全部通过；只提交本任务四个文件，消息 `feat: add isolated frontend state and API transport`。

### Task 6: 原 HTML 风格界面与全部真实交互

**Files:** Create `frontend/index.html`、`frontend/styles.css`、`frontend/render.mjs`、`frontend/app.mjs`、`tests/test_frontend_contract.py`；Modify Task 4 静态入口配置（仅必要部分）。

**Interfaces:**

- Consumes Task 5 状态与网络 API，Task 3–4 响应字段。
- Produces `renderCard(record: object, context: object) -> HTMLElement`、`renderSources(sources: object[]) -> HTMLElement`、`renderDecision(state: object, meta: object) -> HTMLElement`、`renderComparison(items: object[]) -> HTMLElement`；用 DOM textContent/属性设置，不信任数据 HTML。
- `app.mjs` 为页面入口，加载 meta，绑定真实表单、导航、目录搜索/分页、推荐翻页、详情、比较、清空和三类下载。导出按钮只在 ready 且当前条件仍与 confirmedRequest 相符时可用。

- [ ] **Step 1:** 写失败契约测试检查页面有明确 form/label、三导航、确认、全部七项控件、details/compare/download 容器、module 入口和真实 API 路径，无旧 `/api/quiz`、聊天占位和外部 CDN 必需依赖；检查 CSS 的三个基准颜色和小屏断点。断言 `[hidden]` 不被 grid/flex 覆盖。

```python
def test_source_visual_tokens_are_preserved(self):
    css = (ROOT / 'frontend/styles.css').read_text(encoding='utf-8')
    for value in ('#394c46', '#f4f3ef', '#fffefa'):
        self.assertIn(value, css)
    self.assertIn('[hidden]', css)
```

- [ ] **Step 2:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_frontend_contract.py' -v`，确认新页面缺失导致失败。
- [ ] **Step 3:** 参考原 HTML 提取样式与布局，构建实际功能而非复制旧 JS。hero 保留“每一种生活都有适合的手机”的文字与通用装饰；产品区域明确是通用占位，不冒充实物。需求字段读取 meta defaults；拍照/游戏说明、验收状态、历史价格、新机/二手/未知和可比不足必须可见。
- [ ] **Step 4:** 实现上述 DOM 函数与 app 绑定：推荐分数显示原始相对贴近度的展示格式、不改原值；每卡片解释取 bound evidence，不生成额外能力分。目录与推荐翻页分别走各自频道；来源展开与对比按准确 SKU 获取。loading 用 aria-busy/status，错误用可聚焦提示，键盘可完整操作；下载 Blob 完成后释放临时 URL。
- [ ] **Step 5:** 通过现有浏览器控制工具实际启动并检查本地页面：桌面/平板/手机、搜索中文、确认→推荐→来源→比较→修改权重→旧结果清空→再确认→导出。检查乱序网络时旧结果不会回来，并用临时合成服务展示含 `<script>` 名称而不执行。不得改正式库来制造浏览器测试数据。
- [ ] **Step 6:** 重跑新 Python/Node 测试及 API HTTP 测试；真实页面无可见异常或整页横向溢出。保存可读取验证记录；截图只使用工具确实提供的产物，不捏造路径或以静态测试替代视觉验收。
- [ ] **Step 7:** 仅提交本任务文件，消息 `feat: restore original phone selection interface on catalog API`。

### Task 7: 干净环境、跨入口回归与队友启动交付

**Files:** Create `tests/test_api_deployment.py`、`docs/html-api-local.md`；Modify `.github/workflows/tests.yml`、`README.md`。本地完整日志仅放 `local_evidence/html-api/`，公开文档不带私人路径和源资料。

**Interfaces:** Consumes完整 Task 1–6；正式启动为 `python -m uvicorn api.app:app --host 127.0.0.1 --port 8600`。提供 Windows/Linux 等价命令；两种 UI 入口明确区别，启动新版不替换 Streamlit。

- [ ] **Step 1:** 写新失败部署测试：API 的独立子进程不导入 streamlit/web.server/data_pipeline/docx、不需要旧 phones.xlsx、原件或密钥；测试 API 与 Streamlit 的共享输入/排名/权重/目标/证据指纹一致；数据文件哈希不变。校验 API 三项核心依赖 pins 与现有 requirements 相同，避免两入口以后依赖漂移。

新增测试名 `test_api_only_import / test_two_entrypoints_share_result / test_runtime_pins_match / test_delivery_docs_and_ci / test_release_unchanged`；最后一项至少包含如下断言：

```python
def test_release_unchanged(self):
    self.assertEqual(file_sha(ROOT / 'data/catalog.sqlite'),
        '128207745f5450c285cb8d6c61ff2c692f19387f2df0823690cd5b323c453bc8')
    self.assertEqual(file_sha(ROOT / 'data/release_manifest.json'),
        'd7d899e07c540deab0ff01ae19555765cd159809dd7c7bce4b3499b87b45ee22')
```

- [ ] **Step 2:** 运行 `& $apiPython -m unittest discover -s tests -p 'test_api_deployment.py' -v`；新验收门槛/必要交付文档尚不具备时应失败，不能造一个恒过测试。
- [ ] **Step 3:** 完成启动和分工文档，明确接受 GitHub 邀请→自己克隆→功能分支→本地验证→PR；一名队友可维护 frontend，另一名可维护 API/tests，数据更新另走发布流程。CI 加 Node 测试和 API-only 干净虚拟环境导入/真实 HTTP 冒烟，不新增生产托管动作。不得对本地未验证的网站声称公网已更新。
- [ ] **Step 4:** 全量回归：`& $apiPython scripts/check_release.py --root .`；`& $apiPython -m unittest discover -s tests -p 'test_*.py' -v`；设置 `$env:PHONE_CATALOG_DB='data/catalog.sqlite'` 后 `& $apiPython -m unittest discover -s data_pipeline/tests -p 'test_*.py' -v`；Node 两个测试文件。全部退出 0，记录实际测试数，不沿用旧 576/37 作为本轮结果。
- [ ] **Step 5:** 在 `local_evidence/html-api/api-only-env` 新建 Python 3.12 环境，只安装 requirements-api.txt；启动本地 8600，实际完成 meta/catalog/recommend/export HTTP 请求，确认无 Streamlit 依赖。端口若占用先确认进程，不停止其他人的服务。
- [ ] **Step 6:** 两个独立浏览器会话用不同条件同时推荐，核对各自源请求与下载；重复检查 1440/900/390 布局，独立解析下载的三格式文件并复算全量结果。无法完成的项目标为 NOT_CHECKED；存在未通过的关键验收不得称完成。核对 git 只含目标代码和文档、两个发布哈希不变。
- [ ] **Step 7:** 提交本任务四个文件，消息 `test: verify HTML API delivery and document team setup`。执行方式所要求的整分支独立审查在此后进行；修正有效问题并重跑相关及全量测试，向用户给出真实本地入口、结果与未完成事项，不自行推送或上线。

## 设计覆盖与计划自检

- Spec 1–4：Task 1、4、5、6（原风格、独立入口、纯共享模块、实际功能）。
- Spec 5：Task 2–4（字段/状态/全部端点、分页/请求体/下载）。
- Spec 6–7：Task 1–5（不改数值与规则、SKU 作用域、缺失/可比性、发布/证据/请求绑定、安全）。
- Spec 8：Task 1–7（各层测试、真实 HTTP/浏览器、全量回归、干净 API 环境、双会话）。
- Spec 9：Task 7（协作与启动交付）；公网托管、远端推送与正式切换明确排除，保留旧站回退。
- 所有后续调用的函数、字段和类型均由上游 Interfaces 定义；五项 Review Focus 均有指定测试任务。任务提交不等于公网部署。

## 执行方式交接

计划待用户审阅与选择执行方式。建议 Native：由当前助手逐任务实现，最后由独立审查者检查整分支；这些任务强依赖共享接口，集中实现有利于保持一致。也可选择 Subagent-driven：逐任务交给新代理实现、每任务独立审查后推进，审查更细但上下文和协调成本更高。

获得计划与方式确认前，不开始产品代码、安装新 API 依赖、启动新版服务、推送或部署。
