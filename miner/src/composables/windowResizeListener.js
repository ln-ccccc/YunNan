export function createWindowResizeListener(handler) {
  const listener = (event) => handler(event);
  return {
    attach(target = window) {
      target.addEventListener('resize', listener);
    },
    detach(target = window) {
      target.removeEventListener('resize', listener);
    },
  };
}
