"""Regression coverage for the Windows-to-Linux Leaflet SRI failure."""
import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import unittest
from unittest.mock import patch
from app import create_app


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags=[]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))


class MapAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=create_app({"PUBLIC_DEPLOYMENT":True}).test_client()
        cls.tags=Tags(cls.client.get('/').get_data(as_text=True)).tags

    def assets(self):
        return [(tag,a) for tag,a in self.tags if '/vendor/leaflet-' in a.get('href',a.get('src',''))]

    def test_served_leaflet_assets_match_html_integrity(self):
        self.assertEqual(len(self.assets()),2)
        for tag,a in self.assets():
            response=self.client.get(a.get('href',a.get('src')))
            self.assertEqual(response.status_code,200)
            self.assertIn('text/css' if tag=='link' else 'javascript',response.content_type)
            digest=base64.b64encode(hashlib.sha256(response.data).digest()).decode()
            self.assertEqual(a['integrity'],'sha256-'+digest)

    def test_linux_checkout_bytes_match_integrity(self):
        # Git text normalization must not change the bytes approved by SRI.
        for _,a in self.assets():
            path=Path(a.get('href',a.get('src')).lstrip('/'))
            data=path.read_bytes()
            self.assertNotIn(b'\r',data)
            linux=data.replace(b'\r\n',b'\n')
            self.assertEqual(a['integrity'],'sha256-'+base64.b64encode(hashlib.sha256(linux).digest()).decode())
            self.assertIn('/'+path.as_posix()+' text eol=lf',Path('.gitattributes').read_text())

    def test_official_css_positioning_and_clipping_present(self):
        css=self.client.get('/static/vendor/leaflet-1.9.4/leaflet.css').get_data(as_text=True)
        for rule in ('.leaflet-pane,','.leaflet-tile,','position: absolute;','overflow: hidden;','.leaflet-container img.leaflet-tile'):
            self.assertIn(rule,css)
        # Original upstream CRLF representation is identical except line endings.
        original=css.encode().replace(b'\n',b'\r\n')
        self.assertEqual(base64.b64encode(hashlib.sha256(original).digest()).decode(),'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=')

    def test_css_order_and_deferred_script_order(self):
        links=[a['href'] for tag,a in self.tags if tag=='link' and a.get('rel')=='stylesheet']
        self.assertLess(links.index('/static/vendor/leaflet-1.9.4/leaflet.css'),links.index('/static/style.css'))
        scripts=[a for tag,a in self.tags if tag=='script']
        urls=[a['src'] for a in scripts]
        self.assertLess(urls.index('/static/vendor/leaflet-1.9.4/leaflet.js'),urls.index('/static/park-map.js'))
        self.assertTrue(all('defer' in a for a in scripts))

    def test_container_and_mobile_containment(self):
        self.assertEqual(sum(a.get('id')=='park-map' for _,a in self.tags),1)
        css=Path('static/style.css').read_text()
        for rule in ('#park-map{width:100%;max-width:100%;overflow:hidden}', '.map-layout,.map-side{min-width:0}', '#park-map{height:390px}', '#park-map{height:430px}', 'height:480px'):
            self.assertIn(rule,css)
        js=Path('static/park-map.js').read_text()
        self.assertIn('ResizeObserver',js)
        self.assertIn('map.invalidateSize({pan: false})',js)
        self.assertGreater(js.index('L.tileLayer('),js.index('load.addEventListener("click"'))

    def test_production_without_observations(self):
        with patch('app.read_current_waits',side_effect=AssertionError('Private observations read')),patch('app.read_history',side_effect=AssertionError('Private history read')):
            self.assertEqual(self.client.get('/').status_code,200)
            self.assertEqual(self.client.get('/healthz').status_code,200)
            self.assertEqual(len(self.client.get('/api/map-attractions').json['features']),7)
