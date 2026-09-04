export function buildBackendBaseUrl({ protocol, hostname, port }) {
  return `${protocol}//${hostname}:${port}/`;
}
