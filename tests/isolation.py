"""测试与开发机环境隔离的共用基类。

`app.py` 与 `web/server.py` 启动时都会调用 `load_dotenv()`，把 `.env` 的值写进
`os.environ`——这是**全局副作用**，会顺着 unittest 的同一个进程泄漏到后面的
测试模块（discover 按模块名排序，`test_app_flow` 排在 `test_llm` 之前）。

结果就是：开发机上有 `.env` 的人跑测试会红、没有的人会绿。测试必须与机器无关。

把 `LLM_ENV_FILE` 指向一个不存在的路径，`find_dotenv()` 就返回 None，
`.env` 被彻底屏蔽。

**任何会触发 `load_dotenv()` 的用例都必须继承 `IsolatedEnvTest`。**

本模块不匹配 `test*.py`，不会被 unittest discover 收集。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import streamlit as st

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

LLM_VARS = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "LLM_THINKING")
NO_DOTENV = str(BASE / ".env.must-not-exist-during-tests")


class IsolatedEnvTest(unittest.TestCase):
    """屏蔽 `.env`、清空 `LLM_*` 环境变量，并清掉 Streamlit 的缓存。

    少了这一层，测试结果就取决于开发机上有没有 `.env`。
    """

    def setUp(self):
        self._saved = {
            key: os.environ.get(key) for key in (*LLM_VARS, "LLM_ENV_FILE")
        }
        for key in LLM_VARS:
            os.environ.pop(key, None)
        os.environ["LLM_ENV_FILE"] = NO_DOTENV
        st.cache_resource.clear()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        st.cache_resource.clear()
