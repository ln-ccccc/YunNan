<script>
import { buildBackendBaseUrl } from '@/utils/backendUrl.mjs';

const BASEURL = buildBackendBaseUrl({
  protocol: window.location.protocol,
  hostname: window.location.hostname,
  port: process.env.VUE_APP_BACKEND_PORT,
});

export default {
  BASEURL,
};
</script>
