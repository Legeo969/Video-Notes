export interface LocalWhisperModel {
  id: string;
  path?: string;
  source?: string;
}

export interface WhisperModelOption {
  id: string;
  label: string;
  description: string;
  speed: string;
  quality: number;
  installed: boolean;
  path?: string;
  source?: string;
  custom: boolean;
}

const BUILTIN_MODELS: Record<string, Omit<WhisperModelOption, "id" | "installed" | "path" | "source" | "custom">> = {
  tiny: { label: "Tiny", description: "最快速度，适合快速试跑", speed: "约 32×", quality: 1 },
  base: { label: "Base", description: "轻量任务与清晰语音", speed: "约 16×", quality: 2 },
  small: { label: "Small", description: "速度和准确率均衡", speed: "约 8×", quality: 3 },
  medium: { label: "Medium", description: "更高准确率", speed: "约 4×", quality: 4 },
  "large-v3": { label: "Large v3", description: "最高准确率", speed: "约 2×", quality: 5 },
  "large-v3-turbo": { label: "Large v3 Turbo", description: "高准确率与更快推理", speed: "约 4×", quality: 5 },
};

const BUILTIN_ORDER = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"];

export function normalizeWhisperModelId(raw: string | null | undefined): string {
  let value = String(raw ?? "").trim();
  if (!value) return "";

  value = value.replace(/\\/g, "/");
  const last = value.split("/").filter(Boolean).pop();
  if (last) value = last;

  value = value.replace(/^models--Systran--/i, "");
  value = value.replace(/^Systran--/i, "");
  value = value.replace(/^faster-whisper-/i, "");
  return value.trim();
}

export function normalizeLocalWhisperModels(
  raw: Array<string | LocalWhisperModel> | null | undefined,
): LocalWhisperModel[] {
  const byId = new Map<string, LocalWhisperModel>();
  for (const entry of raw ?? []) {
    const source = typeof entry === "string" ? { id: entry } : entry;
    const id = normalizeWhisperModelId(source.id);
    if (!id) continue;
    const existing = byId.get(id);
    byId.set(id, {
      id,
      path: source.path || existing?.path,
      source: source.source || existing?.source,
    });
  }
  return [...byId.values()].sort((a, b) => {
    const ai = BUILTIN_ORDER.indexOf(a.id);
    const bi = BUILTIN_ORDER.indexOf(b.id);
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    }
    return a.id.localeCompare(b.id);
  });
}

export function buildWhisperModelCatalog(
  localModels: LocalWhisperModel[],
  selectedModel?: string,
  includeUnavailableBuiltins = false,
): WhisperModelOption[] {
  const normalized = normalizeLocalWhisperModels(localModels);
  const localById = new Map(normalized.map((model) => [model.id, model]));
  const selected = normalizeWhisperModelId(selectedModel);
  const ids = new Set<string>();

  if (includeUnavailableBuiltins) {
    for (const id of BUILTIN_ORDER) ids.add(id);
  }
  for (const model of normalized) ids.add(model.id);
  if (selected) ids.add(selected);

  const orderedIds = [...ids].sort((a, b) => {
    const ai = BUILTIN_ORDER.indexOf(a);
    const bi = BUILTIN_ORDER.indexOf(b);
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    }
    return a.localeCompare(b);
  });

  return orderedIds.map((id) => {
    const builtin = BUILTIN_MODELS[id];
    const local = localById.get(id);
    return {
      id,
      label: builtin?.label ?? humanizeWhisperModelId(id),
      description: builtin?.description ?? "本地自定义 faster-whisper 模型",
      speed: builtin?.speed ?? "自定义",
      quality: builtin?.quality ?? 4,
      installed: Boolean(local),
      path: local?.path,
      source: local?.source,
      custom: !builtin,
    };
  });
}

export function humanizeWhisperModelId(id: string): string {
  return normalizeWhisperModelId(id)
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => {
      if (/^v\d+$/i.test(part)) return part.toUpperCase();
      if (/^\d+$/.test(part)) return part;
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join(" ");
}
