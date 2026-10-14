import type { LoginBackground } from "$lib/types/login-background";

export function createLoginBackgroundPlayer(
  element: HTMLElement,
  config: LoginBackground,
  getDefaultUrls: () => Promise<string[]>,
) {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let fadeTimer: ReturnType<typeof setTimeout> | undefined;
  let cancelLoad: (() => void) | undefined;
  let index = 0;
  let usingCustom = config.mode !== "default";
  let urls =
    config.mode === "upload"
      ? config.image_url
        ? [config.image_url]
        : []
      : config.mode === "urls"
        ? [...config.urls]
        : [];
  const failed = new Set<string>();
  const interval =
    Math.min(300, Math.max(5, config.interval_seconds || 5)) * 1000;

  function load(url: string): Promise<boolean> {
    return new Promise((resolve) => {
      const image = new Image();
      let finished = false;
      const done = (ok: boolean) => {
        if (finished) return;
        finished = true;
        clearTimeout(timeout);
        image.onload = null;
        image.onerror = null;
        cancelLoad = undefined;
        if (!ok) image.src = "";
        resolve(ok);
      };
      const timeout = setTimeout(() => done(false), 5000);
      cancelLoad = () => done(false);
      image.referrerPolicy = "no-referrer";
      image.onload = () => done(true);
      image.onerror = () => done(false);
      image.src = url;
    });
  }

  async function next() {
    if (stopped) return;
    if (!usingCustom && index >= urls.length) {
      urls = [];
      failed.clear();
    }
    if (!urls.length) {
      usingCustom = false;
      urls = await getDefaultUrls().catch(() => []);
      index = 0;
    }
    if (stopped) return;
    const count = urls.length;
    for (let attempt = 0; attempt < count; attempt++) {
      const url = urls[index++ % urls.length];
      if (failed.has(url)) continue;
      const loaded = await load(url);
      if (stopped) return;
      if (!loaded) {
        failed.add(url);
        continue;
      }
      element.style.opacity = "0";
      fadeTimer = setTimeout(() => {
        if (stopped) return;
        element.style.backgroundImage = "url(" + JSON.stringify(url) + ")";
        element.style.opacity = "1";
      }, 500);
      if (urls.filter((item) => !failed.has(item)).length > 1) {
        timer = setTimeout(() => void next(), interval);
      }
      return;
    }
    if (usingCustom) {
      usingCustom = false;
      urls = [];
      await next();
    } else {
      // A transient failure must not leave the default rotation stopped forever.
      failed.clear();
      urls = [];
      timer = setTimeout(() => void next(), interval);
    }
  }

  return {
    start: next,
    stop() {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(fadeTimer);
      cancelLoad?.();
    },
  };
}
