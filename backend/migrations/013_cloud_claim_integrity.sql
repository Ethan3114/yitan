BEGIN;
CREATE OR REPLACE FUNCTION public.yitan_claim_draft(p_task text,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_draft_tasks;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_capacity',0));
 IF EXISTS(SELECT 1 FROM public.yitan_calls WHERE status='live' AND coalesce(heartbeat_at,created_at)>now()-interval '2 minutes') THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE status='running' AND lease_until>now()) THEN RETURN NULL; END IF;
 SELECT t.* INTO result FROM public.yitan_draft_tasks t
 JOIN public.yitan_calls c ON c.id=t.call_id AND c.owner_id=t.owner_id
 JOIN public.yitan_profiles p ON p.id=c.profile_id AND p.owner_id=t.owner_id
 WHERE t.id=p_task FOR UPDATE OF t SKIP LOCKED;
 IF NOT FOUND OR result.status NOT IN ('queued','running') THEN RETURN NULL; END IF;
 UPDATE public.yitan_draft_tasks SET status='running',lease_token=p_token,lease_until=now()+interval '90 seconds',updated_at=now()
 WHERE id=p_task RETURNING * INTO result;
 RETURN to_jsonb(result);
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_claim_draft(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_claim_draft(text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
