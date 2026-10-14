import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Compile the same TypeScript player used by the login page for Node's test runner.
const source = await readFile(
  new URL("../src/lib/utils/login-background.ts", import.meta.url),
  "utf8",
);
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { createLoginBackgroundPlayer } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);

async function withPlayer(config, broken, callback) {
  const original = {
    Image: globalThis.Image,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  };
  const timers = new Map();
  const requested = [];
  let id = 0;
  globalThis.setTimeout = (fn, ms) => {
    timers.set(++id, { fn, ms });
    return id;
  };
  globalThis.clearTimeout = (key) => timers.delete(key);
  globalThis.Image = class {
    set src(url) {
      if (!url) return;
      requested.push(url);
      queueMicrotask(() =>
        broken.includes(url) ? this.onerror?.() : this.onload?.(),
      );
    }
  };
  const element = { style: {} };
  let defaults = 0;
  const player = createLoginBackgroundPlayer(
    element,
    { interval_seconds: 5, urls: [], image_url: null, ...config },
    async () => {
      defaults++;
      return ["https://default.test/a.jpg", "https://default.test/b.jpg"];
    },
  );
  const fire = async (ms) => {
    const entry = [...timers].find(([, timer]) => timer.ms === ms);
    assert.ok(entry, "Expected a timer at " + ms);
    timers.delete(entry[0]);
    entry[1].fn();
    for (let i = 0; i < 10; i++) await Promise.resolve();
  };
  try {
    await callback({
      player,
      element,
      timers,
      requested,
      fire,
      defaults: () => defaults,
    });
  } finally {
    player.stop();
    Object.assign(globalThis, original);
  }
}

test("one custom URL stays static and preserves arbitrary URL characters", async () => {
  const url = 'https://images.test/a.jpg?label="hello"&size=large';
  await withPlayer(
    { mode: "urls", urls: [url] },
    [],
    async ({ player, element, timers, fire, defaults }) => {
      await player.start();
      await fire(500);
      assert.equal(
        element.style.backgroundImage,
        "url(" + JSON.stringify(url) + ")",
      );
      assert.equal(timers.size, 0);
      assert.equal(defaults(), 0);
    },
  );
});

test("custom URLs rotate in order and repeat at the chosen interval", async () => {
  const urls = ["https://images.test/a.jpg", "https://images.test/b.jpg"];
  await withPlayer(
    { mode: "urls", urls, interval_seconds: 12 },
    [],
    async ({ player, requested, fire }) => {
      await player.start();
      await fire(500);
      await fire(12000);
      await fire(500);
      await fire(12000);
      assert.deepEqual(requested, [urls[0], urls[1], urls[0]]);
    },
  );
});

test("broken custom images are skipped without replacing good custom URLs", async () => {
  await withPlayer(
    {
      mode: "urls",
      urls: ["https://bad.test/a.jpg", "https://good.test/a.jpg"],
    },
    ["https://bad.test/a.jpg"],
    async ({ player, requested, defaults, fire, element }) => {
      await player.start();
      await fire(500);
      assert.deepEqual(requested, [
        "https://bad.test/a.jpg",
        "https://good.test/a.jpg",
      ]);
      assert.match(element.style.backgroundImage, /good.test/);
      assert.equal(defaults(), 0);
    },
  );
});

test("unavailable upload falls back to default rotation", async () => {
  await withPlayer(
    { mode: "upload", image_url: "/static/missing.jpg" },
    ["/static/missing.jpg"],
    async ({ player, requested, defaults, timers }) => {
      await player.start();
      assert.equal(defaults(), 1);
      assert.equal(requested[1], "https://default.test/a.jpg");
      assert.ok([...timers.values()].some((timer) => timer.ms === 5000));
    },
  );
});

test("leaving login cancels rotation and pending fade", async () => {
  await withPlayer(
    {
      mode: "urls",
      urls: ["https://images.test/a.jpg", "https://images.test/b.jpg"],
    },
    [],
    async ({ player, timers, element }) => {
      await player.start();
      player.stop();
      assert.equal(timers.size, 0);
      assert.equal(element.style.backgroundImage, undefined);
    },
  );
});
