"""`web/server.py` 的接口测试：真起一个服务，用真实 HTTP 打进去。

不引入 `httpx`（`starlette.testclient` 需要它），改用标准库 `urllib` 直接打
真端口——这样路由注册、静态托管、JSON 编解码都一起验了，而且**不新增依赖**，
`requirements.txt` 的 4 个运行时依赖保持不变。

分类器通过 `app.state.classifier` 注入 `MockClassifier`，所以跑测试
**不产生任何线上调用费用**。

测试全部继承 `IsolatedEnvTest`：清空 `LLM_*` 并屏蔽开发机的 `.env`。
少了这一层，`/api/meta` 的 `llm.ready` 就会取决于本机有没有配密钥，
测试结果随机器而变。
"""

from __future__ import annotations

import gc
import json
import re
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import uvicorn  # noqa: E402

from engine import load_data  # noqa: E402
from providers.jev import MockClassifier  # noqa: E402
from quiz import build_text, quiz_payload  # noqa: E402
from tests.isolation import IsolatedEnvTest  # noqa: E402
from web import server  # noqa: E402


def free_port() -> int:
    """要一个空闲端口。绑 0 让系统分配，拿到号就立刻释放。"""
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class WebServerTest(IsolatedEnvTest):
    """整个类共用一个真实服务；每个用例之间重置数据缓存。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        config = uvicorn.Config(
            server.app, host='127.0.0.1', port=cls.port, log_level='error'
        )
        cls.uvicorn = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.uvicorn.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 20
        while not cls.uvicorn.started and time.time() < deadline:
            time.sleep(0.05)
        if not cls.uvicorn.started:
            raise RuntimeError('测试服务未能启动')

    @classmethod
    def tearDownClass(cls):
        cls.uvicorn.should_exit = True
        cls.thread.join(timeout=15)
        # uvicorn 关监听套接字是异步的，不等 GC 就会在**别的测试模块**里
        # 冒出 ResourceWarning，看着像那里有问题。这里主动收一次。
        gc.collect()

    def setUp(self):
        super().setUp()
        server.reset_frame()
        server.app.state.classifier = MockClassifier()

    def tearDown(self):
        server.app.state.classifier = None
        server.reset_frame()
        super().tearDown()

    # --- HTTP 小工具 -------------------------------------------------------
    def url(self, path: str) -> str:
        return f'http://127.0.0.1:{self.port}{path}'

    def get(self, path: str) -> tuple[int, bytes]:
        with urllib.request.urlopen(self.url(path), timeout=30) as response:
            return response.status, response.read()

    def get_json(self, path: str) -> tuple[int, dict]:
        status, body = self.get(path)
        return status, json.loads(body)

    def post(self, path: str, payload=None, *, raw: bytes | None = None) -> tuple[int, dict]:
        data = raw if raw is not None else json.dumps(payload or {}).encode('utf-8')
        request = urllib.request.Request(
            self.url(path), data=data, headers={'Content-Type': 'application/json'}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            # HTTPError 也是响应对象，不关会留下 ResourceWarning。
            with error:
                return error.code, json.loads(error.read())

    def ask(self, text: str, **extra) -> dict:
        """打一次 `/api/recommend`，返回响应体。"""
        status, body = self.post('/api/recommend', {'text': text, **extra})
        self.assertEqual(status, 200, body)
        return body


# --- 静态页与路由 -----------------------------------------------------------


class StaticPageTests(WebServerTest):
    def visible_page(self) -> str:
        """页面文本，**去掉注释**。

        源码注释里会写「不存在『长续航』『折叠大屏』这类分类」来解释设计取舍，
        那是说明**为什么没有**，不算页面在宣称这些能力。只有去掉注释才能
        断言「页面没有宣称」。
        """
        _, body = self.get('/')
        page = body.decode('utf-8')
        page = re.sub(r'<!--.*?-->', '', page, flags=re.S)
        return re.sub(r'(?m)^\s*//.*$', '', page)

    def test_root_serves_the_frontend(self):
        status, body = self.get('/')
        self.assertEqual(status, 200)
        page = body.decode('utf-8')
        self.assertIn('知选', page)
        self.assertIn('/api/recommend', page)

    def test_frontend_has_no_hardcoded_model_list(self):
        # 旧原型把 9 款虚构机型写死在 JS 里。现在必须从 /api/phones 取。
        page = self.visible_page()
        for ghost in ('曜影 Pro', '竞速 GT', '长航 Max', '展开 Fold', '光映 Ultra'):
            self.assertNotIn(ghost, page)

    def test_frontend_does_not_claim_unsupported_dimensions(self):
        # 「拍照 / 游戏 / 续航 / 折叠」在数据表里没有依据，不得再出现。
        page = self.visible_page()
        for claim in ('长续航', '游戏性能', '影像摄影', '折叠大屏'):
            self.assertNotIn(claim, page)

    def test_frontend_takes_its_data_from_the_api(self):
        page = self.visible_page()
        self.assertIn("/api/phones", page)
        self.assertIn("/api/meta", page)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get('/api/nope')
        with caught.exception:
            self.assertEqual(caught.exception.code, 404)


# --- /api/meta --------------------------------------------------------------


class MetaTests(WebServerTest):
    def test_lists_the_brands_that_exist_in_the_data(self):
        _, meta = self.get_json('/api/meta')
        self.assertEqual(
            meta['brands'],
            ['OPPO', 'Redmi', 'vivo', '一加', '三星', '华为', '小米', '苹果', '荣耀'],
        )

    def test_reports_the_real_dataset_size(self):
        _, meta = self.get_json('/api/meta')
        self.assertEqual(meta['totals'], {'models': 46, 'configs': 156})

    def test_exposes_the_ui_contract(self):
        _, meta = self.get_json('/api/meta')
        self.assertEqual(meta['criteria'], ['price', 'weight', 'storage', 'refresh', 'screen'])
        self.assertEqual(meta['weight_labels']['price'], '价格更低')
        self.assertEqual(meta['budget']['limit'], [0, 20000])
        self.assertEqual(meta['storage']['options'], [64, 128, 256, 512, 1024, 2048])
        self.assertEqual(meta['top_n'], 5)

    def test_reports_llm_as_unavailable_without_config(self):
        # 基类清空了 LLM_*，所以这里必须是未接入——判定不能依赖开发机的 .env。
        _, meta = self.get_json('/api/meta')
        self.assertFalse(meta['llm']['ready'])
        self.assertIn('LLM_API_KEY', meta['llm']['message'])

    def test_never_leaks_a_secret(self):
        status, body = self.get('/api/meta')
        text = body.decode('utf-8')
        self.assertNotIn('sk-', text)
        self.assertNotIn('LLM_API_KEY=', text)


# --- /api/phones ------------------------------------------------------------


class PhonesTests(WebServerTest):
    def test_returns_every_config(self):
        _, payload = self.get_json('/api/phones')
        self.assertEqual(len(payload['phones']), 156)

    def test_first_row_carries_real_values(self):
        _, payload = self.get_json('/api/phones')
        sample = next(p for p in payload['phones'] if p['name'] == 'iPhone 15')
        self.assertEqual(sample['brand'], '苹果')
        self.assertEqual(sample['price'], 5999.0)
        self.assertEqual(sample['weight'], 171.0)

    def test_does_not_invent_battery_or_fold_fields(self):
        _, payload = self.get_json('/api/phones')
        for phone in payload['phones']:
            for key in ('battery', 'fold', 'foldable'):
                self.assertNotIn(key, phone)

    def test_never_leaks_a_secret(self):
        _, body = self.get('/api/phones')
        self.assertNotIn(b'api_key', body.lower())


# --- /api/recommend ---------------------------------------------------------


class RecommendTests(WebServerTest):
    def test_explicit_budget_returns_engine_models(self):
        body = self.ask('预算2000到3000，越轻越好', write=False)
        self.assertEqual(body['stage'], 'result')
        self.assertTrue(body['models'])
        self.assertLessEqual(len(body['models']), 5)
        self.assertGreater(body['candidate_configs'], 0)

    def test_models_all_come_from_the_dataset(self):
        _, payload = self.get_json('/api/phones')
        known = {p['name'] for p in payload['phones']}
        body = self.ask('预算3000到6000', write=False)
        for model in body['models']:
            self.assertIn(model['name'], known)

    def test_models_carry_the_facts_the_page_renders(self):
        body = self.ask('预算2000到3000', write=False)
        for model in body['models']:
            for key in ('id', 'name', 'brand', 'config', 'chip', 'price',
                        'weight', 'screen', 'refresh', 'storage', 'score', 'reason'):
                self.assertIn(key, model)

    def test_vague_budget_asks_instead_of_capping(self):
        body = self.ask('预算三千左右，想要轻一点的', write=False)
        self.assertEqual(body['stage'], 'clarify')
        self.assertEqual(body['models'], [])
        fields = [item['field'] for item in body['questions']]
        self.assertIn('budget.max', fields)
        self.assertTrue(body['ask_text'])

    def test_ask_text_uses_web_wording_not_streamlit_wording(self):
        body = self.ask('预算三千左右', write=False)
        self.assertIn('我先确认', body['ask_text'])
        self.assertNotIn('下方表单', body['ask_text'])

    def test_clarify_returns_the_state_the_client_must_echo_back(self):
        body = self.ask('预算三千左右', write=False)
        self.assertIn('budget.max', body['asked'])
        self.assertEqual(body['ask_rounds'], 1)

    def test_second_turn_with_the_answer_produces_models(self):
        first = self.ask('预算三千左右，想要轻一点的', write=False)
        second = self.ask(
            '最多 4000',
            history=['预算三千左右，想要轻一点的'],
            asked=first['asked'],
            ask_rounds=first['ask_rounds'],
            write=False,
        )
        self.assertEqual(second['stage'], 'result')
        self.assertEqual(second['budget'], [0, 4000])
        self.assertTrue(second['models'])

    def test_unsupported_needs_are_surfaced_not_replaced(self):
        body = self.ask('预算2000到3000，拍照要好，游戏要流畅', write=False)
        self.assertTrue(body['unsupported'])
        self.assertTrue(body['advice'])
        self.assertIn('数据', body['advice'])

    def test_advice_falls_back_to_template_without_llm(self):
        # 基类没有配密钥：推荐语必须降级成模板文案，而机型列表照常给出。
        body = self.ask('预算2000到3000，越轻越好')
        self.assertTrue(body['models'])
        self.assertTrue(body['advice'])
        self.assertFalse(body['advice_from_llm'])
        self.assertTrue(body['advice_error'])
        for model in body['models']:
            self.assertIn(model['name'], body['advice'])

    def test_llm_ready_flag_is_reported(self):
        body = self.ask('预算2000到3000', write=False)
        self.assertFalse(body['llm_ready'])

    def test_empty_text_is_rejected(self):
        status, body = self.post('/api/recommend', {'text': '   '})
        self.assertEqual(status, 400)
        self.assertIn('text', body['error'])

    def test_missing_text_is_rejected(self):
        status, _ = self.post('/api/recommend', {})
        self.assertEqual(status, 400)

    def test_invalid_json_is_rejected(self):
        status, body = self.post('/api/recommend', raw=b'{not json')
        self.assertEqual(status, 400)
        self.assertIn('JSON', body['error'])

    def test_non_object_body_is_rejected(self):
        status, body = self.post('/api/recommend', raw=b'[1,2,3]')
        self.assertEqual(status, 400)
        self.assertIn('JSON', body['error'])

    def test_junk_history_is_sanitised(self):
        # 前端传什么都有可能，脏数据不得进到管线里。
        body = self.ask(
            '预算2000到3000',
            history=[None, 42, {'a': 1}, '预算 3000 以内'],
            asked=['budget.max', 7],
            ask_rounds='不是数字',
            write=False,
        )
        self.assertEqual(body['stage'], 'result')

    def test_never_leaks_a_secret(self):
        status, body = self.post('/api/recommend', {'text': '预算2000到3000', 'write': False})
        self.assertNotIn('sk-', json.dumps(body, ensure_ascii=False))


class QuizTests(WebServerTest):
    """快速问答的两个接口。"""

    ANSWERS = {
        'budget': 'b2', 'scene': 'photo', 'carry': 'light',
        'screen': 'large', 'storage': 's512',
    }

    def quiz_ask(self, answers=None, **extra) -> dict:
        payload = {'answers': self.ANSWERS if answers is None else answers}
        payload.update(extra)
        status, body = self.post('/api/quiz/recommend', payload)
        self.assertEqual(status, 200, body)
        return body

    # --- 题库 ---
    def test_quiz_returns_questions(self):
        status, body = self.get_json('/api/quiz')
        self.assertEqual(status, 200)
        self.assertTrue(body['questions'])
        for question in body['questions']:
            self.assertTrue(question['key'])
            self.assertTrue(question['title'])
            self.assertGreaterEqual(len(question['options']), 2)

    def test_quiz_does_not_leak_the_internal_say_field(self):
        """`say` 是组句用的内部字段，不该出现在给前端的载荷里。"""
        _, body = self.get_json('/api/quiz')
        self.assertNotIn('say', json.dumps(body, ensure_ascii=False))

    def test_quiz_matches_the_loaded_question_bank(self):
        """接口给的题库必须就是 `quiz.py` 里那一份，不能另有一份。"""
        _, body = self.get_json('/api/quiz')
        self.assertEqual(body['questions'],
                         quiz_payload()['questions'])

    # --- 答卷 ---
    def test_full_answer_returns_engine_models(self):
        body = self.quiz_ask()
        self.assertEqual(body['stage'], 'result')
        self.assertTrue(body['models'])
        self.assertLessEqual(len(body['models']), body.get('candidate_configs', 0) + 5)

    def test_models_all_come_from_the_dataset(self):
        """机型必须真实存在，不能是拼出来的。"""
        frame = load_data()
        known = set(frame['产品名称'])
        body = self.quiz_ask()
        for model in body['models']:
            self.assertIn(model['name'], known)

    def test_answer_does_not_trigger_a_follow_up(self):
        """答题已经用掉追问额度，不该再回头问预算上界。"""
        body = self.quiz_ask()
        self.assertEqual(body['ask_text'], '')
        self.assertEqual(body['questions'], [])
        self.assertEqual(body['stage'], 'result')

    def test_response_echoes_the_answers(self):
        body = self.quiz_ask()
        labels = [item['label'] for item in body['answer_labels']]
        self.assertEqual(len(labels), 5)
        self.assertTrue(body['answers_text'])
        self.assertEqual(body['progress'],
                         {'answered': 5, 'total': len(body['answer_labels'])})

    def test_answers_text_is_what_the_engine_received(self):
        """回显的「原话」必须与真正送进管线的文本一致。

        不一致的话，用户看到的「我会这样理解」就是假话。
        """
        body = self.quiz_ask()
        self.assertEqual(body['answers_text'], build_text(self.ANSWERS))
        self.assertEqual(body['needs']['input_text'], body['answers_text'])

    def test_photo_scene_surfaces_unsupported_not_replaced(self):
        """拍照需求要如实说出来，不能用别的参数替代。"""
        body = self.quiz_ask({'scene': 'photo', 'budget': 'b2', 'storage': 's512'})
        self.assertEqual(body['stage'], 'result')
        self.assertIn('影像', body['unsupported'])
        self.assertIn('影像', body['expected_unsupported'])

    def test_unsupported_never_appears_as_a_scored_dimension(self):
        """「影像」不是可评分维度，不得混进权重或排序口径。"""
        body = self.quiz_ask({'scene': 'photo', 'budget': 'b2', 'storage': 's512'})
        self.assertNotIn('影像', body['weights'])
        self.assertEqual(sorted(body['weights']),
                         ['price', 'refresh', 'screen', 'storage', 'weight'])

    def test_write_false_skips_the_llm_advice(self):
        body = self.quiz_ask(write=False)
        self.assertTrue(body['advice'])

    def test_answer_text_is_accepted_by_the_free_text_endpoint(self):
        """答卷拼出的句子，直接走自由输入也应得到同样的候选数。

        这条锁住「两个入口共用一条链路」：如果哪天问答模式偷偷加了条件，
        这里会立刻暴露。
        """
        quiz = self.quiz_ask(write=False)
        free = self.ask(quiz['answers_text'], write=False)
        self.assertEqual(quiz['candidate_configs'], free['candidate_configs'])
        self.assertEqual([m['id'] for m in quiz['models']],
                         [m['id'] for m in free['models']])

    def test_partial_answer_still_produces_a_result(self):
        """只答一部分也要出结果，缺口写在 pending 里而不是变成新追问。"""
        # 「预算 3000 到 4500」上下界都明确，本身没有缺口；这里只答场景题，
        # 预算完全没提，才是真正的「有缺口」。
        body = self.quiz_ask({'scene': 'photo'})
        self.assertEqual(body['stage'], 'result')
        self.assertIn('预算上界', body['pending'])
        self.assertEqual(body['ask_text'], '')

    def test_explicit_range_leaves_no_pending_budget_item(self):
        """预算上下界都给了就不该再标「预算上界」待确认。"""
        body = self.quiz_ask({'budget': 'b2'})
        self.assertEqual(body['stage'], 'result')
        self.assertNotIn('预算上界', body['pending'])

    # --- 非法输入 ---
    def test_empty_answers_are_rejected(self):
        status, body = self.post('/api/quiz/recommend', {'answers': {}})
        self.assertEqual(status, 400)
        self.assertIn('error', body)

    def test_missing_answers_is_rejected(self):
        status, body = self.post('/api/quiz/recommend', {})
        self.assertEqual(status, 400)

    def test_non_object_answers_is_rejected(self):
        status, body = self.post('/api/quiz/recommend', {'answers': 'budget'})
        self.assertEqual(status, 400)

    def test_unknown_option_codes_are_ignored(self):
        """过期或伪造的选项号不能让服务崩，也不能被当成有效答案。"""
        status, body = self.post(
            '/api/quiz/recommend',
            {'answers': {'budget': 'b2', 'scene': 'zzz', 'carry': 42, 'screen': None}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body['stage'], 'result')
        labels = [item['label'] for item in body['answer_labels']]
        self.assertEqual(len(labels), 1)

    def test_junk_answer_types_are_sanitised(self):
        status, body = self.post(
            '/api/quiz/recommend',
            {'answers': {'budget': ['b1', 'nope', 5], 'scene': {'x': 1}}},
        )
        self.assertEqual(status, 200)
        labels = [item['label'] for item in body['answer_labels']]
        self.assertEqual(len(labels), 1)

    def test_invalid_json_is_rejected(self):
        status, body = self.post('/api/quiz/recommend', raw=b'{not json')
        self.assertEqual(status, 400)

    def test_never_leaks_a_secret(self):
        status, body = self.post('/api/quiz/recommend',
                                 {'answers': self.ANSWERS, 'write': False})
        self.assertNotIn('sk-', json.dumps(body, ensure_ascii=False))


class UnconfiguredClassifierTests(WebServerTest):
    """没有注入替身、也没有配密钥时，接口要给出可读的失败，而不是 500。"""

    def setUp(self):
        super().setUp()
        server.app.state.classifier = None

    def test_reports_failure_instead_of_crashing(self):
        status, body = self.post('/api/recommend', {'text': '预算2000到3000', 'write': False})
        self.assertEqual(status, 200)
        self.assertEqual(body['stage'], 'failed')
        self.assertTrue(body['error'])

    def test_quiz_reports_failure_instead_of_crashing(self):
        status, body = self.post(
            '/api/quiz/recommend',
            {'answers': {'budget': 'b1', 'scene': 'daily', 'carry': 'light',
                         'screen': 'middle', 'storage': 's256'}, 'write': False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body['stage'], 'failed')
        self.assertTrue(body['error'])


if __name__ == '__main__':
    unittest.main()
