"""端到端冒烟：本地假 OpenAI 服务 + LLMClassifier 真实 HTTP 往返。

目的不是测「模型准不准」，而是证明**线格式**是对的：
真实 socket → 真实 JSON 编解码 → normalise_response → parse_needs → 评测比对。
假服务内部委托 MockClassifier，因此通过率应与 mock 路线一致（100%）。
若此处掉分，说明是管道问题，不是模型问题。

用完即弃，不入库。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from providers.jev import MockClassifier, CRITERIA  # noqa: E402

_clf = MockClassifier()
_seen_requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _seen_requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "response_format": body.get("response_format"),
                "model": body.get("model"),
            }
        )
        text = body["messages"][-1]["content"].split("用户需求原文：", 1)[1]
        text = text.split("\n\n按上述", 1)[0].strip()

        result = _clf.classify(text)
        payload = {
            "judgements": [
                {
                    "criterion": j.criterion,
                    "choice": j.choice,
                    "evidence": j.evidence,
                    "verdict": j.verdict,
                }
                for j in result.judgements
            ],
            "hard_constraints": dict(result.hard_hints),
            "excluded_brands": list(result.excluded_brands),
            "asks": list(result.asks),
            "unsupported_needs": list(result.unsupported),
        }
        content = json.dumps(payload, ensure_ascii=False)
        out = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    os.environ["LLM_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
    os.environ["LLM_MODEL"] = "fake-model"
    os.environ["LLM_API_KEY"] = "sk-fake-for-smoke-test"

    print(f"假服务已启动：http://127.0.0.1:{port}/v1")

    # 直接走评测脚本的主流程
    out_json = Path(tempfile.gettempdir()) / "smoke_llm_e2e_result.json"
    sys.argv = [
        "evaluate_needs.py",
        "--impl", "provider",
        "--classifier", "llm",
        "--json", str(out_json),
    ]
    import runpy

    try:
        runpy.run_path(str(BASE / "tests" / "evaluate_needs.py"), run_name="__main__")
    except SystemExit as exc:
        code = exc.code or 0
    else:
        code = 0

    print()
    print("=== 假服务收到的请求 ===")
    print(f"请求数：{len(_seen_requests)}（应等于 20 例）")
    first = _seen_requests[0]
    print(f"路径：{first['path']}（应为 /v1/chat/completions）")
    print(f"Authorization 头存在：{bool(first['auth'])}")
    print(f"Bearer 前缀正确：{first['auth'].startswith('Bearer ')}")
    print(f"response_format：{first['response_format']}")
    print(f"model：{first['model']}")
    print(f"所有请求路径一致：{len({r['path'] for r in _seen_requests}) == 1}")

    server.shutdown()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
