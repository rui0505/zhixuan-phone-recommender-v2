"""用本机 Edge 核对 Web 前端：初始态 → 追问 → 出机型。

真实服务、真实大模型（一次抽取 + 一次推荐语）。截图落在系统临时目录。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8123/'
OUT = Path(tempfile.gettempdir()) / 'zhixuan-shots'
OUT.mkdir(parents=True, exist_ok=True)

errors: list[str] = []
console: list[str] = []


def shot(page, name: str) -> Path:
    path = OUT / f'{name}.png'
    page.screenshot(path=str(path), full_page=False)
    print(f'  截图 -> {path}')
    return path


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.on('console', lambda m: console.append(f'{m.type}: {m.text}'))
    page.on('pageerror', lambda e: errors.append(str(e)))

    print('=== 1. 首屏 ===')
    page.goto(URL, wait_until='domcontentloaded')
    page.wait_for_function(
        "() => !document.querySelector('#p-footnote').textContent.includes('正在载入')",
        timeout=15000,
    )
    print('  llm:', page.inner_text('#p-llm'))
    print('  页脚:', page.inner_text('#p-footnote')[:120])
    print('  分类数:', page.locator('#p-categories button').count())
    print('  卡片数:', page.locator('#p-results .p-card').count())
    print('  状态:', page.inner_text('#p-status'))
    shot(page, '01-initial')

    print('=== 2. 含糊预算 → 追问 ===')
    page.fill('#p-need', '预算三千左右，想要轻一点的')
    page.click('#p-recommend')
    page.wait_for_selector('#p-reco .p-msg', timeout=60000)
    page.wait_for_function("() => !document.querySelector('#p-reco').innerText.includes('正在理解')", timeout=60000)
    print('  推荐区:', page.inner_text('#p-reco')[:200].replace('\n', ' / '))
    print('  快捷按钮:', page.locator('#p-reco .p-chips button').count())
    shot(page, '02-clarify')

    print('=== 3. 点「4000 元」→ 出机型 ===')
    page.click('#p-reco .p-chips button:has-text("4000 元")')
    page.wait_for_function(
        "() => document.querySelector('#p-reco').innerText.includes('本次推荐')",
        timeout=90000,
    )
    cards = page.locator('#p-reco .p-card')
    print('  机型卡片:', cards.count())
    for index in range(cards.count()):
        card = cards.nth(index)
        print('   ', card.locator('h4').inner_text(), '|', card.locator('.p-price').inner_text(),
              '|', card.locator('.p-fit span').inner_text())
    print('  推荐语:', page.inner_text('#p-reco .p-advice')[:160].replace('\n', ' '))
    shot(page, '03-result')

    print('=== 4. 手机库 ===')
    page.click('[data-nav="library"]')
    page.wait_for_timeout(400)
    print('  状态:', page.inner_text('#p-status'))
    print('  卡片数:', page.locator('#p-results .p-card').count())
    print('  分类:', page.locator('#p-categories button').all_inner_texts())
    # 回归：`.p-hero{display:grid}` 与 `[hidden]` 特异性相同，曾经把首屏与推荐区
    # 盖成常显；`renderReco()` 也会无条件写 hidden=false。两处都必须挡住。
    for selector in ('#p-hero', '#p-reco'):
        visible = page.locator(selector).is_visible()
        print(f'  {selector} 可见: {visible}')
        if visible:
            errors.append(f'手机库标签页下 {selector} 不该可见')
    shot(page, '04-library')

    print('=== 5. 按品牌 + 展开分析 + 对比 ===')
    page.click('[data-mode="brand"]')
    page.wait_for_timeout(300)
    print('  品牌分类:', page.locator('#p-categories button').all_inner_texts())
    page.click('#p-categories button:has-text("苹果")')
    page.wait_for_timeout(300)
    print('  苹果:', page.inner_text('#p-status'))
    page.locator('#p-results .p-card button[data-detail]').first.click()
    page.wait_for_timeout(300)
    print('  分析块:', page.locator('#p-results .p-detail').count())
    page.locator('#p-results .p-card button[data-compare]').first.click()
    page.wait_for_timeout(300)
    print('  对比计数:', page.inner_text('#p-count'))
    shot(page, '05-brand-detail')

    print('=== 6. 对比页 ===')
    page.click('#p-open-compare')
    page.wait_for_timeout(400)
    print('  对比面板:', page.inner_text('#p-compare-panel')[:160].replace('\n', ' / '))
    shot(page, '06-compare')

    print('=== 7. 失败态（服务端不可用时的文案）===')
    page.click('[data-nav="smart"]')
    page.wait_for_timeout(300)
    print('  错误框数:', page.locator('#p-reco .p-err').count())

    print()
    print('页面异常:', errors or '无')
    bad = [c for c in console if c.startswith('error')]
    print('控制台 error:', bad or '无')
    print('截图目录:', OUT)
    browser.close()
