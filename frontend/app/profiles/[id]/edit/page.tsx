"use client";
import { use } from "react";
import { ProfileForm } from "@/components/profile-form";
import { Shell, Status } from "@/components/shell";
import { profileSchema } from "@/lib/models";
import { useResource } from "@/lib/use-resource";
export default function Edit({ params: paramsPromise }: { params: Promise<{ id: string }> }) {
  const params = use(paramsPromise);
  const { data, error, unauthorized, reload } = useResource(`/api/v1/profiles/${params.id}`, profileSchema);
  return <Shell title="老人资料" back={`/account?profile=${params.id}`} profileId={params.id} active="me"><Status error={error} unauthorized={unauthorized} retry={reload} loading={!data && !error} />{data ? <div className="card"><ProfileForm profile={data} /></div> : null}</Shell>;
}
