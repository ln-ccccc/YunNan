import csv
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
import rasterio
from rasterio.transform import from_origin, from_bounds
from rasterio.warp import transform_bounds

from applications.kml_roi.change_matrix import write_change_matrix_csv


class TestChangeMatrixAlignment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def pair(self, name, values, transform=None, crs='EPSG:32647'):
        data = np.asarray(values, dtype=np.uint8)
        mask = self.root / f'{name}_mask.png'
        self.assertTrue(cv2.imwrite(str(mask), data))
        if transform is not None:
            with rasterio.open(self.root / f'{name}_label.tif', 'w', driver='GTiff',
                               width=data.shape[1], height=data.shape[0], count=1,
                               dtype='uint8', nodata=255, transform=transform, crs=crs) as dst:
                dst.write(data, 1)
        return mask

    def matrix(self):
        with (self.root / 'change_matrix_pixels.csv').open(newline='') as src:
            return np.asarray([[int(n) for n in row[1:]] for row in list(csv.reader(src))[1:]])

    def test_shifted_window_compares_ground_locations(self):
        old = self.pair('old', [[0, 1], [0, 1]], from_origin(500000, 2800000, 10, 10))
        new = self.pair('new', [[1, 4], [1, 4]], from_origin(500010, 2800000, 10, 10))
        self.assertTrue(write_change_matrix_csv(old, new, self.root))
        matrix = self.matrix()
        self.assertEqual(matrix.sum(), 2)
        self.assertEqual(matrix[1, 1], 2)

    def test_resolution_uses_new_period_grid(self):
        old = self.pair('old', [[0, 1], [4, 5]], from_origin(500000, 2800000, 20, 20))
        new = self.pair('new', [[0, 0, 1, 1], [0, 0, 1, 1], [4, 4, 5, 5], [4, 4, 5, 5]],
                        from_origin(500000, 2800000, 10, 10))
        self.assertTrue(write_change_matrix_csv(old, new, self.root))
        self.assertEqual(self.matrix().sum(), 16)
        self.assertEqual(np.diag(self.matrix()).sum(), 16)
        meta = json.loads((self.root / 'change_matrix_alignment.json').read_text())
        self.assertTrue(meta['spatial_alignment_verified'])
        self.assertEqual(meta['target_grid']['width'], 4)
        self.assertEqual(meta['resampling'], 'nearest')

    def test_different_crs_maps_to_new_grid(self):
        old = self.pair('old', [[0, 1, 4, 5]] * 4, from_origin(500000, 2800000, 10, 10))
        bounds = transform_bounds('EPSG:32647', 'EPSG:4326', 500010, 2799960, 500050, 2800000)
        new = self.pair('new', np.full((4, 4), 5), from_bounds(*bounds, 4, 4), crs='EPSG:4326')
        self.assertTrue(write_change_matrix_csv(old, new, self.root))
        self.assertEqual(self.matrix().sum(), 12)
        self.assertEqual(self.matrix()[1, 5], 4)
        self.assertEqual(self.matrix()[0, 5], 0)

    def test_nodata_excluded_from_common_coverage(self):
        transform = from_origin(500000, 2800000, 10, 10)
        old = self.pair('old', [[0, 255], [1, 4]], transform)
        new = self.pair('new', [[1, 2], [255, 4]], transform)
        self.assertTrue(write_change_matrix_csv(old, new, self.root))
        self.assertEqual(self.matrix().sum(), 2)

    def test_nonoverlap_clears_stale_matrix(self):
        old = self.pair('old', [[0, 1]], from_origin(500000, 2800000, 10, 10))
        new = self.pair('new', [[1, 4]], from_origin(510000, 2800000, 10, 10))
        for name in ('change_matrix_pixels.csv', 'change_matrix_percent_rownorm.csv', 'change_matrix_alignment.json'):
            (self.root / name).write_text('stale')
        self.assertFalse(write_change_matrix_csv(old, new, self.root))
        self.assertFalse((self.root / 'change_matrix_pixels.csv').exists())

    def test_one_label_does_not_fall_back_to_png(self):
        old = self.pair('old', [[0, 1]], from_origin(500000, 2800000, 10, 10))
        new = self.pair('new', [[1, 4]])
        with self.assertRaisesRegex(ValueError, '两期.*地理参考'):
            write_change_matrix_csv(old, new, self.root)

    def test_missing_crs_rejected(self):
        old = self.pair('old', [[0, 1]], from_origin(500000, 2800000, 10, 10), crs=None)
        new = self.pair('new', [[1, 4]], from_origin(500000, 2800000, 10, 10))
        with self.assertRaisesRegex(ValueError, 'CRS'):
            write_change_matrix_csv(old, new, self.root)

    def test_legacy_different_size_rejected(self):
        old = self.pair('old', [[0, 1]])
        new = self.pair('new', [[1, 4], [1, 4]])
        with self.assertRaisesRegex(ValueError, '尺寸不同'):
            write_change_matrix_csv(old, new, self.root)

    def test_legacy_equal_size_marked_unverified(self):
        old = self.pair('old', [[0, 1]])
        new = self.pair('new', [[1, 4]])
        self.assertTrue(write_change_matrix_csv(old, new, self.root))
        meta = json.loads((self.root / 'change_matrix_alignment.json').read_text())
        self.assertEqual(meta['mode'], 'legacy_png')
        self.assertFalse(meta['spatial_alignment_verified'])

    def test_publish_keeps_classification_without_misleading_matrix(self):
        from applications.project_hub.inference_results import _prepare_fid_publication
        target = self.root / 'outputs' / '101'
        stage = self.root / 'stage'
        target.mkdir(parents=True)
        stage.mkdir()
        old = self.pair('101+2020', [[0, 1]], from_origin(500000, 2800000, 10, 10))
        old.rename(target / old.name)
        (self.root / '101+2020_label.tif').rename(target / '101+2020_label.tif')
        (target / 'change_matrix_pixels.csv').write_text('stale')
        new = self.pair('101+2021', [[1, 4]])
        new.rename(stage / new.name)
        self.assertTrue(cv2.imwrite(str(stage / '101+2021.png'), np.full((1, 2, 3), 120, dtype=np.uint8)))
        publication, years, copied = _prepare_fid_publication(1, 101, stage, target)
        self.assertEqual(len(years), 2)
        self.assertIn('101+2021.png', copied)
        self.assertFalse((publication / 'change_matrix_pixels.csv').exists())
        meta = json.loads((publication / 'change_matrix_alignment.json').read_text())
        self.assertEqual(meta['mode'], 'unavailable')
        self.assertIn('地理参考', meta['reason'])
