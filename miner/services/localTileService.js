import fs from 'node:fs';
import path from 'node:path';

const EMPTY_TILE_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAAGklEQVR4nO3BMQEAAADCoPdPbQ8HFAAAAAAAAAAA8G4wQAABiwCo9wAAAABJRU5ErkJggg==';

export function buildEmptyTilePng() {
  return Buffer.from(EMPTY_TILE_PNG_BASE64, 'base64');
}

export function resolveLocalTilePath(tileRoot, z, x, y) {
  const candidate = path.join(tileRoot, String(z), String(x), `${y}.png`);
  return fs.existsSync(candidate) ? candidate : null;
}
