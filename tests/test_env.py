"""`.env` 加载器测试（providers/env.py）。

覆盖两类风险：

1. **解析正确性**：`.env` 是手写的，引号、注释、`export`、CRLF、BOM 都可能出现。
   解析错了会得到「看起来配好了但值是错的」——比直接报错更难查。
2. **优先级**：真实环境变量必须压得住 `.env`，否则「临时 export 改一个值」
   会被文件里的旧值悄悄覆盖，排查起来极其费时。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from providers.env import (  # noqa: E402
    DotenvLoad,
    find_dotenv,
    load_dotenv,
    mask,
    parse_dotenv,
)


class ParseTests(unittest.TestCase):
    """纯解析：不碰环境变量。"""

    def test_plain_pairs(self):
        values, problems = parse_dotenv("A=1\nB=hello\n")
        self.assertEqual(values, {"A": "1", "B": "hello"})
        self.assertEqual(problems, [])

    def test_comments_and_blank_lines_ignored(self):
        text = "# 顶注\n\nA=1\n   \n# 中间注释\nB=2\n"
        values, problems = parse_dotenv(text)
        self.assertEqual(values, {"A": "1", "B": "2"})
        self.assertEqual(problems, [])

    def test_export_prefix_stripped(self):
        values, _ = parse_dotenv("export LLM_MODEL=deepseek-chat\n")
        self.assertEqual(values, {"LLM_MODEL": "deepseek-chat"})

    def test_whitespace_around_key_and_value(self):
        values, _ = parse_dotenv("  A  =  spaced  \n")
        self.assertEqual(values, {"A": "spaced"})

    def test_double_quotes_stripped_and_escapes_expanded(self):
        values, _ = parse_dotenv('A="line1\\nline2"\n')
        self.assertEqual(values, {"A": "line1\nline2"})

    def test_single_quotes_kept_literal(self):
        """单引号内不做转义展开——密钥里出现反斜杠时不能被吃掉。"""
        values, _ = parse_dotenv("A='sk-\\nnot-newline'\n")
        self.assertEqual(values, {"A": "sk-\\nnot-newline"})

    def test_inline_comment_after_unquoted_value(self):
        values, _ = parse_dotenv("A=value  # 这是注释\n")
        self.assertEqual(values, {"A": "value"})

    def test_hash_inside_value_without_space_is_kept(self):
        """`sk-a#b` 里的 `#` 不是注释起点，否则密钥会被截断。"""
        values, _ = parse_dotenv("A=sk-a#b\n")
        self.assertEqual(values, {"A": "sk-a#b"})

    def test_hash_inside_quotes_is_kept(self):
        values, _ = parse_dotenv('A="sk-a # b"\n')
        self.assertEqual(values, {"A": "sk-a # b"})

    def test_value_may_contain_equals_sign(self):
        values, _ = parse_dotenv("A=a=b=c\n")
        self.assertEqual(values, {"A": "a=b=c"})

    def test_empty_value(self):
        values, problems = parse_dotenv("LLM_API_KEY=\n")
        self.assertEqual(values, {"LLM_API_KEY": ""})
        self.assertEqual(problems, [])

    def test_crlf_line_endings(self):
        values, problems = parse_dotenv("A=1\r\nB=2\r\n")
        self.assertEqual(values, {"A": "1", "B": "2"})
        self.assertEqual(problems, [])

    def test_bom_stripped(self):
        """记事本存的 UTF-8 会带 BOM，否则第一个键名会多一个不可见字符。"""
        values, _ = parse_dotenv("\ufeffA=1\n")
        self.assertEqual(values, {"A": "1"})

    def test_malformed_line_reports_line_number(self):
        values, problems = parse_dotenv("A=1\n这行没有等号\nB=2\n")
        self.assertEqual(values, {"A": "1", "B": "2"})
        self.assertEqual(len(problems), 1)
        self.assertIn("第 2 行", problems[0])

    def test_invalid_key_reported(self):
        values, problems = parse_dotenv("2BAD=1\n")
        self.assertEqual(values, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("第 1 行", problems[0])

    def test_later_definition_wins(self):
        values, _ = parse_dotenv("A=1\nA=2\n")
        self.assertEqual(values, {"A": "2"})


class LoadTests(unittest.TestCase):
    """写环境变量：优先级与幂等性。"""

    def _write(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        )
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: os.unlink(handle.name))
        return Path(handle.name)

    def test_loads_into_injected_environ(self):
        path = self._write("LLM_MODEL=m1\nLLM_API_KEY=k1\n")
        env: dict[str, str] = {}
        report = load_dotenv(path, environ=env)
        self.assertTrue(report.found)
        self.assertEqual(env["LLM_MODEL"], "m1")
        self.assertEqual(sorted(report.loaded), ["LLM_API_KEY", "LLM_MODEL"])

    def test_existing_env_wins_by_default(self):
        """真实环境变量优先级最高，这是「临时 export 能压住 .env」的保证。"""
        path = self._write("LLM_MODEL=from-file\n")
        env = {"LLM_MODEL": "from-shell"}
        report = load_dotenv(path, environ=env)
        self.assertEqual(env["LLM_MODEL"], "from-shell")
        self.assertEqual(report.loaded, [])
        self.assertEqual(report.skipped, ["LLM_MODEL"])

    def test_override_true_replaces_existing(self):
        path = self._write("LLM_MODEL=from-file\n")
        env = {"LLM_MODEL": "from-shell"}
        load_dotenv(path, override=True, environ=env)
        self.assertEqual(env["LLM_MODEL"], "from-file")

    def test_empty_existing_value_is_filled(self):
        """环境变量存在但是空串，应当被文件里的值填上。"""
        path = self._write("LLM_API_KEY=k\n")
        env = {"LLM_API_KEY": ""}
        load_dotenv(path, environ=env)
        self.assertEqual(env["LLM_API_KEY"], "k")

    def test_missing_file_is_silent(self):
        """云端部署没有 .env 是正常的，不能抛异常、不能警告。"""
        report = load_dotenv(Path("definitely-not-here.env"), environ={})
        self.assertFalse(report.found)
        self.assertEqual(report.problems, [])
        self.assertIsInstance(report, DotenvLoad)

    def test_report_never_contains_values(self):
        """返回值只记录键名。密钥不能出现在任何可打印的地方。"""
        path = self._write("LLM_API_KEY=sk-super-secret-value\n")
        report = load_dotenv(path, environ={})
        blob = repr(report) + report.summary
        self.assertNotIn("sk-super-secret-value", blob)
        self.assertIn("LLM_API_KEY", report.summary)

    def test_idempotent(self):
        path = self._write("LLM_MODEL=m1\n")
        env: dict[str, str] = {}
        load_dotenv(path, environ=env)
        second = load_dotenv(path, environ=env)
        self.assertEqual(env["LLM_MODEL"], "m1")
        self.assertEqual(second.loaded, [])
        self.assertEqual(second.skipped, ["LLM_MODEL"])


class FindTests(unittest.TestCase):
    """查找顺序：LLM_ENV_FILE 优先。"""

    def test_env_var_override_wins(self):
        path = self._write_named(".env")
        old = os.environ.get("LLM_ENV_FILE")
        os.environ["LLM_ENV_FILE"] = str(path)
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("LLM_ENV_FILE", old)
                if old is not None
                else os.environ.pop("LLM_ENV_FILE", None)
            )
        )
        self.assertEqual(find_dotenv(), path)

    def test_env_var_pointing_at_missing_file_returns_none(self):
        old = os.environ.get("LLM_ENV_FILE")
        os.environ["LLM_ENV_FILE"] = str(Path("nope-does-not-exist.env"))
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("LLM_ENV_FILE", old)
                if old is not None
                else os.environ.pop("LLM_ENV_FILE", None)
            )
        )
        self.assertIsNone(find_dotenv())

    def _write_named(self, name: str) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / name
        path.write_text("A=1\n", encoding="utf-8")
        self.addCleanup(lambda: os.unlink(path))
        return path


class ShippedTemplateTests(unittest.TestCase):
    """随仓库发布的 .env.example 必须能被本解析器完整解析。

    这是最实际的一条：用户照着模板配，解析器要是读不懂模板的写法就白搭。
    """

    def test_env_example_parses_cleanly(self):
        template = BASE / ".env.example"
        self.assertTrue(template.is_file(), "仓库里应当有 .env.example")
        values, problems = parse_dotenv(template.read_text(encoding="utf-8"))
        self.assertEqual(problems, [])
        self.assertEqual(
            sorted(values), ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "PHONE_ENABLE_LLM",
                            "PHONE_LLM_BUDGET_APPROVED", "PHONE_LLM_GATEWAY_LIMITS_APPROVED"]
        )
        from providers.env import llm_enabled
        self.assertFalse(llm_enabled(values))
        self.assertEqual(values['LLM_BASE_URL'],'')
        self.assertEqual(values['LLM_MODEL'],'')
        self.assertEqual(values['LLM_API_KEY'],'')


class MaskTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(mask(""), "（空）")

    def test_short_fully_masked(self):
        self.assertEqual(mask("abc"), "***")

    def test_long_shows_edges_only(self):
        masked = mask("sk-1234567890abcdef")
        self.assertTrue(masked.startswith("sk-1"))
        self.assertTrue(masked.endswith("cdef"))
        self.assertIn("*", masked)
        self.assertNotIn("567890abc", masked)


if __name__ == "__main__":
    unittest.main()
