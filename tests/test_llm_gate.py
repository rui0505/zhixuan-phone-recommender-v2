import os
from unittest.mock import patch
import unittest
from fake_openai import FakeOpenAIServer
from needs_extraction import extract_needs
from providers.recommend import Brief,write_recommendation


class LlmGateTests(unittest.TestCase):
    def test_default_environment_makes_no_live_call_even_with_existing_key(self):
        with FakeOpenAIServer() as server,patch.dict(os.environ,{'PHONE_ENABLE_LLM':'false'},clear=False):
            extract_needs('预算1000到3000')
            write_recommendation(Brief(models=[{'name':'合成机','price':1000}]))
            self.assertEqual(server.requests,[])
    def test_enabled_flag_without_approved_budget_still_makes_no_live_call(self):
        with FakeOpenAIServer() as server,patch.dict(os.environ,{'PHONE_ENABLE_LLM':'true','PHONE_LLM_BUDGET_APPROVED':'false'},clear=False):
            extract_needs('预算1000到3000')
            self.assertEqual(server.requests,[])
    def test_legacy_provider_error_does_not_disclose_secret(self):
        class Writer:
            def write(self,brief):raise RuntimeError('SECRET_NOT_PUBLIC')
        text,error=write_recommendation(Brief(models=[{'name':'合成机'}]),writer=Writer())
        self.assertNotIn('SECRET_NOT_PUBLIC',text+error)
    def test_legacy_render_does_not_turn_missing_values_into_zero(self):
        from providers.recommend import render_facts,fallback_text
        brief=Brief(models=[{'name':'合成机','price':None,'weight':None}])
        self.assertIn('重量: 缺失',render_facts(brief))
        self.assertIn('价格缺失',fallback_text(brief))
    def test_unavailable_classifier_does_not_disclose_upstream_secret(self):
        from providers.jev import JevUnavailable
        class Classifier:
            def classify(self,text):raise JevUnavailable('SECRET_NOT_PUBLIC')
        result=extract_needs('预算1000到3000',classifier=Classifier())
        self.assertEqual(result['status'],'failed')
        self.assertNotIn('SECRET_NOT_PUBLIC',str(result))
    def test_fake_http_never_receives_existing_real_configuration_key(self):
        from providers.llm import LLMClassifier
        with patch.dict(os.environ,{'LLM_API_KEY':'SECRET_SYNTHETIC_PREEXISTING'},clear=False):
            with FakeOpenAIServer() as server:
                LLMClassifier().classify('预算1000到3000')
                self.assertEqual(server.requests[0]['authorization'],'Bearer sk-fake-local-no-real-key')
            self.assertEqual(os.environ['LLM_API_KEY'],'SECRET_SYNTHETIC_PREEXISTING')
