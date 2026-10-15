<script lang="ts">
  import { untrack } from "svelte";
  import { get_api } from "$lib/api";
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Dialog from "$lib/components/ui/dialog/index.js";
  import { Input } from "$lib/components/ui/input/index.js";
  import type {
    MediaType,
    MetadataValueLookup,
    PaginatedMetadataValuesResponse,
  } from "$lib/types/shared";

  interface Props {
    open?: boolean;
    mediaType: MediaType;
    endpoint: "/api/rules/original-languages" | "/api/rules/origin-countries";
    title: string;
    description: string;
    searchPlaceholder: string;
    emptyLabel: string;
    initialSelectedValues?: string[];
    onApply: (values: string[]) => void;
  }

  let {
    open = $bindable(false),
    mediaType,
    endpoint,
    title,
    description,
    searchPlaceholder,
    emptyLabel,
    initialSelectedValues = [],
    onApply,
  }: Props = $props();

  const perPage = 50;
  let query = $state("");
  let loading = $state(false);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let items = $state<MetadataValueLookup[]>([]);
  let page = $state(1);
  let totalPages = $state(0);
  let selectedValues = $state<string[]>([]);
  let searchDebounce: ReturnType<typeof setTimeout> | null = null;
  let wasOpen = $state(false);

  const hasMore = $derived(page < totalPages);

  const loadValues = async (
    nextPage: number,
    opts: { append?: boolean; search?: string } = {},
  ) => {
    const append = opts.append ?? false;
    const needle = (opts.search ?? "").trim();
    if (!open) return;
    if (append) loadingMore = true;
    else loading = true;
    error = null;
    try {
      const params = new URLSearchParams();
      params.set("media_type", mediaType);
      params.set("page", String(nextPage));
      params.set("per_page", String(perPage));
      if (needle) params.set("q", needle);

      const response = await get_api<PaginatedMetadataValuesResponse>(
        `${endpoint}?${params.toString()}`,
      );
      page = response.page;
      totalPages = response.total_pages;
      items = append ? [...items, ...response.items] : response.items;
    } catch (e: any) {
      error = e.message ?? `Failed to load ${title.toLowerCase()}.`;
      if (!append) items = [];
    } finally {
      loading = false;
      loadingMore = false;
    }
  };

  const isSelected = (value: string) => selectedValues.includes(value);

  const toggleSelected = (value: string) => {
    selectedValues = selectedValues.includes(value)
      ? selectedValues.filter((item) => item !== value)
      : [...selectedValues, value];
  };

  const applySelection = () => {
    onApply([
      ...new Set(selectedValues.map((value) => value.trim()).filter(Boolean)),
    ]);
    open = false;
  };

  const handleQueryInput = (value: string) => {
    query = value;
    if (searchDebounce) clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      void loadValues(1, { search: value });
    }, 250);
  };

  const loadMore = () => {
    if (!hasMore || loadingMore || loading) return;
    void loadValues(page + 1, { append: true, search: query });
  };

  $effect(() => {
    if (open && !wasOpen) {
      selectedValues = [
        ...new Set(
          untrack(() => initialSelectedValues)
            .map((item) => item.trim())
            .filter(Boolean),
        ),
      ];
      query = "";
      page = 1;
      totalPages = 0;
      items = [];
      void untrack(() => loadValues(1));
    }
    if (!open && searchDebounce) {
      clearTimeout(searchDebounce);
      searchDebounce = null;
    }
    wasOpen = open;
  });
</script>

<Dialog.Root bind:open>
  <Dialog.Content
    class="sm:max-w-2xl h-[min(90vh,44rem)] max-h-[90vh] p-0 flex flex-col overflow-hidden border-ring border-2 text-foreground"
    onInteractOutside={(event) => event.preventDefault()}
  >
    <Dialog.Header class="px-6 pt-5 pb-3 shrink-0 border-b border-border">
      <Dialog.Title>{title}</Dialog.Title>
      <Dialog.Description>{description}</Dialog.Description>
    </Dialog.Header>

    <div class="flex-1 overflow-y-auto px-6 py-4 space-y-4 min-h-0">
      <Input
        type="text"
        placeholder={searchPlaceholder}
        value={query}
        oninput={(event) => handleQueryInput(event.currentTarget.value)}
      />

      <div class="rounded-md border border-border overflow-y-auto min-h-0">
        {#if loading}
          <p class="p-3 text-sm text-muted-foreground">Loading...</p>
        {:else if error}
          <p class="p-3 text-sm text-destructive">{error}</p>
        {:else if items.length === 0}
          <p class="p-3 text-sm text-muted-foreground">{emptyLabel}</p>
        {:else}
          <ul class="divide-y divide-border">
            {#each items as item (`${item.value}-${item.media_count}`)}
              <li class="flex items-center gap-3 px-3 py-2">
                <input
                  type="checkbox"
                  class="size-4"
                  checked={isSelected(item.value)}
                  oninput={() => toggleSelected(item.value)}
                />
                <button
                  type="button"
                  class="flex-1 text-left"
                  onclick={() => toggleSelected(item.value)}
                >
                  <p class="text-sm">
                    {item.name}
                    {#if item.name !== item.value}
                      <span class="text-muted-foreground">({item.value})</span>
                    {/if}
                  </p>
                  <p class="text-xs text-muted-foreground">
                    {item.media_count} item{item.media_count === 1 ? "" : "s"}
                  </p>
                </button>
              </li>
            {/each}
          </ul>
        {/if}
      </div>

      {#if hasMore}
        <div class="flex justify-center">
          <Button
            type="button"
            variant="secondary"
            disabled={loadingMore}
            onclick={loadMore}
          >
            {loadingMore ? "Loading..." : "Load more"}
          </Button>
        </div>
      {/if}
    </div>

    <Dialog.Footer class="px-6 py-4 shrink-0 border-t border-border mt-auto">
      <Button variant="secondary" onclick={() => (open = false)}>Cancel</Button>
      <Button onclick={applySelection}>Apply</Button>
    </Dialog.Footer>
  </Dialog.Content>
</Dialog.Root>
