<script lang="ts">
  let {
    name,
    size = 18,
    strokeWidth = 1.8,
    className = "",
  }: {
    name: string;
    size?: number;
    strokeWidth?: number;
    className?: string;
  } = $props();
</script>

<svg
  class={className}
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={strokeWidth}
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === "play"}
    <path d="m8 5 11 7-11 7z" />
  {:else if name === "tasks" || name === "list"}
    <path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" />
  {:else if name === "note" || name === "file-text"}
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h6" />
  {:else if name === "folder"}
    <path d="M3 5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  {:else if name === "folder-open"}
    <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v2" /><path d="M3 10h18l-2 10H5z" />
  {:else if name === "settings"}
    <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.55V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63h.01A1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9c.16.61.7 1.03 1.33 1.03H21v4h-.3c-.63 0-1.17.42-1.3.97z" />
  {:else if name === "moon"}
    <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
  {:else if name === "sun"}
    <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
  {:else if name === "plus"}
    <path d="M12 5v14M5 12h14" />
  {:else if name === "upload"}
    <path d="M12 16V4M7 9l5-5 5 5" /><path d="M5 20h14" />
  {:else if name === "download"}
    <path d="M12 4v12M7 11l5 5 5-5" /><path d="M5 20h14" />
  {:else if name === "video"}
    <rect x="3" y="5" width="14" height="14" rx="2" /><path d="m17 9 4-2v10l-4-2z" />
  {:else if name === "audio"}
    <path d="M9 18V5l10-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="16" cy="16" r="3" />
  {:else if name === "link"}
    <path d="M10 13a5 5 0 0 0 7.07.07l2-2A5 5 0 0 0 12 4l-1.15 1.15" /><path d="M14 11a5 5 0 0 0-7.07-.07l-2 2A5 5 0 0 0 12 20l1.15-1.15" />
  {:else if name === "sparkles"}
    <path d="m12 3 1.2 3.1L16 7.5l-2.8 1.4L12 12l-1.2-3.1L8 7.5l2.8-1.4zM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8zM19 13l.6 1.4L21 15l-1.4.6L19 17l-.6-1.4L17 15l1.4-.6z" />
  {:else if name === "scan" || name === "ocr"}
    <path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2" /><path d="M7 12h10M8 9h8M8 15h8" />
  {:else if name === "eye"}
    <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" /><circle cx="12" cy="12" r="2.5" />
  {:else if name === "eye-off"}
    <path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 4.2A10.5 10.5 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.1 3.1M6.2 6.2C3.5 8 2 12 2 12s3.5 8 10 8c1.6 0 3-.5 4.2-1.2" />
  {:else if name === "chevron-down"}
    <path d="m6 9 6 6 6-6" />
  {:else if name === "chevron-right"}
    <path d="m9 18 6-6-6-6" />
  {:else if name === "chevrons-inward"}
    <path d="m7 7 3 3 3-3M7 17l3-3 3 3" />
  {:else if name === "arrow-right"}
    <path d="M5 12h14M13 6l6 6-6 6" />
  {:else if name === "refresh"}
    <path d="M20 11a8 8 0 1 0 2 5M20 4v7h-7" />
  {:else if name === "pause"}
    <path d="M9 5v14M15 5v14" />
  {:else if name === "stop"}
    <rect x="6" y="6" width="12" height="12" rx="1" />
  {:else if name === "rotate"}
    <path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" />
  {:else if name === "search"}
    <circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" />
  {:else if name === "copy"}
    <rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
  {:else if name === "external"}
    <path d="M14 3h7v7M10 14 21 3" /><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
  {:else if name === "trash"}
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 14h10l1-14M9 7V4h6v3" />
  {:else if name === "edit"}
    <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" />
  {:else if name === "calendar"}
    <rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" />
  {:else if name === "clock"}
    <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
  {:else if name === "activity"}
    <path d="M3 12h4l2-6 4 12 2-6h6" />
  {:else if name === "server"}
    <rect x="3" y="4" width="18" height="6" rx="2" /><rect x="3" y="14" width="18" height="6" rx="2" /><path d="M7 7h.01M7 17h.01" />
  {:else if name === "key"}
    <circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M15 8l3 3M17 6l3 3" />
  {:else if name === "bot"}
    <rect x="4" y="7" width="16" height="12" rx="3" /><path d="M12 3v4M8 12h.01M16 12h.01M8 16h8" />
  {:else if name === "template"}
    <rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 9v12" />
  {:else if name === "stethoscope"}
    <path d="M6 3v5a4 4 0 0 0 8 0V3M4 3h4M12 3h4M10 12v2a5 5 0 0 0 10 0v-1" /><circle cx="20" cy="10" r="2" />
  {:else if name === "x" || name === "close"}
    <path d="m6 6 12 12M18 6 6 18" />
  {:else if name === "check"}
    <path d="m5 12 4 4L19 6" />
  {:else if name === "alert"}
    <path d="M10.3 3.6 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z" /><path d="M12 9v4M12 17h.01" />
  {:else if name === "info"}
    <circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" />
  {:else if name === "database"}
    <ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
  {:else if name === "package"}
    <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3z" /><path d="m4.5 8 7.5 4 7.5-4M12 12v8.5" />
  {:else if name === "save"}
    <path d="M5 3h12l4 4v14H3V3z" /><path d="M7 3v6h10V3M8 21v-8h8v8" />
  {:else if name === "more"}
    <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" />
  {:else if name === "shield"}
    <path d="M12 2 4 5v6c0 5.1 3.4 9.8 8 11 4.6-1.2 8-5.9 8-11V5z" /><path d="m9 12 2 2 4-4" />
  {:else if name === "brain"}
    <path d="M12 2a7 7 0 0 0-7 7c0 2.38 1.19 4.47 3 5.74V17a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-2.26c1.81-1.27 3-3.36 3-5.74a7 7 0 0 0-7-7z" /><path d="M9 21h6M10 17v4M14 17v4" />
  {:else}
    <circle cx="12" cy="12" r="9" />
  {/if}
</svg>
