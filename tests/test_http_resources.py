"""Real loopback errors must release response resources, including retries."""
import unittest
from fake_openai import FakeOpenAIServer
from providers.llm import post_chat


class HttpResourceTests(unittest.TestCase):
    def test_failed_http_response_is_closed_before_return(self):
        with FakeOpenAIServer(status=500) as server:
            body,error=post_chat(server.base_url+'/chat/completions',{'messages':[]},'fake',retries=0)
            self.assertIsNone(body);self.assertIsNotNone(error)
            try:self.assertTrue(error.fp.closed)
            finally:error.close()
