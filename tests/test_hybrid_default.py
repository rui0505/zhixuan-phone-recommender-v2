from pathlib import Path
import unittest
from starlette.testclient import TestClient
from api.app import create_app

class HybridDefaultTests(unittest.TestCase):
    def test_default_request_and_rejected_standalone_topsis(self):
        with TestClient(create_app()) as client:
            meta=client.get('/api/v1/meta').json()
            self.assertEqual(meta['defaults'].get('method'), 'hybrid_rrf')
            body={**meta['defaults'], 'needs':{'budget':{'min':None,'max':None},'min_storage':None,'brands':{'include':[],'exclude':[]}},'confirmed':True,'expected_release':meta['release']}
            body.pop('method',None)
            implicit=client.post('/api/v1/recommend',json=body)
            self.assertEqual(implicit.status_code,200,implicit.text)
            self.assertEqual(implicit.json()['method'],'hybrid_rrf')
            explicit=client.post('/api/v1/recommend',json={**body,'method':'hybrid_rrf'})
            self.assertEqual(implicit.json()['ranking_sha256'],explicit.json()['ranking_sha256'])
            topsis=client.post('/api/v1/recommend',json={**body,'method':'topsis'})
            self.assertEqual(topsis.status_code,422)
            self.assertEqual([a['method'] for a in meta['algorithms']],['hybrid_rrf'])
    def test_page_selects_combination(self):
        from html.parser import HTMLParser
        class Options(HTMLParser):
            def __init__(self):super().__init__();self.selected=[]
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if tag=='option' and 'selected' in attrs:self.selected.append(attrs.get('value'))
        parser=Options();parser.feed(Path('frontend/index.html').read_text(encoding='utf-8'))
        self.assertIn('hybrid_rrf',parser.selected)
        self.assertFalse('value="topsis"' in Path('frontend/index.html').read_text(encoding='utf-8'))
