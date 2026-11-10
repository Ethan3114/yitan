"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Welcome } from "@/components/welcome";
import { Status } from "@/components/shell";
import { ApiError, messageFor, read } from "@/lib/api";
import { profilesSchema } from "@/lib/models";

export default function Home() {
  const router = useRouter();
  const [guest, setGuest] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    read("/api/v1/profiles", profilesSchema, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      const remembered = data.items.find(item => item.id === localStorage.getItem("yitan.currentProfile"));
      const selected = remembered ?? (data.items.length === 1 ? data.items[0] : undefined);
      router.replace(selected ? `/profiles/${selected.id}` : data.items.length ? "/profiles" : "/welcome?step=4");
    }).catch(cause => {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError && cause.code === "unauthorized") setGuest(true);
      else setError(messageFor(cause));
    });
    return () => controller.abort();
  }, [router, attempt]);
  if (guest) return <Welcome step={0} />;
  return <main className="onboarding"><Status loading={!error} error={error} retry={() => { setError(""); setAttempt(value => value + 1); }} /></main>;
}
