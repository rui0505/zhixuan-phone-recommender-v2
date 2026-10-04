"""本地演示：假 OpenAI 服务 + Streamlit 一起启动。

用途：没有密钥时也能演示/调试自然语言输入这条链路。
假服务内部委托 `MockClassifier`，返回格式与真实供应商一致。

    python tools/dev_server.py            # 默认 8501
    python tools/dev_server.py --port 8600

**这不是生产入口**，只是开发与演示用。真实部署走 `streamlit run app.py`
并配置 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))

from fake_openai import FakeOpenAIServer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="假大模型 + Streamlit 演示环境")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit 端口")
    args = parser.parse_args()

    server = FakeOpenAIServer()
    with server:
        print("=" * 64)
        print("假 OpenAI 服务：", server.base_url)
        print("模型：", server.model)
        print("（返回由 MockClassifier 生成，格式与真实供应商一致）")
        print("=" * 64)
        print()
        print(f"打开 http://localhost:{args.port}")
        print("在页面底部的对话框里说一句需求，就会出机型与推荐语。")
        print("按 Ctrl+C 退出。")
        print()

        environment = dict(os.environ)
        environment["LLM_BASE_URL"] = server.base_url
        environment["LLM_MODEL"] = server.model
        environment.setdefault("LLM_API_KEY", "sk-fake-local-no-real-key")
        environment["PYTHONIOENCODING"] = "utf-8"

        return subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(BASE / "app.py"),
                "--server.port",
                str(args.port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=str(BASE),
            env=environment,
        )


if __name__ == "__main__":
    raise SystemExit(main())
