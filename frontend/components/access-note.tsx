"use client";
import { z } from "zod";
import { useResource } from "@/lib/use-resource";
const methods = z.object({ local_invite_login: z.boolean() });
export function AccessNote() {
  const { data } = useResource("/api/v1/auth/methods", methods);
  return <>{data?.local_invite_login ? "双方连接同一 Wi-Fi，电脑保持运行。" : "家人可通过邀请地址加入阅读。"}</>;
}
