import { Router } from 'express';

import { fetchSpectralLive } from '../services/geoviewBackend.js';

const router = Router();

router.get('/spectral_live/:fid', async (req, res) => {
  try {
    const upstream = await fetchSpectralLive(req.params.fid);
    res.status(upstream.status);
    res.type(upstream.contentType);
    res.send(upstream.body);
  } catch (error) {
    res.status(502).json({
      error: 'Failed to proxy GeoView spectral live data',
      detail: error?.message || String(error),
      next: 'Check GEOVIEW_BACKEND_URL and backend service health'
    });
  }
});

export default router;
