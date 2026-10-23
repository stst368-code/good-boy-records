/* One desktop density for DOM controls and screen-space canvas geometry.
   Use CSS viewport pixels, not devicePixelRatio: OS/browser scaling already
   accounts for pixel density. Phones retain their dedicated layout. */
(() => {
  const root = document.documentElement;
  let scale = 1;
  function update() {
    scale = innerWidth <= 900 ? 1 : Math.max(1, Math.min(2.5, innerWidth / 1920, innerHeight / 1080));
    root.style.setProperty('--display-scale', scale);
    root.style.setProperty('--display-height', `${innerHeight / scale}px`);
    root.style.setProperty('--display-width', `${innerWidth / scale}px`);
    root.style.setProperty('--playing-cover-size', `${innerWidth <= 900 ? 252 : 360 * scale}px`);
  }
  window.GBRDisplay = Object.freeze({
    get scale() { return scale; },
    get coverSize() { return innerWidth <= 900 ? 252 : 360 * scale; },
    get lyricHeight() { return innerWidth <= 900 ? 96 : 58 * scale; },
  });
  update();
  addEventListener('resize', update);
})();
