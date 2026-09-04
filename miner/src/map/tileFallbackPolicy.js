export function createTileFallbackState() {
  return {
    hasSuccessfulTileLoad: false,
    consecutiveInitialErrors: 0,
  };
}

export function noteTileLoad(state) {
  state.hasSuccessfulTileLoad = true;
  state.consecutiveInitialErrors = 0;
}

export function noteTileError(state, options = {}) {
  const threshold = Number(options.initialErrorThreshold || 4);
  if (state.hasSuccessfulTileLoad) return false;
  state.consecutiveInitialErrors += 1;
  return state.consecutiveInitialErrors >= threshold;
}
