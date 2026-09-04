import { resolveViewFromHash } from '../navigation/viewNavigation.js';

export function resolveInitialView({ authenticated, hash }) {
  if (!authenticated) {
    return 'login';
  }
  return resolveViewFromHash(hash);
}
