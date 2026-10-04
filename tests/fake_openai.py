"""本地假 OpenAI 服务，供离线测试与示例共用。

让 `LLMClassifier` / `LLMWriter` 走**真实 HTTP**，但对面是本地进程。
用途不是测模型准不准，而是验证线格式：真实 socket → 真实 JSON 编解码 →
normalise_response → 契约结构。这样没有密钥也能跑通整条链路。

服务按 system prompt 区分两类请求：

- **需求抽取**（prompt 里含「结构化抽取器」）→ 委托 `MockClassifier`，
  返回结构化 JSON。
- **推荐语生成**（prompt 里含「解说员」）→ 从事实清单里抠出真实机型名，
  返回一段**只提这些机型**的散文。这样测试能断言「推荐语没有编造型号」。

这个模块不匹配 `test*.py`，不会被 unittest discover 收集。
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from providers.jev import MockClassifier

EXTRACT_MARKER = "结构化抽取器"
RECOMMEND_MARKER = "解说员"

# 事实清单里的机型行：「1. 机型名: iPhone 15」
_MODEL_LINE = re.compile(r"^\s*\d+\.\s*机型名:\s*(.+?)\s*$", re.MULTILINE)


def fake_recommendation(user_content: str) -> str:
    """从事实清单里抠出机型名，写一段只提这些机型的「推荐语」。

    刻意不引入任何清单外的信息——这正是真实 prompt 要求的。
    """
    names = _MODEL_LINE.findall(user_content)
    if not names:
        return "本次没有筛出符合条件的机型，建议放宽预算或存储要求。"
    listed = "、".join(names)
    return (
        f"按你的条件筛下来，最贴合的是 {listed} 这几款。"
        "它们的重量和屏幕尺寸都落在你说的范围内，价格也都在预算之内。"
        "注意匹配度只是本次候选集内的相对得分，换个条件名次会变，"
        "不代表哪款绝对更好。"
    )


class FakeOpenAIServer:
    """OpenAI 兼容的最小假服务。

    用法：

        with FakeOpenAIServer() as server:
            # 环境变量已指向本服务，直接构造 LLMClassifier 即可
            ...

    环境变量只在 `__enter__` 时写入，且不覆盖已有的真实配置。
    """

    def __init__(self, *, model: str = "fake-local-model", status: int = 200) -> None:
        self.model = model
        self.status = status
        self.requests: list[dict] = []
        classifier = MockClassifier()

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                messages = body.get("messages") or []
                system = messages[0].get("content", "") if messages else ""
                content = messages[-1].get("content", "") if messages else ""
                kind = (
                    "recommend"
                    if RECOMMEND_MARKER in system
                    else "extract"
                )
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization", ""),
                        "payload": body,
                        "kind": kind,
                        "model": body.get("model"),
                        "temperature": body.get("temperature"),
                    }
                )

                if kind == "recommend":
                    # 散文回复，不包 JSON。真实模型也走这条路。
                    reply_text = fake_recommendation(content)
                else:
                    text = (
                        content.split("用户需求原文：", 1)[-1]
                        .split("\n\n按上述", 1)[0]
                        .strip()
                    )
                    result = classifier.classify(text)
                    reply_text = json.dumps(
                        {
                            "judgements": [
                                {
                                    "criterion": item.criterion,
                                    "choice": item.choice,
                                    "evidence": item.evidence,
                                    "verdict": item.verdict,
                                }
                                for item in result.judgements
                            ],
                            "hard_constraints": dict(result.hard_hints),
                            "excluded_brands": list(result.excluded_brands),
                            "asks": list(result.asks),
                            "unsupported_needs": list(result.unsupported),
                        },
                        ensure_ascii=False,
                    )

                payload = {
                    "model": outer.model,
                    "choices": [
                        {"message": {"role": "assistant", "content": reply_text}}
                    ],
                }
                out = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self):
        os.environ["LLM_BASE_URL"] = self.base_url
        os.environ["LLM_MODEL"] = self.model
        os.environ.setdefault("LLM_API_KEY", "sk-fake-local-no-real-key")
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False


__all__ = ["FakeOpenAIServer"]
