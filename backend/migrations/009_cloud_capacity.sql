BEGIN;
CREATE OR REPLACE FUNCTION public.yitan_claim_draft(p_task text,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_draft_tasks;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_capacity',0));
 IF EXISTS(SELECT 1 FROM public.yitan_calls WHERE status='live' AND coalesce(heartbeat_at,created_at)>now()-interval '2 minutes') THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE status='running' AND lease_until>now()) THEN RETURN NULL; END IF;
 SELECT * INTO result FROM public.yitan_draft_tasks WHERE id=p_task FOR UPDATE SKIP LOCKED;
 IF NOT FOUND OR result.status NOT IN ('queued','running') THEN RETURN NULL; END IF;
 UPDATE public.yitan_draft_tasks SET status='running',lease_token=p_token,lease_until=now()+interval '90 seconds',updated_at=now()
 WHERE id=p_task RETURNING * INTO result;
 RETURN to_jsonb(result);
END;
$$;
CREATE FUNCTION public.yitan_claim_call(p_owner text,p_call text,p_token text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE changed integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_capacity',0));
 IF EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE status='running' AND lease_until>now()) THEN RETURN false; END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_calls WHERE id<>p_call AND status='live' AND connection_token<>'' AND heartbeat_at>now()-interval '2 minutes') THEN RETURN false; END IF;
 UPDATE public.yitan_calls SET connection_token=p_token,heartbeat_at=now()
 WHERE id=p_call AND owner_id=p_owner AND status='live' AND connection_token='';
 GET DIAGNOSTICS changed=ROW_COUNT;
 RETURN changed=1;
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_claim_call(text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_claim_call(text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
