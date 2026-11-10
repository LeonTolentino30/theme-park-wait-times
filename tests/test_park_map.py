import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import create_app
from src import park_map
from src.collection_pilot import APPROVED


class MapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app({"PUBLIC_DEPLOYMENT": True}).test_client()

    def test_exact_approved_active_attractions(self):
        data = park_map.map_catalog()
        self.assertEqual({f["id"] for f in data["features"]}, APPROVED)
        self.assertEqual(len(data["features"]), 7)
        self.assertNotIn("Rockit", json.dumps(data))
        self.assertEqual(data["license"], "ODbL-1.0")

    def test_verified_source_coordinates(self):
        expected = {17295822: (-81.4724911,28.471099),8248132721: (-81.4735089,28.4734362),8248062617: (-81.4687757,28.4715017),2453688: (-81.4696594,28.4700548),17297382: (-81.4739481,28.4718757),4694456530: (-81.4699854,28.4768628),5642698838: (-81.4684387,28.4764)}
        for f in park_map.map_catalog()["features"]:
            p=f["properties"]
            self.assertEqual(tuple(f["geometry"]["coordinates"]),expected[p["osm_id"]])
            self.assertEqual(p["source_url"],f"https://www.openstreetmap.org/{p['osm_type']}/{p['osm_id']}")
            self.assertTrue(p["coordinate_kind"])

    def test_park_filters(self):
        for park,count in (("all",7),("islands",5),("studios",2)):
            response=self.client.get('/api/map-attractions',query_string={"park":park})
            self.assertEqual(response.status_code,200)
            features=response.json["features"]
            self.assertEqual(len(features),count)
            if park!="all":self.assertTrue(all(f["properties"]["park"]==park for f in features))

    def test_invalid_filters(self):
        for query in ("park=unknown","park=","park=all&park=islands","secret=1","park=../private"):
            self.assertEqual(self.client.get('/api/map-attractions?'+query).status_code,400)

    def test_missing_catalog_safe(self):
        with patch.object(park_map,'DATA',Path(tempfile.gettempdir())/'queue-atlas-missing-map-catalog.geojson'):
            response=self.client.get('/api/map-attractions')
            self.assertEqual(response.status_code,503)
            self.assertNotIn(str(park_map.DATA),response.get_data(as_text=True))
            self.assertEqual(self.client.get('/').status_code,200)

    def test_invalid_coordinates_rejected(self):
        for coordinates in ([0,0],[True,28.47],[-81.47],[-81.47,91]):
            data=park_map.map_catalog()
            data['features'][0]['geometry']['coordinates']=coordinates
            with patch.object(Path,'read_text',return_value=json.dumps(data)):
                with self.assertRaises((ValueError,TypeError)):park_map.map_catalog()

    def test_rendering_and_existing_features(self):
        html=self.client.get('/').get_data(as_text=True)
        self.assertEqual(html.count('data-map-id='),7)
        for text in ('prediction-form','current-waits','history','Load interactive map','OpenStreetMap contributors','No live waits','synthetic estimate','data-map-park="studios"'):
            self.assertIn(text,html)

    def test_public_metadata_no_observations(self):
        with patch('app.read_current_waits',side_effect=AssertionError('No database access')),patch('app.read_history',side_effect=AssertionError('No database access')):
            self.assertEqual(self.client.get('/api/map-attractions').status_code,200)
            self.assertEqual(self.client.get('/').status_code,200)
        data=self.client.get('/api/map-attractions').get_data(as_text=True)
        for text in ('standby','posted_wait','sqlite','C:\\Users','fetch_timestamp'):
            self.assertNotIn(text,data)

    def test_hidden_filters_override_button_styles(self):
        self.assertIn(".park-map-section [hidden]{display:none!important}",Path("static/style.css").read_text())

    def test_security_and_tile_policy(self):
        response=self.client.get('/')
        self.assertEqual(response.headers['Referrer-Policy'],'strict-origin-when-cross-origin')
        self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
        self.assertIn('https://tile.openstreetmap.org',response.headers['Content-Security-Policy'])
        self.assertEqual(park_map.map_catalog()['tiles']['url'],'https://tile.openstreetmap.org/{z}/{x}/{y}.png')
        self.assertEqual(self.client.get('/api/map-attractions').headers['Referrer-Policy'],'no-referrer')

    def test_vendored_leaflet_integrity(self):
        expected={'leaflet.js':'20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=','leaflet.css':'p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY='}
        import base64
        for name,digest in expected.items():
            self.assertEqual(base64.b64encode(hashlib.sha256((Path('static/vendor/leaflet-1.9.4')/name).read_bytes()).digest()).decode(),digest)
        self.assertIn('Redistribution',Path('static/vendor/leaflet-1.9.4/LICENSE').read_text())
