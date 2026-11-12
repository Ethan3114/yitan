import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    state = json.loads((ROOT / ".intermediate/public-runtime/account.json").read_text())
    original = str(UUID(state["task_id"]))
    owner, profile, call, prior, request_id = [str(uuid4()) for _ in range(5)]
    sql = f"""
BEGIN;
INSERT INTO public.yitan_owners(id) VALUES('{owner}');
INSERT INTO public.yitan_profiles(id,owner_id,elder_name,birth_year,hometown,family_names,topics_want,topics_avoid,source)
SELECT '{profile}','{owner}',p.elder_name,p.birth_year,p.hometown,p.family_names,p.topics_want,p.topics_avoid,p.source
FROM public.yitan_draft_tasks t JOIN public.yitan_calls c ON c.id=t.call_id JOIN public.yitan_profiles p ON p.id=c.profile_id
WHERE t.id='{original}';
INSERT INTO public.yitan_calls(id,owner_id,profile_id,status,ended_at) VALUES('{call}','{owner}','{profile}','ready',now());
INSERT INTO public.yitan_transcripts(id,owner_id,call_id,track,text)
SELECT gen_random_uuid()::text,'{owner}','{call}',x.track,x.text
FROM public.yitan_transcripts x JOIN public.yitan_draft_tasks t ON t.call_id=x.call_id WHERE t.id='{original}';
INSERT INTO public.yitan_draft_tasks(id,owner_id,call_id,request_id,status,step,transcription_id,transcription_object)
SELECT '{prior}','{owner}','{call}','{owner}:prior','failed','transcribing',transcription_id,transcription_object
FROM public.yitan_draft_tasks WHERE id='{original}';
DO $$
DECLARE existing public.yitan_draft_tasks; resumed jsonb;
BEGIN
 SELECT * INTO existing FROM public.yitan_draft_tasks WHERE id='{prior}';
 IF existing.transcription_id IS NULL THEN RAISE EXCEPTION 'real_provider_receipt_missing'; END IF;
 resumed:=public.yitan_enqueue_draft('{owner}','{call}','{request_id}',true);
 IF resumed->>'transcription_id' IS DISTINCT FROM existing.transcription_id
 OR resumed->>'transcription_object' IS DISTINCT FROM existing.transcription_object
 OR resumed->>'step'<>'transcribing' THEN RAISE EXCEPTION 'known_provider_resume_failed'; END IF;
END;
$$;
ROLLBACK;
SELECT count(*) AS remaining_verification_owners FROM public.yitan_owners WHERE id='{owner}';
"""
    cli = ROOT / ".intermediate/deploy-tools/node_modules/.bin/tcb"
    completed = subprocess.run([str(cli), "db", "execute", "-e", "xhs-titile-d0g1g5e3481a57b71", "--sql", sql, "--json"], capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(ROOT / ".intermediate/tmp")))
    if completed.returncode:
        raise RuntimeError("已知真实转写任务的恢复事务检查失败")
    body = json.loads(completed.stdout)
    if body.get("error") or body.get("Error"):
        raise RuntimeError("云端恢复事务返回错误")
    rows = body["data"]["Rows"]
    if len(rows) != 1 or json.loads(rows[0]) != ["0"]:
        raise AssertionError("只读恢复验证事务未完全撤销")
    result = {"known_provider_id_resume": True, "same_transcription_object": True, "resumes_polling": True, "transaction_rolled_back": True, "remaining_verification_owners": 0, "source_task_sha256": hashlib.sha256(original.encode()).hexdigest(), "model_calls": 0, "transcription_calls": 0}
    (ROOT / ".intermediate/cloud-known-transcription-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": 4, "transaction_rolled_back": True, "model_calls": 0, "transcription_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
