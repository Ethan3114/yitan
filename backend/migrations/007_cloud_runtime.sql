BEGIN;
ALTER TABLE public.yitan_private_files ALTER COLUMN call_id DROP NOT NULL;
ALTER TABLE public.yitan_private_files DROP CONSTRAINT IF EXISTS yitan_private_files_call_id_role_key;
ALTER TABLE public.yitan_calls ADD COLUMN IF NOT EXISTS connection_token text NOT NULL DEFAULT '';
ALTER TABLE public.yitan_calls ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;
CREATE TABLE public.yitan_call_contexts (
 id varchar(100) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) UNIQUE NOT NULL REFERENCES public.yitan_calls(id),photo_id varchar(36),topic varchar(500) NOT NULL DEFAULT ''
);
CREATE TABLE public.yitan_audio_chunks (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),kind varchar(20) NOT NULL,
 sequence integer NOT NULL,file_id varchar(64) NOT NULL REFERENCES public.yitan_private_files(id),
 size_bytes bigint NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(call_id,kind,sequence)
);
CREATE TABLE public.yitan_call_timelines (
 call_id varchar(36) PRIMARY KEY REFERENCES public.yitan_calls(id),owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 payload text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.yitan_draft_tasks (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),request_id varchar(100) UNIQUE NOT NULL,
 status varchar(20) NOT NULL DEFAULT 'queued',step varchar(20) NOT NULL DEFAULT 'queued',error_code varchar(60) NOT NULL DEFAULT '',
 lease_token varchar(36),lease_until timestamptz,transcription_id text,transcription_object text,
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX yitan_tasks_one_active ON public.yitan_draft_tasks(call_id) WHERE status IN ('queued','running');
CREATE TABLE public.yitan_writing_cache (
 id varchar(64) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),status varchar(20) NOT NULL,
 payload text NOT NULL DEFAULT '',usage jsonb NOT NULL DEFAULT '{}',task_id varchar(36) NOT NULL REFERENCES public.yitan_draft_tasks(id),
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.yitan_writing_runs (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),task_id varchar(36) NOT NULL REFERENCES public.yitan_draft_tasks(id),
 status varchar(20) NOT NULL,payload text NOT NULL,error_code varchar(60) NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.yitan_call_contexts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_audio_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_call_timelines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_draft_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_writing_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_writing_runs ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.yitan_call_contexts,public.yitan_audio_chunks,public.yitan_call_timelines,
 public.yitan_draft_tasks,public.yitan_writing_cache,public.yitan_writing_runs TO service_role;
REVOKE ALL ON public.yitan_call_contexts,public.yitan_audio_chunks,public.yitan_call_timelines,
 public.yitan_draft_tasks,public.yitan_writing_cache,public.yitan_writing_runs FROM anon,authenticated;

CREATE FUNCTION public.yitan_open_call(p_owner text,p_profile text,p_request text,p_photo text,p_topic text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.yitan_calls; result public.yitan_calls; call_id text:=gen_random_uuid()::text;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_call_'||p_owner,0));
 SELECT c.* INTO old FROM public.yitan_calls c JOIN public.yitan_call_contexts x ON x.call_id=c.id
 WHERE x.id=p_owner||':'||p_request AND c.owner_id=p_owner;
 IF FOUND THEN
  IF old.profile_id<>p_profile THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='request_conflict'; END IF;
  RETURN to_jsonb(old);
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.yitan_profiles WHERE id=p_profile AND owner_id=p_owner) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='profile_missing';
 END IF;
 IF p_photo IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.yitan_photos WHERE id=p_photo AND profile_id=p_profile AND owner_id=p_owner) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='photo_missing';
 END IF;
 UPDATE public.yitan_calls SET status='failed',failure_code='connection_expired',ended_at=now()
 WHERE owner_id=p_owner AND status='live' AND coalesce(heartbeat_at,created_at)<now()-interval '2 minutes';
 IF EXISTS(SELECT 1 FROM public.yitan_calls WHERE owner_id=p_owner AND status='live') THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='call_active';
 END IF;
 INSERT INTO public.yitan_calls(id,owner_id,profile_id) VALUES(call_id,p_owner,p_profile) RETURNING * INTO result;
 INSERT INTO public.yitan_call_contexts(id,owner_id,call_id,photo_id,topic) VALUES(p_owner||':'||p_request,p_owner,call_id,p_photo,p_topic);
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,p_owner,'consent','accepted');
 RETURN to_jsonb(result);
END;
$$;

CREATE FUNCTION public.yitan_enqueue_draft(p_owner text,p_call text,p_request text,p_replace boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_draft_tasks;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_draft_'||p_call,0));
 IF NOT EXISTS(SELECT 1 FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner AND status IN ('ready','failed')) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='call_not_ready';
 END IF;
 SELECT * INTO result FROM public.yitan_draft_tasks WHERE request_id=p_owner||':'||p_request;
 IF FOUND THEN
  IF result.call_id<>p_call THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='request_conflict'; END IF;
  RETURN to_jsonb(result);
 END IF;
 SELECT * INTO result FROM public.yitan_draft_tasks WHERE call_id=p_call AND status IN ('queued','running');
 IF FOUND THEN RETURN to_jsonb(result); END IF;
 IF NOT p_replace AND EXISTS(SELECT 1 FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='confirm_replace';
 END IF;
 INSERT INTO public.yitan_draft_tasks(id,owner_id,call_id,request_id)
 VALUES(gen_random_uuid()::text,p_owner,p_call,p_owner||':'||p_request) RETURNING * INTO result;
 RETURN to_jsonb(result);
END;
$$;

CREATE FUNCTION public.yitan_claim_draft(p_task text,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_draft_tasks;
BEGIN
 SELECT * INTO result FROM public.yitan_draft_tasks WHERE id=p_task FOR UPDATE SKIP LOCKED;
 IF NOT FOUND OR result.status NOT IN ('queued','running') OR (result.status='running' AND result.lease_until>now()) THEN RETURN NULL; END IF;
 UPDATE public.yitan_draft_tasks SET status='running',lease_token=p_token,lease_until=now()+interval '90 seconds',updated_at=now()
 WHERE id=p_task RETURNING * INTO result;
 RETURN to_jsonb(result);
END;
$$;

CREATE FUNCTION public.yitan_model_begin(p_id text,p_owner text,p_call text,p_task text,p_token text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_writing_cache; inserted integer;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND lease_until>now() AND status='running') THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='lease_lost';
 END IF;
 INSERT INTO public.yitan_writing_cache(id,owner_id,call_id,status,task_id)
 VALUES(p_id,p_owner,p_call,'pending',p_task) ON CONFLICT(id) DO NOTHING;
 GET DIAGNOSTICS inserted=ROW_COUNT;
 SELECT * INTO result FROM public.yitan_writing_cache WHERE id=p_id AND owner_id=p_owner;
 RETURN to_jsonb(result)||jsonb_build_object('created',inserted=1);
END;
$$;

REVOKE ALL ON FUNCTION public.yitan_open_call(text,text,text,text,text),public.yitan_enqueue_draft(text,text,text,boolean),
 public.yitan_claim_draft(text,text),public.yitan_model_begin(text,text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_open_call(text,text,text,text,text),public.yitan_enqueue_draft(text,text,text,boolean),
 public.yitan_claim_draft(text,text),public.yitan_model_begin(text,text,text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
