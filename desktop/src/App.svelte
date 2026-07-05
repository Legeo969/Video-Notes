<script lang="ts">
  import Sidebar from "./lib/components/Sidebar.svelte";
  import Process from "./pages/Process.svelte";
  import Tasks from "./pages/Tasks.svelte";
  import Notes from "./pages/Notes.svelte";
  import Collections from "./pages/Collections.svelte";
  import Settings from "./pages/Settings.svelte";

  let currentPage = $state("process");

  function navigate(page: string) {
    currentPage = page;
  }
</script>

<div class="app-layout">
  <Sidebar {navigate} active={currentPage} />
  <main class="content-area">
    {#if currentPage === "process"}
      <Process />
    {:else if currentPage === "tasks"}
      <Tasks />
    {:else if currentPage === "notes"}
      <Notes />
    {:else if currentPage === "collections"}
      <Collections />
    {:else if currentPage === "settings"}
      <Settings />
    {/if}
  </main>
</div>

<style>
  .app-layout {
    display: flex;
    height: 100vh;
    overflow: hidden;
  }

  .content-area {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    background: var(--bg-primary);
  }
</style>