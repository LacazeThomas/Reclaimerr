/** Protect a hash-routed editor without allowing a canceled route to unmount it. */
export function guardUnsavedChanges(
  isDirty: () => boolean,
  confirmDiscard: () => boolean,
): () => void {
  const route = (url: string) => new URL(url).hash.split("?")[0];
  let approvedUrl: string | null = null;

  const click = (event: MouseEvent) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      event.altKey
    )
      return;
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest("a");
    if (
      !anchor ||
      anchor.hasAttribute("download") ||
      (anchor.target && anchor.target !== "_self")
    )
      return;
    const current = new URL(window.location.href);
    const target = new URL(anchor.href);
    if (
      target.origin !== current.origin ||
      target.pathname !== current.pathname ||
      target.search !== current.search ||
      !target.hash.startsWith("#/") ||
      route(target.href) === route(current.href) ||
      !isDirty()
    )
      return;
    if (confirmDiscard()) {
      approvedUrl = target.href;
    } else {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  };

  const beforeUnload = (event: BeforeUnloadEvent) => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  };

  const hashChange = (event: HashChangeEvent) => {
    const approved = approvedUrl === event.newURL;
    approvedUrl = null;
    if (approved) return;
    if (route(event.oldURL) === route(event.newURL) || !isDirty()) return;
    if (confirmDiscard()) return;
    // The router listens in the bubble phase. Stop it before it can destroy
    // the editor. pushState restores the URL without another hashchange and
    // keeps the attempted destination available via the Back button.
    event.stopImmediatePropagation();
    window.history.pushState(window.history.state, "", event.oldURL);
  };

  window.addEventListener("beforeunload", beforeUnload);
  document.addEventListener("click", click, true);
  window.addEventListener("hashchange", hashChange, true);
  return () => {
    window.removeEventListener("beforeunload", beforeUnload);
    document.removeEventListener("click", click, true);
    window.removeEventListener("hashchange", hashChange, true);
  };
}
