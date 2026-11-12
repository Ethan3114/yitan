"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { ApiError, messageFor, read } from "./api";

export function useResource<T>(path: string, schema: z.ZodType<T>, { enabled = true, refreshPath }: { enabled?: boolean; refreshPath?: string } = {}) {
  const [result, setResult] = useState<{ path: string; version: number; data: T | null; error: string; unauthorized: boolean } | null>(null);
  const [version, setVersion] = useState(0);
  const refreshNext = useRef(false);
  const reload = useCallback(() => { refreshNext.current = true; setVersion(value => value + 1); }, []);
  useEffect(() => {
    if (!enabled) { refreshNext.current = false; return; }
    const controller = new AbortController();
    const requestedPath = refreshNext.current && refreshPath ? refreshPath : path;
    refreshNext.current = false;
    read(requestedPath, schema, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ path, version, data, error: "", unauthorized: false });
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setResult({ path, version, data: null, error: messageFor(cause), unauthorized: cause instanceof ApiError && cause.code === "unauthorized" });
    });
    return () => controller.abort();
  }, [path, schema, version, enabled, refreshPath]);
  const current = result?.path === path ? result : null;
  return { data: current?.data ?? null, error: current?.version === version ? current.error : "", unauthorized: current?.version === version ? current.unauthorized : false, reload };
}
