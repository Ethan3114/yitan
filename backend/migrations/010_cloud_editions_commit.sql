BEGIN;
ALTER TABLE public.yitan_text_editions ADD COLUMN IF NOT EXISTS source_hash varchar(64);

CREATE OR REPLACE FUNCTION public.yitan_call_checkpoint(p_owner text,p_call text,p_token text,p_chunks jsonb,p_texts jsonb,p_timeline text,p_status text DEFAULT NULL,p_error text DEFAULT '')
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE part jsonb; track_name text; content text; active_call public.yitan_calls; existing public.yitan_audio_chunks;
 file public.yitan_private_files; expected_sequence integer;
BEGIN
 SELECT * INTO active_call FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner AND connection_token=p_token FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF p_status IS NOT NULL AND p_status NOT IN ('ready','failed') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_call_status'; END IF;
 IF active_call.status<>'live' THEN
  IF p_status IS NULL OR active_call.status<>p_status OR active_call.failure_code<>p_error THEN RETURN false; END IF;
  FOR part IN SELECT value FROM jsonb_array_elements(p_chunks) LOOP
   IF NOT EXISTS(SELECT 1 FROM public.yitan_audio_chunks WHERE call_id=p_call AND owner_id=p_owner AND kind=part->>'kind' AND sequence=(part->>'sequence')::integer AND file_id=part->>'file_id' AND size_bytes=(part->>'size_bytes')::bigint) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='checkpoint_conflict';
   END IF;
  END LOOP;
  FOR track_name,content IN SELECT key,value FROM jsonb_each_text(p_texts) LOOP
   IF NOT EXISTS(SELECT 1 FROM public.yitan_transcripts WHERE call_id=p_call AND owner_id=p_owner AND track=track_name AND text=content) THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='checkpoint_conflict';
   END IF;
  END LOOP;
  RETURN true;
 END IF;
 FOR part IN SELECT value FROM jsonb_array_elements(p_chunks) LOOP
  IF part->>'kind' IS NULL OR part->>'kind' NOT IN ('user','assistant','conversation') OR (part->>'sequence')::integer<0 OR (part->>'size_bytes')::bigint<=0 OR (part->>'size_bytes')::bigint%2<>0 THEN
   RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_audio_chunk';
  END IF;
  SELECT * INTO file FROM public.yitan_private_files WHERE id=part->>'file_id' AND owner_id=p_owner AND call_id=p_call AND profile_id=active_call.profile_id AND size_bytes=(part->>'size_bytes')::bigint;
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='audio_file_owner_mismatch'; END IF;
  SELECT * INTO existing FROM public.yitan_audio_chunks WHERE call_id=p_call AND kind=part->>'kind' AND sequence=(part->>'sequence')::integer;
  IF FOUND THEN
   IF existing.owner_id<>p_owner OR existing.file_id<>part->>'file_id' OR existing.size_bytes<>(part->>'size_bytes')::bigint THEN
    RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='checkpoint_conflict';
   END IF;
  ELSE
   SELECT coalesce(max(sequence),-1)+1 INTO expected_sequence FROM public.yitan_audio_chunks WHERE call_id=p_call AND kind=part->>'kind';
   IF expected_sequence<>(part->>'sequence')::integer THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='audio_sequence_gap'; END IF;
   INSERT INTO public.yitan_audio_chunks(id,owner_id,call_id,kind,sequence,file_id,size_bytes)
   VALUES(gen_random_uuid()::text,p_owner,p_call,part->>'kind',(part->>'sequence')::integer,part->>'file_id',(part->>'size_bytes')::bigint);
  END IF;
  INSERT INTO public.yitan_audio_assets(id,owner_id,call_id,kind,relpath,size_bytes)
  VALUES(gen_random_uuid()::text,p_owner,p_call,part->>'kind','chunks', (part->>'size_bytes')::bigint)
  ON CONFLICT(call_id,kind) DO UPDATE SET size_bytes=(SELECT sum(size_bytes) FROM public.yitan_audio_chunks WHERE call_id=p_call AND kind=part->>'kind');
 END LOOP;
 FOR track_name,content IN SELECT key,value FROM jsonb_each_text(p_texts) LOOP
  IF track_name NOT IN ('realtime_user','realtime_assistant') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_realtime_track'; END IF;
  INSERT INTO public.yitan_transcripts(id,owner_id,call_id,track,text)
  VALUES(gen_random_uuid()::text,p_owner,p_call,track_name,content)
  ON CONFLICT(call_id,track) DO UPDATE SET text=excluded.text;
 END LOOP;
 IF p_timeline<>'' THEN
  INSERT INTO public.yitan_call_timelines(call_id,owner_id,payload) VALUES(p_call,p_owner,p_timeline)
  ON CONFLICT(call_id) DO UPDATE SET payload=excluded.payload,updated_at=now();
 END IF;
 UPDATE public.yitan_calls SET heartbeat_at=now(),status=coalesce(p_status,status),failure_code=p_error,ended_at=CASE WHEN p_status IS NULL THEN ended_at ELSE now() END WHERE id=p_call;
 IF p_status IS NOT NULL THEN
  INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id,duration_seconds,stage,error_code)
  VALUES(gen_random_uuid()::text,CASE WHEN p_status='ready' THEN 'recording_saved' ELSE 'operation_failed' END,p_owner,'server',p_call,
   (SELECT coalesce(sum(size_bytes),0)/32000 FROM public.yitan_audio_chunks WHERE call_id=p_call AND kind='user'),'call',p_error);
 END IF;
 RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_commit_draft(p_owner text,p_call text,p_task text,p_token text,p_book jsonb,p_previous text,p_accepted text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE chapter jsonb; paragraph jsonb; evidence jsonb; memory jsonb; fact jsonb; task public.yitan_draft_tasks;
 v_chapter_id text; v_paragraph_id text; chapter_index integer:=0; paragraph_index integer; profile text; accepted_source_hash text;
BEGIN
 accepted_source_hash:=encode(sha256(convert_to(p_book::text,'UTF8')),'hex');
 SELECT * INTO task FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF task.status='succeeded' THEN
  IF EXISTS(SELECT 1 FROM public.yitan_text_editions WHERE owner_id=p_owner AND call_id=p_call AND request_id='accepted:'||p_task AND source_hash=accepted_source_hash) THEN RETURN true; END IF;
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='commit_conflict';
 END IF;
 IF task.status<>'running' OR task.lease_token IS DISTINCT FROM p_token OR task.lease_until<=now() THEN RETURN false; END IF;
 SELECT profile_id INTO profile FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF jsonb_typeof(p_book->'chapters') IS DISTINCT FROM 'array' OR jsonb_array_length(p_book->'chapters')=0 OR length(p_accepted)<100 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_book'; END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner) AND p_previous='' THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='previous_edition_required'; END IF;
 IF p_previous<>'' THEN
  INSERT INTO public.yitan_text_editions(id,owner_id,call_id,request_id,payload)
  VALUES(gen_random_uuid()::text,p_owner,p_call,'previous:'||p_task,p_previous);
 END IF;
 INSERT INTO public.yitan_text_editions(id,owner_id,call_id,request_id,payload,source_hash)
 VALUES(gen_random_uuid()::text,p_owner,p_call,'accepted:'||p_task,p_accepted,accepted_source_hash);
 DELETE FROM public.yitan_paragraph_evidence WHERE paragraph_id IN(SELECT id FROM public.yitan_paragraphs WHERE chapter_id IN(SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner));
 DELETE FROM public.yitan_paragraphs WHERE chapter_id IN(SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner);
 DELETE FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner;
 DELETE FROM public.yitan_memory_items WHERE call_id=p_call AND owner_id=p_owner;
 DELETE FROM public.yitan_fact_items WHERE call_id=p_call AND owner_id=p_owner;
 FOR chapter IN SELECT value FROM jsonb_array_elements(p_book->'chapters') LOOP
  IF jsonb_typeof(chapter->'paragraphs') IS DISTINCT FROM 'array' OR jsonb_array_length(chapter->'paragraphs')=0 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='empty_chapter'; END IF;
  v_chapter_id:=gen_random_uuid()::text;
  INSERT INTO public.yitan_chapters(id,owner_id,call_id,title,created_at) VALUES(v_chapter_id,p_owner,p_call,chapter->>'title',now()+chapter_index*interval '1 second');
  chapter_index:=chapter_index+1; paragraph_index:=0;
  FOR paragraph IN SELECT value FROM jsonb_array_elements(chapter->'paragraphs') LOOP
   IF length(trim(paragraph->>'text'))=0 OR jsonb_typeof(paragraph->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(paragraph->'evidence')=0 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_paragraph'; END IF;
   v_paragraph_id:=gen_random_uuid()::text;
   INSERT INTO public.yitan_paragraphs(id,owner_id,chapter_id,position,text,source_quote)
   VALUES(v_paragraph_id,p_owner,v_chapter_id,paragraph_index,paragraph->>'text',paragraph->>'source_quote');
   paragraph_index:=paragraph_index+1;
   FOR evidence IN SELECT value FROM jsonb_array_elements(paragraph->'evidence') LOOP
    INSERT INTO public.yitan_paragraph_evidence(id,owner_id,paragraph_id,source_line,quote)
    VALUES(gen_random_uuid()::text,p_owner,v_paragraph_id,(evidence->>'line')::integer,evidence->>'quote');
   END LOOP;
  END LOOP;
 END LOOP;
 FOR memory IN SELECT value FROM jsonb_array_elements(p_book->'memories') LOOP
  INSERT INTO public.yitan_memory_items(id,owner_id,profile_id,call_id,kind,name,detail,source_quote)
  VALUES(gen_random_uuid()::text,p_owner,profile,p_call,memory->>'kind',left(memory->>'name',80),memory->>'detail',memory->>'source_quote');
 END LOOP;
 FOR fact IN SELECT value FROM jsonb_array_elements(p_book->'uncertain') LOOP
  INSERT INTO public.yitan_fact_items(id,owner_id,call_id,topic,detail,source_quote)
  VALUES(gen_random_uuid()::text,p_owner,p_call,left(fact->>'topic',80),fact->>'detail',fact->>'source_quote');
 END LOOP;
 UPDATE public.yitan_draft_tasks SET status='succeeded',step='done',lease_token=NULL,lease_until=NULL,updated_at=now() WHERE id=p_task;
 INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'memoir_completed',p_owner,'server',p_call);
 RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_commit_draft(text,text,text,text,jsonb,text) FROM service_role;
REVOKE ALL ON FUNCTION public.yitan_commit_draft(text,text,text,text,jsonb,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_commit_draft(text,text,text,text,jsonb,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
