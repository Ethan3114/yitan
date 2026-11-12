import { z } from "zod";
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(init?.signal?.reason);
  init?.signal?.addEventListener("abort", cancel, { once: true });
  if (init?.signal?.aborted) cancel();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    signal: controller.signal,
    headers: { ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...init?.headers },
  });
  const data = await response.json();
  if (!response.ok) {
    const error = data.error || { code: "http_error", message: "请求没有完成" };
    throw new ApiError(error.code, error.message);
  }
  return data as T;
  } finally {
    clearTimeout(timeout);
    init?.signal?.removeEventListener("abort", cancel);
  }
}

export async function apiForm<T>(path: string, body: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body });
}

export function wsUrl(callId: string): string {
  const base = (API_BASE || window.location.origin).replace(/^http/, "ws");
  return `${base}/api/v1/calls/${callId}/live`;
}

export async function read<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  return schema.parse(await api<unknown>(path, { signal }));
}
export function messageFor(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === "NotAllowedError") return "请在浏览器设置中允许使用麦克风，再开始访谈。";
  if (error instanceof DOMException && error.name === "NotFoundError") return "没有找到麦克风，请检查设备连接。";
  if (error instanceof z.ZodError) return "收到的内容格式有误，请重新读取。";
  return "暂时连接不上，请检查网络后重试。已经保存的内容还在。";
}
