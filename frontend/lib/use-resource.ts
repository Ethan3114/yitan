"use client";
import { useCallback, useEffect, useState } from "react";
import { z } from "zod";
import { ApiError, messageFor, read } from "./api";

export function useResource<T>(path: string, schema: z.ZodType<T>) {
  const [result, setResult] = useState<{ path: string; version: number; data: T | null; error: string; unauthorized: boolean } | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    read(path, schema, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ path, version, data, error: "", unauthorized: false });
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setResult({ path, version, data: null, error: messageFor(cause), unauthorized: cause instanceof ApiError && cause.code === "unauthorized" });
    });
    return () => controller.abort();
  }, [path, schema, version]);
  const current = result?.path === path ? result : null;
  return { data: current?.data ?? null, error: current?.version === version ? current.error : "", unauthorized: current?.version === version ? current.unauthorized : false, reload };
}
