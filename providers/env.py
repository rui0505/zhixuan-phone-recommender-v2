"""`.env` 文件加载器（零依赖）。

## 为什么有这个模块

`.env.example` 一直写着「复制为 `.env` 后填入真实值」，但在本模块出现之前
**没有任何代码读它**：`providers/llm.py` 只读环境变量，`app.py` 只搬
`st.secrets`。也就是说照着模板配 `.env` 是**完全无效**的——应用仍会报
「未配置 LLM_BASE_URL」。本模块补上这一环。

## 为什么自己写

本项目只读三个键，`.env.example` 的格式就是最朴素的 `KEY=value`。
为此引入 `python-dotenv` 不划算（`requirements.txt` 一直只维持 4 个依赖）。

## 优先级

    真实环境变量  >  `.env`  >  `st.secrets`

`load_dotenv()` 默认**只填不覆盖**，所以「临时用 `export` 改一个值」永远
压得住 `.env`。这条规则有测试锁住。

## 安全

密钥只进 `os.environ`，**不进返回值、不进日志**。`load_dotenv` 返回的是
「写入了哪些键名」，不是值——调用方可以放心打印。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping

# 合法的键名。`LLM_API_KEY` 这类都符合；出现别的形态说明这行写坏了。
_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 双引号里的转义序列
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}

__all__ = ["DotenvLoad", "load_dotenv", "parse_dotenv", "find_dotenv"]


@dataclass
class DotenvLoad:
    """一次加载的结果。**只记录键名，不记录值。**"""

    path: Path | None = None
    found: bool = False
    loaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.found:
            return "未找到 .env（只使用真实环境变量与 st.secrets）"
        parts = [f"已读取 {self.path}"]
        parts.append(f"写入 {len(self.loaded)} 项" + (f"：{'、'.join(self.loaded)}" if self.loaded else ""))
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 项（已有同名环境变量）：{'、'.join(self.skipped)}")
        if self.problems:
            parts.append(f"有 {len(self.problems)} 行无法解析")
        return "；".join(parts)


def _strip_inline_comment(value: str) -> str:
    """去掉行内注释。

    `#` 只有在**前面是空白**时才算注释起点，否则 `sk-a#b` 这种密钥会被截断。
    """
    for index, char in enumerate(value):
        if char == "#" and index > 0 and value[index - 1] in " \t":
            return value[:index].rstrip()
    return value


def _unquote(value: str) -> str:
    """剥掉包裹的引号。双引号内支持 `\\n` 这类转义，单引号内保持原样。"""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        body = value[1:-1]
        out: list[str] = []
        index = 0
        while index < len(body):
            char = body[index]
            if char == "\\" and index + 1 < len(body):
                nxt = body[index + 1]
                if nxt in _ESCAPES:
                    out.append(_ESCAPES[nxt])
                    index += 2
                    continue
            out.append(char)
            index += 1
        return "".join(out)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def parse_dotenv(text: str) -> tuple[dict[str, str], list[str]]:
    """把 `.env` 文本解析成键值对。纯函数，不碰环境变量。

    返回 `(键值对, 问题列表)`。问题列表里是「第 N 行：原因」这种可读描述，
    行号从 1 开始，方便用户直接去文件里改。
    """
    values: dict[str, str] = {}
    problems: list[str] = []

    # 去掉 BOM：Windows 记事本存 UTF-8 会带上它，否则第一个键名会多一个字符
    text = text.lstrip("\ufeff")

    for number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip("\r\n").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            problems.append(f"第 {number} 行：没有等号，无法解析（{raw_line.strip()!r}）")
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_PATTERN.match(key):
            problems.append(f"第 {number} 行：键名 {key!r} 不合法")
            continue
        value = value.strip()
        if not (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
            value = _strip_inline_comment(value)
        values[key] = _unquote(value.strip())

    return values, problems


def find_dotenv(start: Path | None = None) -> Path | None:
    """按顺序找一个可用的 `.env`。

    查找顺序（先命中先用）：

    1. 环境变量 `LLM_ENV_FILE` 指定的路径
    2. 当前工作目录下的 `.env`
    3. 本包的上一级目录（即 `phone-demo/.env`）

    第 3 条是为了「从仓库根目录运行脚本」也能找到——两种启动方式都不出错。
    """
    override = os.environ.get("LLM_ENV_FILE")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None

    roots: list[Path] = []
    if start is not None:
        roots.append(Path(start))
    roots.append(Path.cwd())
    roots.append(Path(__file__).resolve().parents[1])

    for root in roots:
        candidate = root / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(
    path: str | os.PathLike[str] | None = None,
    *,
    override: bool = False,
    environ: MutableMapping[str, str] | None = None,
) -> DotenvLoad:
    """读 `.env` 并把缺失的键写进环境变量。

    - `path` 留空则走 `find_dotenv()` 的顺序查找。
    - `override=False`（默认）**只填不覆盖**：已存在的环境变量优先级更高。
    - `environ` 可注入替身，便于测试（默认 `os.environ`）。

    文件不存在是**正常情况**（云端部署走 `st.secrets`），因此静默返回
    `found=False`，不抛异常、不打印警告。
    """
    target = Path(path) if path is not None else find_dotenv()
    report = DotenvLoad(path=target)
    if target is None or not target.is_file():
        return report

    report.found = True
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as error:
        report.problems.append(f"读取失败：{error}")
        return report

    values, problems = parse_dotenv(text)
    report.problems = problems

    env = os.environ if environ is None else environ
    for key, value in values.items():
        if not override and env.get(key):
            report.skipped.append(key)
            continue
        env[key] = value
        report.loaded.append(key)

    return report


def mask(secret: str) -> str:
    """把密钥打码成可安全打印的样子。空值返回 `（空）`。"""
    if not secret:
        return "（空）"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * 6}{secret[-4:]}"


def describe(environ: Mapping[str, str] | Iterable[str] | None = None) -> list[tuple[str, str]]:
    """列出三个 LLM 配置项及其当前取值（密钥已打码），供体检脚本打印。"""
    env = os.environ if environ is None else environ
    rows: list[tuple[str, str]] = []
    for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        value = env.get(key, "")
        rows.append((key, mask(value) if key == "LLM_API_KEY" else (value or "（未设置）")))
    return rows
