BEGIN;
ALTER TABLE public.yitan_writing_cache ADD COLUMN invocation_token varchar(36);
ALTER TABLE public.yitan_draft_tasks ADD COLUMN transcription_token varchar(36);

CREATE FUNCTION public.yitan_heartbeat_draft(p_task text,p_owner text,p_token text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 UPDATE public.yitan_draft_tasks SET lease_until=now()+interval '90 seconds',updated_at=now()
 WHERE id=p_task AND owner_id=p_owner AND lease_token=p_token AND status='running' AND lease_until>now();
 RETURN FOUND;
END;
$$;

CREATE FUNCTION public.yitan_cache_checkpoint(p_id text,p_owner text,p_call text,p_task text,p_token text,p_payload text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM 1 FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND lease_until>now() AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO public.yitan_writing_cache(id,owner_id,call_id,status,payload,task_id)
 VALUES(p_id,p_owner,p_call,'completed',p_payload,p_task)
 ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status='completed',task_id=excluded.task_id,updated_at=now()
 WHERE public.yitan_writing_cache.owner_id=p_owner AND public.yitan_writing_cache.call_id=p_call AND public.yitan_writing_cache.invocation_token IS NULL;
 RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_model_begin(p_id text,p_owner text,p_call text,p_task text,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_writing_cache; inserted integer;
BEGIN
 PERFORM 1 FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND lease_until>now() AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='lease_lost'; END IF;
 INSERT INTO public.yitan_writing_cache(id,owner_id,call_id,status,task_id,invocation_token)
 VALUES(p_id,p_owner,p_call,'pending',p_task,p_token) ON CONFLICT(id) DO NOTHING;
 GET DIAGNOSTICS inserted=ROW_COUNT;
 SELECT * INTO result FROM public.yitan_writing_cache WHERE id=p_id AND owner_id=p_owner AND call_id=p_call;
 IF NOT FOUND OR result.invocation_token IS NULL THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='cache_conflict'; END IF;
 RETURN to_jsonb(result)||jsonb_build_object('created',inserted=1);
END;
$$;

CREATE FUNCTION public.yitan_model_finish(p_id text,p_owner text,p_call text,p_task text,p_token text,p_status text,p_payload text,p_usage jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE cached public.yitan_writing_cache;
BEGIN
 IF p_status NOT IN ('completed','incomplete','interrupted','rejected') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_model_status'; END IF;
 SELECT * INTO cached FROM public.yitan_writing_cache WHERE id=p_id AND owner_id=p_owner AND call_id=p_call AND task_id=p_task AND invocation_token=p_token FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF cached.status<>'pending' THEN
  RETURN cached.status=p_status AND cached.payload=p_payload AND cached.usage=p_usage;
 END IF;
 UPDATE public.yitan_writing_cache SET status=p_status,payload=p_payload,usage=p_usage,updated_at=now() WHERE id=p_id;
 RETURN true;
END;
$$;

CREATE FUNCTION public.yitan_transcription_begin(p_owner text,p_call text,p_task text,p_token text,p_object text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE task public.yitan_draft_tasks;
BEGIN
 SELECT * INTO task FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND lease_until>now() AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='lease_lost'; END IF;
 IF task.transcription_id IS NOT NULL THEN RETURN false; END IF;
 IF task.step='transcribe_pending' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='transcription_outcome_unknown'; END IF;
 UPDATE public.yitan_draft_tasks SET step='transcribe_pending',transcription_object=p_object,transcription_token=p_token,updated_at=now() WHERE id=p_task;
 RETURN true;
END;
$$;

CREATE FUNCTION public.yitan_transcription_record(p_owner text,p_call text,p_task text,p_token text,p_provider_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE task public.yitan_draft_tasks;
BEGIN
 IF p_provider_id='' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_provider_id'; END IF;
 SELECT * INTO task FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND transcription_token=p_token FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF task.transcription_id IS NOT NULL THEN RETURN task.transcription_id=p_provider_id; END IF;
 UPDATE public.yitan_draft_tasks SET transcription_id=p_provider_id,step=CASE WHEN step='transcribe_pending' THEN 'transcribing' ELSE step END,updated_at=now() WHERE id=p_task;
 RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION public.yitan_heartbeat_draft(text,text,text),public.yitan_cache_checkpoint(text,text,text,text,text,text),public.yitan_model_begin(text,text,text,text,text),public.yitan_model_finish(text,text,text,text,text,text,text,jsonb),public.yitan_transcription_begin(text,text,text,text,text),public.yitan_transcription_record(text,text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_heartbeat_draft(text,text,text),public.yitan_cache_checkpoint(text,text,text,text,text,text),public.yitan_model_begin(text,text,text,text,text),public.yitan_model_finish(text,text,text,text,text,text,text,jsonb),public.yitan_transcription_begin(text,text,text,text,text),public.yitan_transcription_record(text,text,text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
