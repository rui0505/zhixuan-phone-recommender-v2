from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import unittest
from starlette.testclient import TestClient
from api.app import create_app


class Document(HTMLParser):
    def __init__(self):super().__init__();self.nodes=[]
    def handle_starttag(self,tag,attrs):self.nodes.append((tag,dict(attrs)))


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(create_app()); self.addCleanup(self.client.close)
        response=self.client.get('/')
        self.assertEqual(response.status_code,200,'new HTML entrypoint not served')
        self.document=Document();self.document.feed(response.text)
    def test_forms_labels_and_all_seven_weight_controls(self):
        nodes=self.document.nodes
        self.assertTrue(any(tag=='form' and a.get('id')=='preferences' for tag,a in nodes))
        controls={a.get('id'):a for tag,a in nodes if tag in ('input','select')}
        labels={a.get('for') for tag,a in nodes if tag=='label'}
        for metric in ('weight_g','storage_gb','screen_size_inch','battery_capacity_mah','dxomark_photo','honor_of_kings_avg_fps','chipset_tier'):
            self.assertIn(metric,controls);self.assertEqual(controls[metric]['type'],'range');self.assertIn(metric,labels)
        self.assertEqual(controls['confirmed']['type'],'checkbox')
        for name in ('min-price','max-price','min-storage','include-brands','exclude-brands','target-screen','mode'):
            self.assertIn(name,controls);self.assertIn(name,labels)
    def test_navigation_and_core_containers_are_served(self):
        nodes=self.document.nodes
        self.assertEqual({a['data-view'] for tag,a in nodes if tag=='button' and 'data-view' in a},{'recommend','catalog','compare','data'})
        ids={a.get('id') for tag,a in nodes}
        self.assertTrue({'detail-dialog','compare-view','downloads','results','catalog-results'}.issubset(ids))
    def test_dependencies_are_local_and_reachable(self):
        paths=[a['src'] for tag,a in self.document.nodes if tag=='script']
        paths += [a['href'] for tag,a in self.document.nodes if tag=='link' and a.get('rel')=='stylesheet']
        # Version queries are allowed; each exact served URL is still checked below.
        resource_paths=[urlsplit(path).path for path in paths]
        self.assertIn('/app.mjs',resource_paths);self.assertIn('/styles.css',resource_paths)
        for path in paths:
            self.assertTrue(path.startswith('/')); response=self.client.get(path);self.assertEqual(response.status_code,200)
        for path in ('/render.mjs','/state.mjs','/api.mjs'):
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.get('/api/quiz').status_code,404)
        self.assertEqual(self.client.get('/data/catalog.sqlite').status_code,404)
    def test_ui_modules_are_not_reused_from_previous_deployment(self):
        for path in ('/','/app.mjs','/render.mjs','/styles.css'):
            self.assertIn('no-store',self.client.get(path).headers.get('cache-control',''))

    def test_categories_have_accessible_controls_and_explain_evidence_limits(self):
        nodes=self.document.nodes
        buttons=[a for tag,a in nodes if tag=='button' and 'data-category' in a]
        self.assertEqual({a['data-category'] for a in buttons},{'all','gaming','senior','photography'})
        self.assertTrue(all(a.get('aria-pressed') in ('true','false') for a in buttons))
        self.assertTrue(any(tag=='label' and a.get('for')=='category-status' for tag,a in nodes))
        ids={a.get('id') for tag,a in nodes}
        self.assertIn('category-methods',ids)
