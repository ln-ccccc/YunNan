import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import create_project


class TestMineAreaUnits(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PROJECT_STORAGE_ROOT': self.temp.name})
        self.env.start()
        self.app = create_app('testing')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.pid = create_project({'name': '面积单位验收', 'region': '云南'})['id']
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['admin_user_id'] = 1
        self.fixture = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/project-hub-v1/mine-area-units.json').read_text(encoding='utf-8'))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.env.stop()
        self.temp.cleanup()

    def _import(self, sample):
        geometry = {'type': 'Polygon', 'coordinates': [[[102, 25], [102.1, 25], [102.1, 25.1], [102, 25]]]}
        source = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'FID_1': 7503, 'area': sample['area']}, 'geometry': geometry}]}
        text = json.dumps(source)
        payload = {'filename': 'mine.geojson', 'content': text, 'field_mapping': {'fid': 'FID_1', 'area': 'area'}}
        if 'area_unit' in sample:
            payload['area_unit'] = sample['area_unit']
        return self.client.post(f'/api/projects/{self.pid}/spatial/mines', json=payload), text, source

    def test_km2_is_converted_once_and_source_is_preserved(self):
        sample = self.fixture['success']
        response, text, source = self._import(sample)
        body = response.get_json()
        self.assertTrue(body['success'], body)
        project = Project.query.get(self.pid)
        self.assertEqual(project.mines[0].area_snapshot, sample['expected_m2'])
        resource = body['data']['resource']
        original = Path(self.temp.name) / ProjectSpatialResource.query.get(resource['id']).source_path
        self.assertEqual(original.read_text(encoding='utf-8'), text)
        normalized = self.client.get(f'/api/projects/{self.pid}/geojson').get_json()['data']['features'][0]
        self.assertEqual(normalized['properties']['area'], sample['area'])
        self.assertEqual(normalized['properties']['area_m2'], sample['expected_m2'])
        self.assertEqual(normalized['geometry'], source['features'][0]['geometry'])
        stats = self.client.get(f'/api/projects/{self.pid}/stats').get_json()['data']
        self.assertEqual(stats['mineAreaTotal'], sample['expected_m2'])
        activity = ProjectActivityLog.query.filter_by(project_id=self.pid, event_type='spatial_resource_activated').first()
        self.assertEqual(json.loads(activity.payload_json)['area_unit'], 'km2')

    def test_omitted_unit_retains_legacy_m2(self):
        response, _, _ = self._import(self.fixture['legacy'])
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(Project.query.get(self.pid).mines[0].area_snapshot, 282700.0)

    def test_missing_area_remains_unavailable(self):
        response, _, _ = self._import(self.fixture['no_data'])
        self.assertTrue(response.get_json()['success'])
        self.assertIsNone(Project.query.get(self.pid).mines[0].area_snapshot)

    def test_unmapped_raw_area_is_not_used_as_m2(self):
        response, text, _ = self._import(self.fixture['success'])
        self.assertTrue(response.get_json()['success'])
        response = self.client.post(f'/api/projects/{self.pid}/spatial/mines', json={
            'filename': 'unmapped.geojson', 'content': text, 'area_unit': 'm2',
            'field_mapping': {'fid': 'FID_1', 'area': None}})
        self.assertTrue(response.get_json()['success'])
        normalized = self.client.get(f'/api/projects/{self.pid}/geojson').get_json()['data']['features'][0]
        self.assertEqual(normalized['properties']['area'], 0.2827)
        self.assertIsNone(normalized['properties']['area_m2'])
        stats = self.client.get(f'/api/projects/{self.pid}/stats').get_json()['data']
        self.assertEqual(stats['mineAreaTotal'], 0)
        self.assertEqual(stats['areaStats'], {'small': 0, 'medium': 0, 'large': 0})

    def test_bad_unit_or_nonfinite_area_creates_no_resource(self):
        for sample in (self.fixture['parameter_error'], self.fixture['invalid_area'], {'area': -1, 'area_unit': 'm2'}):
            with self.subTest(sample=sample):
                response, _, _ = self._import(sample)
                self.assertFalse(response.get_json()['success'])
                self.assertEqual(Project.query.get(self.pid).spatial_resources, [])


if __name__ == '__main__':
    unittest.main()
