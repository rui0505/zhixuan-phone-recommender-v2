"""外部服务适配层。

每个子模块隔离一类外部服务的返回格式，向内核暴露稳定契约：

- `jev`：语义理解（需求 → 偏好分类）。工作文档第 4 章。
  内含 `MockClassifier`（确定性替身）与 `HttpClassifier`（调用 Jev 服务）。
- `llm`：外接通用大模型做需求抽取（OpenAI 兼容接口），与 `jev` 同一
  契约（`PreferenceClassifier`），可互换。用环境变量配置，见 `.env.example`。
- `recommend`：把引擎算好的机型写成一段推荐语。**唯一让大模型自由生成
  文字的地方**，因此约束最重（见该模块的 prompt）。
- `env`：`.env` 加载器。优先级「真实环境变量 > `.env` > `st.secrets`」，
  默认只填不覆盖。体检脚本 `tools/check_llm.py` 用它。
- `explainer`：推荐解释生成与回退。工作文档第 8 章（待实现）。

设计约定：内核只依赖本层暴露的 Protocol 与 dataclass，不依赖任何
具体供应商的字段名、错误码或重试语义。换供应商只改本层。
"""
