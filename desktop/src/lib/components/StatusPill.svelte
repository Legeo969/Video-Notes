<script lang="ts">
  let { status, label }: { status: string; label?: string } = $props();

  const labels: Record<string, string> = {
    pending: "等待启动",
    running: "运行中",
    pausing: "正在暂停",
    cancelling: "正在取消",
    paused: "已暂停",
    interrupted: "异常中断",
    failed: "失败",
    cancelled: "已取消",
    active: "可用",
    completed: "已完成",
    pass: "通过",
    warn: "警告",
    fail: "失败",
  };
</script>

<span class="status-pill status-{status}">
  <span class="status-pill-dot"></span>
  {label || labels[status] || status}
</span>
