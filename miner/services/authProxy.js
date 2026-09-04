export function relayBackendResponse(res, upstream) {
  for (const cookie of upstream?.setCookies || []) {
    res.append('set-cookie', cookie);
  }
  res.status(upstream?.status || 200).json(upstream?.body || {});
}

export function requireMinerAuth({ sessionApi }) {
  return async (req, res, next) => {
    try {
      const upstream = await sessionApi(req.headers.cookie || '');
      if (upstream.status !== 200 || !upstream.body?.data?.authenticated) {
        return res.status(401).json({ success: false, code: 401, msg: '未登录' });
      }
      req.auth = upstream.body.data;
      return next();
    } catch (error) {
      return res.status(502).json({ success: false, code: 1, msg: error?.message || String(error) });
    }
  };
}
