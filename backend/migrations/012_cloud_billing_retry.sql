BEGIN;
CREATE OR REPLACE FUNCTION public.yitan_enqueue_draft(p_owner text,p_call text,p_request text,p_replace boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_draft_tasks; transcription public.yitan_draft_tasks;
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
 SELECT * INTO result FROM public.yitan_draft_tasks WHERE call_id=p_call AND owner_id=p_owner AND status IN ('queued','running');
 IF FOUND THEN RETURN to_jsonb(result); END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_writing_cache WHERE call_id=p_call AND owner_id=p_owner AND status='pending') THEN
  RAISE EXCEPTION USING ERRCODE='P0101',MESSAGE='model_outcome_unknown';
 END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE call_id=p_call AND owner_id=p_owner AND step='transcribe_pending' AND transcription_id IS NULL) THEN
  RAISE EXCEPTION USING ERRCODE='P0102',MESSAGE='transcription_outcome_unknown';
 END IF;
 IF NOT p_replace AND EXISTS(SELECT 1 FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='confirm_replace';
 END IF;
 SELECT * INTO transcription FROM public.yitan_draft_tasks
 WHERE call_id=p_call AND owner_id=p_owner AND transcription_id IS NOT NULL ORDER BY updated_at DESC,id DESC LIMIT 1;
 INSERT INTO public.yitan_draft_tasks(id,owner_id,call_id,request_id,transcription_id,transcription_object,step)
 VALUES(gen_random_uuid()::text,p_owner,p_call,p_owner||':'||p_request,transcription.transcription_id,transcription.transcription_object,
  CASE WHEN transcription.transcription_id IS NULL THEN 'queued' ELSE 'transcribing' END) RETURNING * INTO result;
 RETURN to_jsonb(result);
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_enqueue_draft(text,text,text,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_enqueue_draft(text,text,text,boolean) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
