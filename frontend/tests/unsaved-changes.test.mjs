import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/lib/utils/unsaved-changes.ts", import.meta.url),
  "utf8",
);
const js = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { guardUnsavedChanges } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);

function withGuard(callback) {
  const original = {
    window: globalThis.window,
    document: globalThis.document,
    Element: globalThis.Element,
  };
  function surface() {
    const listeners = [];
    return {
      listeners,
      addEventListener(type, fn, capture = false) {
        listeners.push({ type, fn, capture });
      },
      removeEventListener(type, fn, capture = false) {
        const index = listeners.findIndex(
          (item) =>
            item.type === type && item.fn === fn && item.capture === capture,
        );
        if (index >= 0) listeners.splice(index, 1);
      },
      dispatch(type, values = {}) {
        const event = {
          prevented: false,
          stopped: false,
          preventDefault() {
            this.prevented = true;
          },
          stopImmediatePropagation() {
            this.stopped = true;
          },
          ...values,
        };
        for (const item of [...listeners]
          .filter((item) => item.type === type)
          .sort((a, b) => Number(b.capture) - Number(a.capture))) {
          item.fn(event);
          if (event.stopped) break;
        }
        return event;
      },
    };
  }
  const window = surface();
  const document = surface();
  const oldURL = "http://reclaimerr.test/#/rules";
  const newURL = "http://reclaimerr.test/#/storage";
  const restored = [];
  window.location = { href: oldURL };
  window.history = {
    state: { previous: "state" },
    pushState: (...args) => restored.push(args),
  };
  class Element {
    closest() {
      return this.anchor;
    }
  }
  Object.assign(globalThis, { window, document, Element });
  let dirty = false;
  let discard = false;
  let prompts = 0;
  const cleanup = guardUnsavedChanges(
    () => dirty,
    () => {
      prompts++;
      return discard;
    },
  );
  const link = () => {
    const target = new Element();
    target.anchor = { href: newURL, target: "", hasAttribute: () => false };
    return { target, button: 0 };
  };
  try {
    callback({
      window,
      document,
      oldURL,
      newURL,
      restored,
      cleanup,
      link,
      dirty: (value) => (dirty = value),
      discard: (value) => (discard = value),
      prompts: () => prompts,
    });
  } finally {
    cleanup();
    Object.assign(globalThis, original);
  }
}

test("clean editors navigate and reload without prompts", () =>
  withGuard(({ window, oldURL, newURL, prompts, restored }) => {
    assert.equal(
      window.dispatch("hashchange", { oldURL, newURL }).stopped,
      false,
    );
    assert.equal(window.dispatch("beforeunload").prevented, false);
    assert.equal(prompts(), 0);
    assert.deepEqual(restored, []);
  }));

test("canceling browser/programmatic hash navigation preserves the editor before the router runs", () =>
  withGuard(({ window, oldURL, newURL, dirty, prompts, restored }) => {
    dirty(true);
    let routerCalls = 0;
    window.addEventListener("hashchange", () => routerCalls++);
    assert.equal(
      window.dispatch("hashchange", { oldURL, newURL }).stopped,
      true,
    );
    assert.equal(routerCalls, 0);
    assert.equal(prompts(), 1);
    assert.deepEqual(restored, [[{ previous: "state" }, "", oldURL]]);
  }));

test("approving hash navigation allows the router to leave", () =>
  withGuard(({ window, oldURL, newURL, dirty, discard, restored }) => {
    dirty(true);
    discard(true);
    assert.equal(
      window.dispatch("hashchange", { oldURL, newURL }).stopped,
      false,
    );
    assert.deepEqual(restored, []);
  }));

test("canceling a sidebar link prevents changing the URL", () =>
  withGuard(({ document, link, dirty, prompts }) => {
    dirty(true);
    const event = document.dispatch("click", link());
    assert.equal(event.prevented, true);
    assert.equal(event.stopped, true);
    assert.equal(prompts(), 1);
  }));

test("approved links prompt only once, and modified clicks do not discard this editor", () =>
  withGuard(
    ({ window, document, oldURL, newURL, link, dirty, discard, prompts }) => {
      dirty(true);
      discard(true);
      document.dispatch("click", { ...link(), ctrlKey: true });
      assert.equal(prompts(), 0);
      assert.equal(document.dispatch("click", link()).prevented, false);
      assert.equal(
        window.dispatch("hashchange", { oldURL, newURL }).stopped,
        false,
      );
      assert.equal(prompts(), 1);
    },
  ));

test("dirty reloads warn, reverted changes do not, and cleanup removes all handlers", () =>
  withGuard(({ window, document, dirty, cleanup }) => {
    dirty(true);
    const event = window.dispatch("beforeunload");
    assert.equal(event.prevented, true);
    assert.equal(event.returnValue, "");
    dirty(false);
    assert.equal(window.dispatch("beforeunload").prevented, false);
    cleanup();
    assert.equal(window.listeners.length, 0);
    assert.equal(document.listeners.length, 0);
  }));

test("query-only changes within Rules keep the editor without a discard prompt", () =>
  withGuard(({ window, oldURL, dirty, prompts }) => {
    dirty(true);
    window.dispatch("hashchange", { oldURL, newURL: oldURL + "?view=all" });
    assert.equal(prompts(), 0);
  }));
