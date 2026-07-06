<script lang="ts">
  import { onMount } from "svelte";
  import { engineCall } from "../lib/api";
  import { deleteJob, jobs, jobsError, jobsLoading, refreshJobs, runJobAction } from "../lib/stores/jobs";
  import type { JobInfo } from "../lib/types";
  import Icon from "../lib/components/Icon.svelte";
  import PageHeader from "../lib/components/PageHeader.svelte";
  import EmptyState from "../lib/components/EmptyState.svelte";
  import StatusPill from "../lib/components/StatusPill.svelte";

  let filter = $state("all");
  let selectedJobId = $state<number | null>(null);
  let actionJobId = $state<number | null>(null);
  let localError = $state("");
  let searchQuery = $state("");

  let filteredJobs = $derived.by(() => {
    let result = filter === "all" ? $jobs : $jobs.filter((job) => job.status === filter);
    const query = searchQuery.trim().toLowerCase();
    if (query) {
      result = result.filter((job) => titleOf(job).toLowerCase().includes(query) || job.input.toLowerCase().includes(query));
    }
    return result;
  });

  let selectedJob = $derived(selectedJobId === null ? undefined : $jobs.find((job) => job.id === selectedJobId));
  let counts = $derived.by(() => ({
    all: $jobs.length,
    running: $jobs.filter((job) => ["running", "pending", "pausing", "cancelling"].includes(job.status)).length,
    paused: $jobs.filter((job) => job.status === "paused").length,
    interrupted: $jobs.filter((job) => job.status === "interrupted").length,
    failed: $jobs.filter((job) => job.status === "failed").length,
    completed: $jobs.filter((job) => job.status === "completed").length,
  }));

  const statusText: Record<string, string> = {
    pending: "等待启动", running: "运行中", pausing: "正在暂停", cancelling: "正在取消",
    paused: "已暂停", interrupted: "异常中断", failed: "失败", cancelled: "已取消", completed: "已完成",
  };

  const stageText: Record<string, string> = {
    pending: "准备任务", resolving: "解析媒体", downloading: "下载媒体", transcribing: "语音转录",
    extracting_frames: "提取画面", vision_analyzing: "视觉理解", generating_notes: "生成笔记", indexing: "写入产物", completed: "处理完成",
    failed: "处理失败", interrupted: "异常中断", paused: "已暂停", cancelled: "已取消",
  };

  const filters = [
    { id: "all", label: "全部", icon: "list" },
    { id: "running", label: "运行中", icon: "activity" },
    { id: "paused", label: "已暂停", icon: "pause" },
    { id: "interrupted", label: "异常中断", icon: "alert" },
    { id: "failed", label: "失败", icon: "x" },
    { id: "completed", label: "已完成", icon: "check" },
  ];

  onMount(() => { refreshJobs().catch(() => undefined); });

  function titleOf(job: JobInfo) {
    return job.title || job.input.split(/[\\/]/).pop() || job.input;
  }

  function formatTime(value?: string | null) {
    if (!value) return "—";
    const date = new Date(value.includes("T") ? value : value.replace(" ", "T"));
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function formatDuration(seconds?: number) {
    if (seconds === undefined || seconds === null) return "—";
    if (seconds < 60) return `${Math.round(seconds)} 秒`;
    const minutes = Math.floor(seconds / 60);
    const rest = Math.round(seconds % 60);
    return `${minutes} 分 ${rest} 秒`;
  }

  function durationCaption(job: JobInfo) {
    if (job.elapsed_sec !== undefined && job.elapsed_sec !== null) return `已用 ${formatDuration(job.elapsed_sec)}`;
    return ["pending", "running", "pausing", "cancelling"].includes(job.status) ? "统计中" : "—";
  }

  function sourceKind(input: string) { return input.startsWith("http") ? "link" : "video"; }

  function canDelete(job: JobInfo) {
    return !["pending", "running", "pausing", "cancelling"].includes(job.status);
  }

  async function action(method: "process.pause" | "process.cancel" | "process.resume" | "process.retry", job: JobInfo) {
    if (method === "process.cancel" && !window.confirm("取消会清理该任务的断点工作区。确定继续吗？")) return;
    actionJobId = job.id;
    localError = "";
    try {
      const result = await runJobAction(method, job.id);
      if (typeof result === "object" && result?.job_id) selectedJobId = result.job_id;
    } catch (error) {
      localError = error instanceof Error ? error.message : String(error);
    } finally {
      actionJobId = null;
    }
  }

  async function removeJob(job: JobInfo) {
    if (!canDelete(job)) {
      localError = "任务仍在运行或等待启动，请先取消后再删除。";
      return;
    }
    const name = titleOf(job);
    const confirmed = window.confirm(
      `确定删除任务「${name}」吗？\n\n这会删除任务记录、断点工作区和临时文件；已经导出的笔记文件不会被删除。`
    );
    if (!confirmed) return;

    actionJobId = job.id;
    localError = "";
    try {
      const ok = await deleteJob(job.id);
      if (!ok) {
        localError = "任务不存在或已经被删除。";
      }
      if (selectedJobId === job.id) selectedJobId = null;
    } catch (error) {
      localError = error instanceof Error ? error.message : String(error);
    } finally {
      actionJobId = null;
    }
  }

  function toggleDetails(job: JobInfo) {
    selectedJobId = selectedJobId === job.id ? null : job.id;
  }

  async function openOutput(job: JobInfo, reveal = false) {
    if (!job.output_path) {
      localError = "该任务还没有生成笔记产物。";
      return;
    }
    actionJobId = job.id;
    localError = "";
    try {
      await engineCall(reveal ? "process.reveal_output" : "process.open_output", { job_id: job.id });
    } catch (error) {
      localError = error instanceof Error ? error.message : String(error);
    } finally {
      actionJobId = null;
    }
  }
</script>

<div class="page tasks-page">
  <PageHeader
    eyebrow="任务运行中心"
    title="任务中心"
    description="集中查看所有处理任务的实时进度、运行阶段、错误详情和断点恢复状态。"
    icon="tasks"
  >
    {#snippet actions()}
      <button class="btn btn-secondary" onclick={() => refreshJobs()} disabled={$jobsLoading}>
        <Icon name="refresh" size={15} />{$jobsLoading ? "正在刷新" : "刷新任务"}
      </button>
    {/snippet}
  </PageHeader>

  <section class="metrics-row" aria-label="任务统计">
    <button class="metric-card surface" class:active={filter === "all"} onclick={() => filter = "all"}>
      <span class="metric-icon all"><Icon name="list" size={19} /></span><div><strong>{counts.all}</strong><span>全部任务</span></div>
    </button>
    <button class="metric-card surface" class:active={filter === "running"} onclick={() => filter = "running"}>
      <span class="metric-icon running"><Icon name="activity" size={19} /></span><div><strong>{counts.running}</strong><span>正在运行</span></div>
    </button>
    <button class="metric-card surface" class:active={filter === "paused"} onclick={() => filter = "paused"}>
      <span class="metric-icon paused"><Icon name="pause" size={19} /></span><div><strong>{counts.paused}</strong><span>已暂停</span></div>
    </button>
    <button class="metric-card surface" class:active={filter === "failed"} onclick={() => filter = "failed"}>
      <span class="metric-icon failed"><Icon name="alert" size={19} /></span><div><strong>{counts.failed}</strong><span>失败任务</span></div>
    </button>
    <button class="metric-card surface" class:active={filter === "completed"} onclick={() => filter = "completed"}>
      <span class="metric-icon completed"><Icon name="check" size={19} /></span><div><strong>{counts.completed}</strong><span>已完成</span></div>
    </button>
  </section>

  <section class="task-workspace surface">
    <div class="task-toolbar">
      <div class="filter-tabs" aria-label="任务筛选">
        {#each filters as item}
          <button class:active={filter === item.id} onclick={() => filter = item.id}>
            <Icon name={item.icon} size={14} />
            <span>{item.label}</span>
            <em>{counts[item.id as keyof typeof counts]}</em>
          </button>
        {/each}
      </div>
      <div class="task-search input-wrap has-icon">
        <span class="input-icon"><Icon name="search" size={15} /></span>
        <input type="search" bind:value={searchQuery} placeholder="搜索任务名称或路径" aria-label="搜索任务" />
        {#if searchQuery}<button class="search-clear" onclick={() => searchQuery = ""} aria-label="清空搜索"><Icon name="x" size={13} /></button>{/if}
      </div>
    </div>

    {#if localError || $jobsError}
      <div class="alert alert-error workspace-alert"><Icon name="alert" size={17} /><span>{localError || $jobsError}</span></div>
    {/if}

    <div class="task-layout" class:with-detail={Boolean(selectedJob)}>
      <div class="task-list-area">
        <div class="table-head" aria-hidden="true">
          <span>任务</span><span>阶段与进度</span><span>创建时间</span><span>状态</span><span>操作</span>
        </div>

        <section class="task-list" aria-label="任务列表">
          {#if $jobsLoading && $jobs.length === 0}
            <div class="loading-state"><span class="loading-ring"></span><strong>正在读取任务记录</strong><p>正在连接持久化任务数据库…</p></div>
          {:else if filteredJobs.length === 0}
            <EmptyState
              icon={searchQuery ? "search" : "tasks"}
              title={searchQuery ? "没有匹配的任务" : "此筛选下没有任务"}
              description={searchQuery ? "尝试更换关键词或清空筛选条件。" : "新建任务后会自动出现在这里，运行状态将实时同步。"}
            />
          {:else}
            {#each filteredJobs as job (job.id)}
              <article class="task-row" class:selected={selectedJobId === job.id}>
                <button class="task-main" onclick={() => toggleDetails(job)} aria-label={`查看任务 ${titleOf(job)}`}>
                  <span class="media-icon"><Icon name={sourceKind(job.input)} size={18} /></span>
                  <span class="task-identity"><strong>{titleOf(job)}</strong><small>#{job.id} · {job.input}</small></span>
                  <span class="stage-cell">
                    <span class="stage-label"><strong>{job.progress_message || stageText[job.stage] || job.stage}</strong><em>{Math.round(job.progress || 0)}%</em></span>
                    <span class="progress-track"><span class="progress-bar" style={`width:${Math.max(0, Math.min(100, job.progress || 0))}%`}></span></span>
                    {#if job.error_message}<small class="inline-error">{job.error_message}</small>{/if}
                  </span>
                  <span class="time-cell"><strong>{formatTime(job.created_at)}</strong><small>{durationCaption(job)}</small></span>
                  <span class="status-cell"><StatusPill status={job.status} /></span>
                </button>

                <div class="row-actions">
                  {#if ["running", "pending"].includes(job.status)}
                    <button class="icon-btn" title="暂停" disabled={actionJobId === job.id} onclick={() => action("process.pause", job)}><Icon name="pause" size={15} /></button>
                    <button class="icon-btn danger-action" title="取消" disabled={actionJobId === job.id} onclick={() => action("process.cancel", job)}><Icon name="stop" size={15} /></button>
                  {:else if ["paused", "interrupted", "failed"].includes(job.status) && job.can_resume !== false}
                    <button class="btn btn-primary btn-sm" disabled={actionJobId === job.id} onclick={() => action("process.resume", job)}><Icon name="play" size={13} />断点继续</button>
                    {#if ["interrupted", "failed"].includes(job.status)}
                      <button class="icon-btn" title="从头重跑" disabled={actionJobId === job.id} onclick={() => action("process.retry", job)}><Icon name="rotate" size={15} /></button>
                    {/if}
                  {:else if job.status === "cancelled"}
                    <button class="btn btn-secondary btn-sm" disabled={actionJobId === job.id} onclick={() => action("process.retry", job)}><Icon name="rotate" size={13} />重新运行</button>
                  {:else}
                    <button class="icon-btn" title={selectedJobId === job.id ? "收起详情" : "查看详情"} onclick={() => toggleDetails(job)}><Icon name={selectedJobId === job.id ? "chevron-down" : "chevron-right"} size={16} /></button>
                  {/if}
                  {#if canDelete(job)}
                    <button class="icon-btn danger-action" title="删除任务" aria-label={`删除任务 ${titleOf(job)}`} disabled={actionJobId === job.id} onclick={() => removeJob(job)}><Icon name="trash" size={15} /></button>
                  {/if}
                </div>
              </article>
            {/each}
          {/if}
        </section>
      </div>

      {#if selectedJob}
        <aside class="detail-panel">
          <div class="detail-head">
            <div class="detail-icon"><Icon name={sourceKind(selectedJob.input)} size={20} /></div>
            <div class="detail-title"><span>任务详情</span><h2>{titleOf(selectedJob)}</h2></div>
            <button class="icon-btn" aria-label="关闭详情" onclick={() => selectedJobId = null}><Icon name="x" size={15} /></button>
          </div>

          <div class="detail-actions">
            {#if ["running", "pending"].includes(selectedJob.status)}
              <button class="btn btn-secondary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => action("process.pause", selectedJob)}><Icon name="pause" size={13} />暂停</button>
              <button class="btn btn-danger btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => action("process.cancel", selectedJob)}><Icon name="stop" size={13} />取消</button>
            {:else if ["paused", "interrupted", "failed"].includes(selectedJob.status) && selectedJob.can_resume !== false}
              <button class="btn btn-primary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => action("process.resume", selectedJob)}><Icon name="play" size={13} />断点继续</button>
              {#if ["interrupted", "failed"].includes(selectedJob.status)}
                <button class="btn btn-secondary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => action("process.retry", selectedJob)}><Icon name="rotate" size={13} />从头重跑</button>
              {/if}
            {:else if selectedJob.status === "cancelled"}
              <button class="btn btn-secondary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => action("process.retry", selectedJob)}><Icon name="rotate" size={13} />重新运行</button>
            {/if}
            {#if canDelete(selectedJob)}
              <button class="btn btn-danger btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => removeJob(selectedJob)}><Icon name="trash" size={13} />删除任务</button>
            {/if}
            {#if selectedJob.output_path}
              <button class="btn btn-primary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => openOutput(selectedJob)}><Icon name="external" size={13} />打开笔记</button>
              <button class="btn btn-secondary btn-sm" disabled={actionJobId === selectedJob.id} onclick={() => openOutput(selectedJob, true)}><Icon name="folder-open" size={13} />定位文件</button>
            {/if}
          </div>

          <div class="detail-status-card">
            <div><StatusPill status={selectedJob.status} /><span>{stageText[selectedJob.stage] || selectedJob.stage}</span></div>
            <strong>{Math.round(selectedJob.progress || 0)}%</strong>
            <div class="progress-track"><div class="progress-bar" style={`width:${selectedJob.progress || 0}%`}></div></div>
          </div>

          <div class="detail-section">
            <h3>执行信息</h3>
            <dl>
              <div><dt>当前阶段</dt><dd>{stageText[selectedJob.stage] || selectedJob.stage}</dd></div>
              <div><dt>最后断点</dt><dd>{stageText[selectedJob.last_active_stage || ""] || selectedJob.last_active_stage || "—"}</dd></div>
              <div><dt>执行次数</dt><dd>第 {selectedJob.attempt || 1} 次</dd></div>
              <div><dt>抽帧数量</dt><dd>{selectedJob.frames_count || 0} 帧</dd></div>
              <div><dt>开始时间</dt><dd>{formatTime(selectedJob.created_at)}</dd></div>
              <div><dt>运行时长</dt><dd>{durationCaption(selectedJob).replace(/^已用\s*/, "")}</dd></div>
            </dl>
          </div>

          <div class="detail-section">
            <h3>媒体来源</h3>
            <code>{selectedJob.input}</code>
          </div>

          {#if selectedJob.output_path}
            <div class="detail-section"><h3>笔记产物</h3><code>{selectedJob.output_path}</code></div>
          {/if}

          {#if selectedJob.error_message}
            <div class="detail-error"><div><Icon name="alert" size={16} /><strong>错误详情</strong></div><p>{selectedJob.error_message}</p></div>
          {/if}

          <div class="snapshot-note"><Icon name="shield" size={16} /><p>任务参数以无密钥快照保存；继续任务时会恢复原始模型配置，并重新读取当前安全凭据。</p></div>
        </aside>
      {/if}
    </div>
  </section>
</div>

<style>
  .tasks-page { max-width: 1440px; }
  .metrics-row { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 16px; }
  .metric-card { display: flex; align-items: center; gap: 11px; padding: 14px; color: var(--text-primary); cursor: pointer; text-align: left; transition: transform .15s, border-color .15s, box-shadow .15s; }
  .metric-card:hover { transform: translateY(-2px); box-shadow: var(--shadow-sm); }
  .metric-card.active { border-color: var(--accent-color); box-shadow: 0 0 0 3px var(--accent-glow); }
  .metric-icon { display: grid; place-items: center; width: 39px; height: 39px; flex: 0 0 auto; border-radius: 12px; }
  .metric-icon.all { color: var(--accent-color); background: var(--accent-soft); }
  .metric-icon.running { color: var(--info-color); background: var(--info-soft); }
  .metric-icon.paused { color: var(--warning-color); background: var(--warning-soft); }
  .metric-icon.failed { color: var(--danger-color); background: var(--danger-soft); }
  .metric-icon.completed { color: var(--success-color); background: var(--success-soft); }
  .metric-card div { display: flex; flex-direction: column; }
  .metric-card strong { font-size: 24px; line-height: 1.05; letter-spacing: -.03em; }
  .metric-card span:not(.metric-icon) { margin-top: 3px; color: var(--text-secondary); font-size: 13px; }

  .task-workspace { overflow: hidden; }
  .task-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 14px; border-bottom: 1px solid var(--border-color); background: var(--bg-subtle); }
  .filter-tabs { display: flex; align-items: center; gap: 3px; min-width: 0; }
  .filter-tabs button { display: flex; align-items: center; gap: 5px; min-height: 32px; padding: 6px 9px; border: 0; border-radius: 8px; color: var(--text-secondary); background: transparent; cursor: pointer; font-size: 14px; font-weight: 650; }
  .filter-tabs button:hover { color: var(--text-primary); background: var(--bg-hover); }
  .filter-tabs button.active { color: var(--accent-color); background: var(--accent-soft); }
  .filter-tabs em { display: grid; place-items: center; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 99px; color: var(--text-tertiary); background: var(--bg-muted); font-size: 12px; font-style: normal; }
  .filter-tabs button.active em { color: var(--accent-color); background: color-mix(in srgb, var(--accent-color) 12%, var(--bg-card)); }
  .task-search { width: 240px; flex: 0 0 auto; }
  .task-search input { min-height: 34px; padding-top: 6px; padding-bottom: 6px; font-size: 14px; }
  .search-clear { position: absolute; right: 6px; display: grid; place-items: center; width: 24px; height: 24px; border: 0; border-radius: 6px; color: var(--text-tertiary); background: transparent; cursor: pointer; }
  .workspace-alert { margin: 12px 14px 0; }

  .task-layout { display: grid; grid-template-columns: minmax(0,1fr); min-height: 420px; }
  .task-layout.with-detail { grid-template-columns: minmax(0,1fr) 330px; }
  .task-list-area { min-width: 0; }
  .table-head { display: grid; grid-template-columns: minmax(220px, 1.25fr) minmax(220px, 1.2fr) 115px 90px 112px; gap: 12px; padding: 10px 16px; border-bottom: 1px solid var(--border-color); color: var(--text-tertiary); background: color-mix(in srgb, var(--bg-subtle) 75%, transparent); font-size: 12px; font-weight: 750; letter-spacing: .06em; text-transform: uppercase; }
  .task-list { min-height: 360px; }
  .task-row { position: relative; display: grid; grid-template-columns: minmax(0,1fr) 112px; border-bottom: 1px solid var(--border-color); transition: background .14s; }
  .task-row:last-child { border-bottom: 0; }
  .task-row:hover, .task-row.selected { background: var(--accent-faint); }
  .task-row.selected { box-shadow: inset 3px 0 0 var(--accent-color); }
  .task-main { display: grid; grid-template-columns: 38px minmax(170px,1.25fr) minmax(210px,1.2fr) 115px 90px; align-items: center; gap: 12px; min-width: 0; padding: 13px 0 13px 16px; border: 0; color: inherit; background: transparent; cursor: pointer; text-align: left; }
  .media-icon { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 11px; color: var(--accent-color); background: var(--accent-soft); }
  .task-identity { display: flex; min-width: 0; flex-direction: column; }
  .task-identity strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
  .task-identity small { margin-top: 3px; overflow: hidden; color: var(--text-tertiary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .stage-cell { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
  .stage-label { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .stage-label strong { overflow: hidden; color: var(--text-secondary); font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
  .stage-label em { color: var(--text-primary); font-size: 13px; font-style: normal; font-weight: 700; }
  .stage-cell .progress-track { height: 5px; }
  .inline-error { overflow: hidden; color: var(--danger-color); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .time-cell { display: flex; flex-direction: column; }
  .time-cell strong { color: var(--text-secondary); font-size: 13px; font-weight: 580; }
  .time-cell small { margin-top: 3px; color: var(--text-tertiary); font-size: 12px; }
  .status-cell { display: flex; align-items: center; }
  .row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; padding-right: 14px; }
  .danger-action { color: var(--danger-color); }

  .loading-state { min-height: 360px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--text-secondary); }
  .loading-ring { width: 31px; height: 31px; margin-bottom: 12px; border: 3px solid var(--bg-progress); border-top-color: var(--accent-color); border-radius: 50%; animation: spin .8s linear infinite; }
  .loading-state strong { color: var(--text-primary); font-size: 14px; }
  .loading-state p { margin-top: 4px; font-size: 13px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .detail-panel { min-width: 0; padding: 17px; border-left: 1px solid var(--border-color); background: var(--bg-subtle); }
  .detail-head { display: grid; grid-template-columns: 39px minmax(0,1fr) 36px; align-items: start; gap: 10px; }
  .detail-icon { display: grid; place-items: center; width: 39px; height: 39px; border-radius: 12px; color: var(--accent-color); background: var(--accent-soft); }
  .detail-title { display: flex; min-width: 0; flex-direction: column; }
  .detail-title span { color: var(--text-tertiary); font-size: 12px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
  .detail-title h2 { margin-top: 3px; overflow: hidden; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
  .detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .btn-danger { border-color: color-mix(in srgb, var(--danger-color) 24%, var(--border-color)); color: var(--danger-color); background: var(--danger-soft); }
  .btn-danger:hover:not(:disabled) { border-color: color-mix(in srgb, var(--danger-color) 45%, var(--border-color)); background: color-mix(in srgb, var(--danger-color) 14%, var(--bg-card)); }
  .detail-status-card { margin-top: 16px; padding: 13px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card); }
  .detail-status-card > div:first-child { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .detail-status-card > div:first-child > span:last-child { overflow: hidden; color: var(--text-secondary); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .detail-status-card > strong { display: block; margin: 13px 0 7px; font-size: 30px; line-height: 1; letter-spacing: -.04em; }
  .detail-status-card .progress-track { height: 6px; }
  .detail-section { margin-top: 17px; }
  .detail-section h3 { margin-bottom: 8px; color: var(--text-secondary); font-size: 12px; font-weight: 750; letter-spacing: .07em; text-transform: uppercase; }
  dl { display: flex; flex-direction: column; gap: 1px; }
  dl div { display: flex; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid color-mix(in srgb, var(--border-color) 65%, transparent); font-size: 13px; }
  dl div:last-child { border-bottom: 0; }
  dt { color: var(--text-tertiary); }
  dd { text-align: right; }
  .detail-section code { display: block; max-height: 78px; overflow-y: auto; padding: 9px; border: 1px solid var(--border-color); border-radius: 9px; color: var(--text-secondary); background: var(--bg-card); font-family: var(--font-mono); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
  .detail-error { margin-top: 15px; padding: 11px; border: 1px solid color-mix(in srgb, var(--danger-color) 22%, var(--border-color)); border-radius: 10px; color: var(--danger-color); background: var(--danger-soft); }
  .detail-error > div { display: flex; align-items: center; gap: 6px; }
  .detail-error strong { font-size: 13px; }
  .detail-error p { margin-top: 6px; font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
  .snapshot-note { display: flex; align-items: flex-start; gap: 7px; margin-top: 17px; padding-top: 13px; border-top: 1px solid var(--border-color); color: var(--text-tertiary); }
  .snapshot-note p { font-size: 12px; line-height: 1.55; }

  @media (max-width: 1180px) {
    .metrics-row { grid-template-columns: repeat(3, 1fr); }
    .table-head { display: none; }
    .task-main { grid-template-columns: 38px minmax(160px,1fr) minmax(180px,1fr) 85px; }
    .time-cell { display: none; }
  }
  @media (max-width: 1050px) {
    .task-layout.with-detail { grid-template-columns: 1fr; }
    .detail-panel { border-top: 1px solid var(--border-color); border-left: 0; }
  }

  .tasks-page { max-width: 1320px; }
  .metrics-row { gap: 9px; margin-bottom: 13px; }
  .metric-card { min-height: 66px; padding: 11px 12px; border-radius: 13px; }
  .metric-icon { width: 34px; height: 34px; border-radius: 10px; }
  .metric-card strong { font-size: 21px; }
  .task-workspace { border-radius: 16px; box-shadow: var(--shadow-sm); }
  .task-toolbar { padding: 10px 12px; background: var(--bg-card); }
  .table-head { padding: 9px 14px; background: var(--bg-subtle); }
  .task-main { padding-left: 14px; }
  .task-row:hover { background: var(--bg-subtle); }
  .task-row.selected { background: var(--accent-faint); }
  .detail-panel { background: var(--bg-subtle); }


  /* UI v7 — desktop-density and readability pass */
  .tasks-page { max-width: 1240px; }
  .metrics-row { gap: 14px; margin-bottom: 18px; }
  .metric-card { min-height: 88px; gap: 14px; padding: 17px 18px; border-radius: 14px; }
  .metric-icon { width: 44px; height: 44px; border-radius: 12px; }
  .metric-card strong { font-size: 26px; }
  .metric-card span:not(.metric-icon) { margin-top: 5px; font-size: 13px; }

  .task-workspace { border-radius: 16px; box-shadow: var(--shadow-sm); }
  .task-toolbar { padding: 14px 16px; background: var(--bg-card); }
  .filter-tabs { gap: 5px; flex-wrap: wrap; }
  .filter-tabs button { min-height: 36px; padding: 7px 11px; font-size: 13px; }
  .filter-tabs em { min-width: 20px; height: 20px; font-size: 11px; }
  .task-search { width: 280px; }
  .task-search input { min-height: 40px; font-size: 13px; }

  .task-layout { min-height: 520px; }
  .task-layout.with-detail { grid-template-columns: minmax(0,1fr) 360px; }
  .table-head { grid-template-columns: minmax(240px,1.25fr) minmax(230px,1.2fr) 130px 100px 120px; gap: 14px; padding: 12px 18px; font-size: 11px; }
  .task-list { min-height: 440px; }
  .task-row { grid-template-columns: minmax(0,1fr) 120px; }
  .task-main { grid-template-columns: 44px minmax(190px,1.25fr) minmax(220px,1.2fr) 130px 100px; gap: 14px; padding: 16px 0 16px 18px; }
  .media-icon { width: 42px; height: 42px; border-radius: 12px; }
  .task-identity strong { font-size: 14px; }
  .task-identity small { margin-top: 4px; font-size: 11px; }
  .stage-label strong, .stage-label em { font-size: 12px; }
  .inline-error { font-size: 11px; }
  .time-cell strong { font-size: 12px; }
  .time-cell small { font-size: 11px; }
  .row-actions { padding-right: 16px; }

  .loading-state { min-height: 440px; }
  .loading-state strong { font-size: 15px; }
  .loading-state p { margin-top: 6px; font-size: 13px; }

  .detail-panel { padding: 20px; }
  .detail-title span, .detail-section h3 { font-size: 11px; }
  .detail-title h2 { font-size: 15px; }
  .detail-status-card { padding: 15px; }
  .detail-status-card > div:first-child > span:last-child { font-size: 11px; }
  dl div { padding: 8px 0; font-size: 12px; }
  .detail-section code { max-height: 100px; padding: 11px; font-size: 11px; }
  .detail-error strong { font-size: 12px; }
  .detail-error p, .snapshot-note p { font-size: 11px; }

  .task-layout.with-detail .table-head { display: none; }
  .task-layout.with-detail .task-list { min-height: 440px; }
  .task-layout.with-detail .task-row { grid-template-columns: minmax(0,1fr); row-gap: 10px; padding: 15px 16px 15px 18px; }
  .task-layout.with-detail .task-main {
    grid-template-columns: 42px minmax(0,1fr) auto;
    grid-template-areas:
      "icon identity status"
      "icon stage stage"
      "icon time time";
    align-items: start;
    gap: 7px 12px;
    padding: 0;
  }
  .task-layout.with-detail .media-icon { grid-area: icon; }
  .task-layout.with-detail .task-identity { grid-area: identity; }
  .task-layout.with-detail .task-identity strong { max-width: 100%; }
  .task-layout.with-detail .stage-cell { grid-area: stage; }
  .task-layout.with-detail .stage-label { align-items: baseline; }
  .task-layout.with-detail .inline-error { max-width: 100%; white-space: nowrap; }
  .task-layout.with-detail .time-cell { grid-area: time; display: flex; flex-direction: row; gap: 8px; }
  .task-layout.with-detail .status-cell { grid-area: status; justify-content: flex-end; }
  .task-layout.with-detail .row-actions { justify-content: flex-start; flex-wrap: wrap; gap: 7px; padding: 0 0 0 54px; }
  .task-layout.with-detail .row-actions .btn { min-height: 34px; }

  @media (max-width: 1250px) {
    .metrics-row { grid-template-columns: repeat(3, 1fr); }
    .task-layout.with-detail { grid-template-columns: 1fr; }
    .detail-panel { border-left: 0; border-top: 1px solid var(--border-color); }
  }

</style>
