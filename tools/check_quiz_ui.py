"""用本机 Edge 真实跑一遍「快速问答」流程。

验证的是**页面实际行为**，不是接口返回：DOM 可见性、按钮状态、问答推进、
结果渲染。这些只有浏览器能发现（CSS 特异性、hidden 失效都栽在这上面）。

用法：
    .venv/Scripts/python.exe tools/check_quiz_ui.py [base_url]

默认打 http://127.0.0.1:8010（避免与开发中的 8000 冲突）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8010'
SHOT = Path(tempfile.gettempdir()) / 'zx-quiz'

errors: list[str] = []
console_errors: list[str] = []
page_errors: list[str] = []


def check(label: str, ok: bool, detail: str = '') -> None:
    mark = '  OK ' if ok else ' FAIL'
    print(f'{mark} {label}' + (f'  —— {detail}' if detail else ''))
    if not ok:
        errors.append(f'{label}: {detail}')


def main() -> int:
    SHOT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', args=['--no-proxy-server'])
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: page_errors.append(str(e)))

        print(f'打开 {BASE}')
        page.goto(BASE, wait_until='networkidle', timeout=60000)
        page.wait_for_timeout(1200)

        # --- 1. 首屏有「快速问答」按钮 ---
        btn = page.locator('#p-quiz-start')
        check('首屏有「快速问答」按钮', btn.is_visible())
        check('按钮文案正确',
              '快速问答' in (btn.inner_text() or ''),
              repr(btn.inner_text()))
        page.screenshot(path=str(SHOT / '1-hero.png'), full_page=False)

        # --- 2. 点开问答，出现第一题 ---
        btn.click()
        page.wait_for_timeout(500)
        check('问答面板可见', page.locator('#p-quiz').is_visible())
        check('首屏已收起', not page.locator('#p-hero').is_visible())
        check('常规推荐区已收起', not page.locator('#p-reco').is_visible())
        # 问答期间不应同时展示浏览区，否则页面上有两套内容在抢注意力。
        for selector in ('#p-discover', '#p-bottom'):
            check(f'问答期间 {selector} 已收起',
                  not page.locator(selector).is_visible())

        opts = page.locator('#p-quiz .p-opt')
        n_opt = opts.count()
        check('第一题有 3 个选项', n_opt == 3, f'实际 {n_opt}')
        step = page.locator('#p-quiz .p-quiz-step').inner_text()
        check('显示第 1 题', '第 1' in step, step)
        page.screenshot(path=str(SHOT / '2-q1.png'), full_page=False)

        # 「下一题」在未作答时应禁用
        next_btn = page.locator('[data-quiz="next"]')
        check('未作答时「下一题」禁用', next_btn.is_disabled())

        # --- 3. 依次作答五题 ---
        # 选择与「拍照大存储」一致的答案，便于与接口层结果交叉核对。
        picks = ['b2', 'photo', 'light', 'large', 's512']
        for i, key in enumerate(picks):
            page.locator(f'#p-quiz [data-opt="{key}"]').click()
            page.wait_for_timeout(350)
            if i < len(picks) - 1:
                step = page.locator('#p-quiz .p-quiz-step').inner_text()
                check(f'答完第 {i + 1} 题后自动进入下一题',
                      f'第 {i + 2}' in step, step)
        page.screenshot(path=str(SHOT / '3-last.png'), full_page=False)

        # --- 4. 可以回退修改 ---
        # 此时停在第 5 题（索引 4），「上一题」应回到第 4 题。
        page.locator('[data-quiz="prev"]').click()
        page.wait_for_timeout(300)
        step = page.locator('#p-quiz .p-quiz-step').inner_text()
        check('「上一题」可回退到第 4 题', '第 4' in step, step)
        # 回退后应能看到已选项处于选中态
        pressed = page.locator('#p-quiz .p-opt[aria-pressed="true"]').count()
        check('回退后已选项保持选中态', pressed == 1, f'选中 {pressed} 个')
        page.locator('[data-quiz="next"]').click()
        page.wait_for_timeout(300)
        step = page.locator('#p-quiz .p-quiz-step').inner_text()
        check('再「下一题」回到第 5 题', '第 5' in step, step)

        # --- 5. 提交出机型 ---
        submit = page.locator('[data-quiz="submit"]')
        check('答完五题后「看看推荐结果」可用', not submit.is_disabled())
        submit.click()
        page.wait_for_selector('#p-quiz .p-grid .p-card', timeout=120000)
        page.wait_for_timeout(900)

        cards = page.locator('#p-quiz .p-grid .p-card')
        n_card = cards.count()
        check('结果区出了机型卡片', n_card > 0, f'{n_card} 张')
        check('机型数不超过 top_n', n_card <= 5, f'{n_card}')

        # 回显用户的选择
        recap = page.locator('#p-quiz .p-recap').last
        check('结果区回显了用户选择', recap.is_visible())
        recap_text = recap.inner_text() or ''
        check('回显含预算选项', '3000 到 4500' in recap_text, recap_text[:120])
        check('回显含场景选项', '拍照' in recap_text)

        # 「影像」应被如实标注为无数据依据
        quiz_text = page.locator('#p-quiz').inner_text() or ''
        check('如实标注「影像」无数据依据',
              '影像' in quiz_text and ('覆盖不到' in quiz_text or '没有数据' in quiz_text),
              [l for l in quiz_text.splitlines() if '影像' in l][:2])

        page.screenshot(path=str(SHOT / '4-result.png'), full_page=True)

        # 结果区不能同时出现在两个地方
        check('常规推荐区没有重复显示结果', not page.locator('#p-reco').is_visible())

        # --- 6. 收起问答后首屏恢复 ---
        page.locator('[data-quiz="cancel"]').click()
        page.wait_for_timeout(500)
        check('收起问答后首屏恢复', page.locator('#p-hero').is_visible())
        check('收起问答后问答面板隐藏', not page.locator('#p-quiz').is_visible())
        check('收起问答后浏览区恢复', page.locator('#p-discover').is_visible())
        page.screenshot(path=str(SHOT / '5-after.png'), full_page=False)

        # --- 7. 重新打开应清空上次答案 ---
        page.locator('#p-quiz-start').click()
        page.wait_for_timeout(400)
        step = page.locator('#p-quiz .p-quiz-step').inner_text()
        check('重新打开回到第 1 题', '第 1' in step, step)
        pressed = page.locator('#p-quiz .p-opt[aria-pressed="true"]').count()
        check('重新打开时清空了上次答案', pressed == 0, f'仍选中 {pressed} 个')

        # --- 8. 手机库不受影响 ---
        page.locator('[data-nav="library"]').click()
        page.wait_for_timeout(500)
        check('切到手机库后问答面板隐藏', not page.locator('#p-quiz').is_visible())
        lib_cards = page.locator('#p-results .p-card').count()
        check('手机库仍有卡片', lib_cards > 0, f'{lib_cards} 张')

        browser.close()

    print()
    print('控制台 error：', console_errors if console_errors else '无')
    print('页面异常：', page_errors if page_errors else '无')
    print('截图目录：', SHOT)
    if console_errors or page_errors:
        errors.append('存在控制台错误或页面异常')

    print()
    if errors:
        print(f'===== {len(errors)} 项未通过 =====')
        for item in errors:
            print(' -', item)
        return 1
    print('===== 全部通过 =====')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
