import { derived, get, writable } from "svelte/store";
import { engineCall, onEngineEvent } from "../api";
import type { JobInfo, JobProgressEvent, PageName } from "../types";

export const jobs = writable<JobInfo[]>([]);
export const jobsLoading = writable(false);
export const jobsError = writable("");
export const activeJobs = derived(jobs, ($jobs) =>
  $jobs.filter((job) => ["pending", "running", "pausing", "cancelling", "paused"].includes(job.status))
);

/** Set by Tasks.svelte to auto-select a note on the Notes page */
export const selectedNoteId = writable<number | null>(null);

/** Set by pages to trigger page navigation in App.svelte */
export const navigateQueue = writable<PageName[]>([]);
export function requestNavigate(page: PageName) {
  navigateQueue.update(q => [...q, page]);
}

let eventUnlisten: (() => void) | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshJobs().catch(() => undefined);
  }, 250);
}

export async function refreshJobs(): Promise<JobInfo[]> {
  jobsLoading.set(true);
  jobsError.set("");
  try {
    const result = await engineCall<JobInfo[]>("process.list", { limit: 200 });
    jobs.set(result);
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    jobsError.set(message);
    throw error;
  } finally {
    jobsLoading.set(false);
  }
}

export function applyJobEvent(event: JobProgressEvent) {
  let found = false;
  jobs.update((items) => {
    const updated = items.map((job) =>
      job.id === event.job_id
        ? ({
            ...job,
            status: event.status,
            stage: event.stage,
            progress: Math.max(0, Math.min(100, event.progress)),
            progress_message: event.message,
            heartbeat_at: event.timestamp ?? job.heartbeat_at,
          } as JobInfo)
        : job
    );
    // If the job id isn't in the store yet, it's a new job — do a full refresh
    found = updated.some((job) => job.id === event.job_id);
    return updated;
  });
  if (!found) {
    scheduleRefresh();
  }
}

export async function initializeJobEvents(): Promise<() => void> {
  if (!eventUnlisten) {
    eventUnlisten = await onEngineEvent<JobProgressEvent>("job.progress", applyJobEvent);
  }
  return () => {
    eventUnlisten?.();
    eventUnlisten = null;
  };
}

export function getJob(runId: number): JobInfo | undefined {
  return get(jobs).find((job) => job.id === runId);
}

export async function runJobAction(
  method: "process.pause" | "process.cancel" | "process.resume" | "process.retry",
  jobId: number
): Promise<{ job_id?: number } | boolean> {
  const result = await engineCall<{ job_id?: number } | boolean>(method, { job_id: jobId });
  await refreshJobs();
  return result;
}

export async function deleteJob(jobId: number): Promise<boolean> {
  const result = await engineCall<boolean>("process.delete", { job_id: jobId });
  await refreshJobs();
  return result;
}
