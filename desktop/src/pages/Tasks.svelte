<script lang="ts">
  import { invoke, listen } from "../lib/api";
  import type { JobInfo } from "../lib/types";

  let jobs = $state<JobInfo[]>([]);
  let selectedJob = $state<JobInfo | null>(null);

  async function loadJobs() {
    jobs = await invoke<JobInfo[]>("process.list");
  }

  async function pauseJob(id: number) { await invoke("process.pause", { id }); await loadJobs(); }
  async function cancelJob(id: number) { await invoke("process.cancel", { id }); await loadJobs(); }
  async function resumeJob(id: number) { await invoke("process.resume", { id }); await loadJobs(); }
  async function retryJob(id: number) { await invoke("process.retry", { id }); await loadJobs(); }

  // Listen for progress updates
  $effect(() => {
    const unlisten = listen<{ job_id: number; stage: string; stage_progress: number }>(
      "job.progress",
      (event) => {
        console.log("progress", event);
      }
    );
    return () => unlisten.then(fn => fn());
  });

  $effect(() => { loadJobs(); });
</script>

<div class="page">
  <h2 class="page-title">任务</h2>

  <table class="task-table">
    <thead>
      <tr>
        <th>标题</th>
        <th>状态</th>
        <th>阶段</th>
        <th>进度</th>
        <th>操作</th>
      </tr>
    </thead>
    <tbody>
      {#each jobs as job (job.id)}
        <tr onclick={() => selectedJob = job}>
          <td>{job.title || job.input.slice(0, 40)}</td>
          <td><span class="status-badge status-{job.status}">{job.status}</span></td>
          <td>{job.stage}</td>
          <td>
            <div class="mini-progress">
              <div class="mini-fill" style="width: {job.progress * 100}%"></div>
            </div>
          </td>
          <td class="actions">
            {#if job.status === "running"}
              <button class="btn-sm" onclick={() => pauseJob(job.id)}>暂停</button>
              <button class="btn-sm btn-danger" onclick={() => cancelJob(job.id)}>取消</button>
            {:else if job.status === "paused"}
              <button class="btn-sm" onclick={() => resumeJob(job.id)}>继续</button>
            {:else if job.status === "failed"}
              <button class="btn-sm" onclick={() => retryJob(job.id)}>重试</button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .task-table {
    width: 100%;
    border-collapse: collapse;
  }

  .task-table th {
    text-align: left;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary);
    border-bottom: 2px solid var(--border-color);
  }

  .task-table td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-color);
    font-size: 14px;
  }

  .task-table tbody tr {
    cursor: pointer;
    transition: background 0.1s;
  }

  .task-table tbody tr:hover {
    background: var(--bg-hover);
  }

  .status-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 500;
  }

  .status-completed { background: #d4edda; color: #155724; }
  .status-running { background: #cce5ff; color: #004085; }
  .status-paused { background: #fff3cd; color: #856404; }
  .status-failed { background: #f8d7da; color: #721c24; }
  .status-cancelled { background: #e2e3e5; color: #383d41; }

  .mini-progress {
    width: 100px;
    height: 6px;
    background: var(--bg-progress);
    border-radius: 3px;
    overflow: hidden;
  }

  .mini-fill {
    height: 100%;
    background: var(--accent-color);
    transition: width 0.3s;
  }

  .actions {
    display: flex;
    gap: 4px;
  }
</style>