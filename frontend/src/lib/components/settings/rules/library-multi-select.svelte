<script lang="ts" module>
  import type { LibraryType } from "$lib/types/shared";

  export const libraryInstanceLabel = (library: LibraryType) =>
    library.serviceName ||
    (library.serviceConfigId !== null
      ? `${library.serviceType} #${library.serviceConfigId}`
      : library.serviceType);
</script>

<script lang="ts">
  import { Button } from "$lib/components/ui/button/index.js";
  import * as Command from "$lib/components/ui/command/index.js";
  import * as Popover from "$lib/components/ui/popover/index.js";
  import JellyfinSVG from "$lib/components/svgs/jellyfin-svg.svelte";
  import PlexSVG from "$lib/components/svgs/plex-svg.svelte";
  import EmbySVG from "$lib/components/svgs/emby-svg.svelte";
  import ChevronsUpDown from "@lucide/svelte/icons/chevrons-up-down";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { SettingsTab } from "$lib/types/shared";

  interface Props {
    libraries: LibraryType[];
    selectedIds: string[];
    onChange: (ids: string[]) => void;
    disabled?: boolean;
    // name the server beside each library when more than one is configured
    qualify?: boolean;
    placeholder?: string;
    triggerClass?: string;
  }

  let {
    libraries,
    selectedIds,
    onChange,
    disabled = false,
    qualify = false,
    placeholder = "Select libraries…",
    triggerClass = "",
  }: Props = $props();

  let open = $state(false);

  const byId = $derived(
    new Map(libraries.map((library) => [library.libraryId, library])),
  );

  // ids the rule names that the main server no longer has (server swap, imported rule)
  const unknownIds = $derived(selectedIds.filter((id) => !byId.has(id)));

  const nameFor = (id: string) => {
    const library = byId.get(id);
    if (!library) return `Unknown library (${id})`;
    return qualify
      ? `${library.libraryName} (${libraryInstanceLabel(library)})`
      : library.libraryName;
  };

  const triggerText = $derived(selectedIds.map(nameFor).join(", "));

  const toggle = (id: string) => {
    onChange(
      selectedIds.includes(id)
        ? selectedIds.filter((selected) => selected !== id)
        : [...selectedIds, id],
    );
  };
</script>

<Popover.Root bind:open>
  <Popover.Trigger class={triggerClass} {disabled}>
    <Button
      type="button"
      variant="outline"
      aria-label="Select libraries"
      aria-expanded={open}
      {disabled}
      class="h-8 w-full min-w-0 justify-between bg-background px-3 text-sm font-normal text-foreground cursor-pointer"
    >
      <span
        class={`flex min-w-0 items-center gap-1.5 truncate ${triggerText ? "" : "text-muted-foreground"}`}
      >
        {#if unknownIds.length > 0}
          <TriangleAlert class="size-3.5 shrink-0 text-amber-500" />
        {/if}
        <span class="truncate">{triggerText || placeholder}</span>
      </span>
      <ChevronsUpDown class="size-4 shrink-0 opacity-60" />
    </Button>
  </Popover.Trigger>
  <Popover.Content
    align="start"
    class="w-(--bits-popover-anchor-width) min-w-64 p-0 gap-0"
  >
    <Command.Root class="rounded-md p-0">
      <Command.Input placeholder="Search libraries..." />
      <Command.List class="max-h-72">
        <Command.Empty>No matching libraries.</Command.Empty>
        {#each libraries as library (`${library.serviceConfigId}:${library.libraryId}`)}
          <Command.Item
            value={`${library.libraryId}:${library.libraryName}`}
            keywords={[library.libraryName, libraryInstanceLabel(library)]}
            data-checked={selectedIds.includes(library.libraryId)}
            onSelect={() => toggle(library.libraryId)}
          >
            <span class="w-4 h-4 shrink-0">
              {#if library.serviceType === SettingsTab.Jellyfin}
                <JellyfinSVG />
              {:else if library.serviceType === SettingsTab.Plex}
                <PlexSVG />
              {:else if library.serviceType === SettingsTab.Emby}
                <EmbySVG />
              {/if}
            </span>
            <span class="truncate">{library.libraryName}</span>
            {#if qualify}
              <span class="truncate text-xs text-muted-foreground">
                {libraryInstanceLabel(library)}
              </span>
            {/if}
          </Command.Item>
        {/each}
        {#if unknownIds.length > 0}
          <Command.Group heading="Not on the main server">
            {#each unknownIds as id (id)}
              <Command.Item
                value={`unknown:${id}`}
                data-checked={true}
                onSelect={() => toggle(id)}
                class="text-amber-600 dark:text-amber-400"
              >
                <TriangleAlert class="size-4 shrink-0" />
                <span class="truncate">{nameFor(id)}</span>
              </Command.Item>
            {/each}
          </Command.Group>
        {/if}
      </Command.List>
    </Command.Root>
  </Popover.Content>
</Popover.Root>
