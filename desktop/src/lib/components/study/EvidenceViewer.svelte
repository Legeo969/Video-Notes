<script lang="ts">
  import type { VideoCapsule, Evidence } from "../../types";

  let {
    bundle = null,
    onSeek,
  }: {
    bundle: VideoCapsule | null;
    onSeek?: (timeUs: number) => void;
  } = $props();

  // ── Search / filter ────────────────────────────────────
  let filterText = $state("");

  /** Filtered evidence items. */
  let filtered = $derived.by(() => {
    if (!bundle?.evidences?.length) return [];
    if (!filterText.trim()) return bundle.evidences;
    const q = filterText.toLowerCase();
    return bundle.evidences.filter(
      (e) =>
        e.content.toLowerCase().includes(q) ||
        e.evidence_type.toLowerCase().includes(q) ||
        (e.speaker && e.speaker.toLowerCase().includes(q))
    );
  });

  // ── Type badge colour map ──────────────────────────────
  const TYPE_COLORS: Record<string, string> = {
    fact: "#3b82f6",
    procedure: "#10b981",
    concept: "#8b5cf6",
    failure: "#ef4444",
    verification: "#f59e0b",
    draft: "#94a3b8",
  };

  function typeColor(t: string): string {
    return TYPE_COLORS[t.toLowerCase()] || "#94a3b8";
  }

  // ── Time formatting ────────────────────────────────────
  function fmtTime(sec: number): string {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // ── Confidence bar width ──────────────────────────────
  function pct(v: number): string {
    return `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%`;
  }

  // ── Seek handler ───────────────────────────────────────
  function handleSeek(evidence: Evidence) {
    onSeek?.(evidence.timestamp_start_sec * 1_000_000);
  }

  function handleKeydown(e: KeyboardEvent, evidence: Evidence) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSeek(evidence);
    }
  }

  function activeTypeColor(t: string): string {
    return typeColor(t);
  }
</script>

<div class="ev-root">
  <!-- Header + search -->
  <div class="ev-header">
    <div class="ev-header-top">
      <h3 class="ev-title">Evidence</h3>
      {#if bundle?.evidences}
        <span class="ev-count">{bundle.evidences.length}</span>
      {/if}
    </div>

    <div class="ev-filter-wrap">
      <svg class="ev-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
      <input
        class="ev-filter"
        type="text"
        placeholder="Filter evidence…"
        bind:value={filterText}
        aria-label="Filter evidence items"
      />
    </div>
  </div>

  <!-- Evidence list -->
  <div class="ev-list" role="list" aria-label="Evidence items">
    {#if !bundle}
      <div class="ev-empty">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h6"/></svg>
        <span>No evidence bundle loaded.</span>
      </div>
    {:else if bundle.evidences.length === 0}
      <div class="ev-empty">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>
        <span>No evidence items in this bundle.</span>
      </div>
    {:else if filtered.length === 0}
      <div class="ev-empty">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
        <span>No evidence matches your filter.</span>
      </div>
    {:else}
      {#each filtered as evidence (evidence.id)}
        <div
          class="ev-card"
          role="button"
          tabindex="0"
          aria-label={`Evidence: ${evidence.content.slice(0, 60)}`}
          onclick={() => handleSeek(evidence)}
          onkeydown={(e) => handleKeydown(e, evidence)}
        >
          <div class="ev-card-main">
            <p class="ev-content">{evidence.content}</p>

            <div class="ev-meta">
              <!-- Timestamp -->
              <span class="ev-timestamp" title="Click to seek to this timestamp">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>
                {fmtTime(evidence.timestamp_start_sec)} &ndash; {fmtTime(evidence.timestamp_end_sec)}
              </span>

              <!-- Type badge -->
              <span
                class="ev-badge"
                style="--badge-color: {typeColor(evidence.evidence_type)}"
              >
                {evidence.evidence_type}
              </span>

              <!-- Speaker -->
              {#if evidence.speaker}
                <span class="ev-speaker">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                  {evidence.speaker}
                </span>
              {/if}
            </div>

            <!-- Confidence bar -->
            <div class="ev-confidence">
              <span class="ev-conf-label">{Math.round(evidence.confidence * 100)}%</span>
              <div class="ev-conf-track">
                <div class="ev-conf-fill" style="width: {pct(evidence.confidence)}"></div>
              </div>
            </div>
          </div>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .ev-root {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    background: var(--bg-card);
    border: 1px solid var(--border-color);
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .ev-header {
    flex: 0 0 auto;
    padding: 14px 14px 10px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-card);
  }

  .ev-header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
  }

  .ev-title {
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0;
  }

  .ev-count {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 22px;
    height: 22px;
    padding: 0 6px;
    border-radius: 99px;
    background: var(--accent-soft);
    color: var(--accent-color);
    font-size: 11px;
    font-weight: 700;
  }

  .ev-filter-wrap {
    position: relative;
    display: flex;
    align-items: center;
  }

  .ev-search-icon {
    position: absolute;
    left: 9px;
    color: var(--text-tertiary);
    pointer-events: none;
  }

  .ev-filter {
    width: 100%;
    min-height: 32px;
    padding: 5px 8px 5px 30px;
    border: 1px solid var(--border-color);
    border-radius: 8px;
    background: var(--bg-input);
    color: var(--text-primary);
    font-size: 12px;
    outline: none;
    transition: border-color 0.14s;
  }

  .ev-filter:focus {
    border-color: var(--accent-color);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }

  .ev-filter::placeholder {
    color: var(--text-tertiary);
  }

  .ev-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .ev-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 120px;
    color: var(--text-tertiary);
    font-size: 13px;
    text-align: center;
    padding: 20px;
  }

  .ev-empty svg {
    opacity: 0.4;
  }

  .ev-card {
    display: flex;
    flex-direction: column;
    padding: 10px 12px;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    background: var(--bg-sidebar);
    cursor: pointer;
    transition: background 0.14s, border-color 0.14s, box-shadow 0.14s;
    outline: none;
  }

  .ev-card:hover {
    background: var(--bg-hover);
    border-color: var(--border-strong);
  }

  .ev-card:focus-visible {
    border-color: var(--accent-color);
    box-shadow: 0 0 0 3px var(--accent-glow);
  }

  .ev-card-main {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }

  .ev-content {
    font-size: 13px;
    line-height: 1.5;
    color: var(--text-primary);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    line-clamp: 3;
    overflow: hidden;
  }

  .ev-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }

  .ev-timestamp {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    font-weight: 600;
    color: var(--accent-color);
    white-space: nowrap;
  }

  .ev-badge {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    border-radius: 99px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--badge-color);
    background: color-mix(in srgb, var(--badge-color) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--badge-color) 24%, transparent);
  }

  .ev-speaker {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    color: var(--text-secondary);
    white-space: nowrap;
  }

  .ev-confidence {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .ev-conf-label {
    font-size: 10px;
    font-weight: 700;
    color: var(--text-tertiary);
    min-width: 28px;
    text-align: right;
  }

  .ev-conf-track {
    flex: 1;
    height: 4px;
    border-radius: 99px;
    background: var(--bg-progress);
    overflow: hidden;
  }

  .ev-conf-fill {
    height: 100%;
    border-radius: 99px;
    background: var(--accent-color);
    transition: width 0.2s ease;
  }
</style>
