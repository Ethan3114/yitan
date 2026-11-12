BEGIN;
CREATE FUNCTION public.yitan_call_checkpoint(p_owner text,p_call text,p_token text,p_chunks jsonb,p_texts jsonb,p_timeline text,p_status text DEFAULT NULL,p_error text DEFAULT '')
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE part jsonb; track text; content text;
BEGIN
 PERFORM 1 FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner AND connection_token=p_token AND status='live' FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 FOR part IN SELECT value FROM jsonb_array_elements(p_chunks) LOOP
  INSERT INTO public.yitan_audio_chunks(id,owner_id,call_id,kind,sequence,file_id,size_bytes)
  VALUES(gen_random_uuid()::text,p_owner,p_call,part->>'kind',(part->>'sequence')::integer,part->>'file_id',(part->>'size_bytes')::bigint)
  ON CONFLICT(call_id,kind,sequence) DO NOTHING;
  INSERT INTO public.yitan_audio_assets(id,owner_id,call_id,kind,relpath,size_bytes)
  VALUES(gen_random_uuid()::text,p_owner,p_call,part->>'kind','chunks', (part->>'size_bytes')::bigint)
  ON CONFLICT(call_id,kind) DO UPDATE SET size_bytes=(SELECT sum(size_bytes) FROM public.yitan_audio_chunks WHERE call_id=p_call AND kind=part->>'kind');
 END LOOP;
 FOR track,content IN SELECT key,value FROM jsonb_each_text(p_texts) LOOP
  INSERT INTO public.yitan_transcripts(id,owner_id,call_id,track,text)
  VALUES(gen_random_uuid()::text,p_owner,p_call,track,content)
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

CREATE FUNCTION public.yitan_finish_transcript(p_owner text,p_call text,p_task text,p_token text,p_text text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM 1 FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND status='running' AND lease_until>now() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO public.yitan_transcripts(id,owner_id,call_id,track,text)
 VALUES(gen_random_uuid()::text,p_owner,p_call,'full',p_text)
 ON CONFLICT(call_id,track) DO UPDATE SET text=excluded.text;
 UPDATE public.yitan_draft_tasks SET step='writing',updated_at=now() WHERE id=p_task;
 INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'transcription_completed',p_owner,'server',p_call);
 RETURN true;
END;
$$;

CREATE FUNCTION public.yitan_commit_draft(p_owner text,p_call text,p_task text,p_token text,p_book jsonb,p_previous text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE chapter jsonb; paragraph jsonb; evidence jsonb; memory jsonb; fact jsonb;
 chapter_id text; paragraph_id text; chapter_index integer:=0; paragraph_index integer; profile text;
BEGIN
 PERFORM 1 FROM public.yitan_draft_tasks WHERE id=p_task AND owner_id=p_owner AND call_id=p_call AND lease_token=p_token AND lease_until>now() AND status='running' FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 IF jsonb_typeof(p_book->'chapters') IS DISTINCT FROM 'array' OR jsonb_array_length(p_book->'chapters')=0 THEN RAISE EXCEPTION 'empty_book'; END IF;
 SELECT profile_id INTO profile FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner;
 IF p_previous<>'' THEN
  INSERT INTO public.yitan_text_editions(id,owner_id,call_id,request_id,payload)
  VALUES(gen_random_uuid()::text,p_owner,p_call,'draft:'||p_task,p_previous);
 END IF;
 DELETE FROM public.yitan_paragraph_evidence WHERE paragraph_id IN(SELECT id FROM public.yitan_paragraphs WHERE chapter_id IN(SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner));
 DELETE FROM public.yitan_paragraphs WHERE chapter_id IN(SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner);
 DELETE FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner;
 DELETE FROM public.yitan_memory_items WHERE call_id=p_call AND owner_id=p_owner;
 DELETE FROM public.yitan_fact_items WHERE call_id=p_call AND owner_id=p_owner;
 FOR chapter IN SELECT value FROM jsonb_array_elements(p_book->'chapters') LOOP
  chapter_id:=gen_random_uuid()::text;
  INSERT INTO public.yitan_chapters(id,owner_id,call_id,title,created_at) VALUES(chapter_id,p_owner,p_call,chapter->>'title',now()+chapter_index*interval '1 second');
  chapter_index:=chapter_index+1; paragraph_index:=0;
  FOR paragraph IN SELECT value FROM jsonb_array_elements(chapter->'paragraphs') LOOP
   paragraph_id:=gen_random_uuid()::text;
   INSERT INTO public.yitan_paragraphs(id,owner_id,chapter_id,position,text,source_quote)
   VALUES(paragraph_id,p_owner,chapter_id,paragraph_index,paragraph->>'text',paragraph->>'source_quote');
   paragraph_index:=paragraph_index+1;
   FOR evidence IN SELECT value FROM jsonb_array_elements(paragraph->'evidence') LOOP
    INSERT INTO public.yitan_paragraph_evidence(id,owner_id,paragraph_id,source_line,quote)
    VALUES(gen_random_uuid()::text,p_owner,paragraph_id,(evidence->>'line')::integer,evidence->>'quote');
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
REVOKE ALL ON FUNCTION public.yitan_call_checkpoint(text,text,text,jsonb,jsonb,text,text,text),public.yitan_finish_transcript(text,text,text,text,text),public.yitan_commit_draft(text,text,text,text,jsonb,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_call_checkpoint(text,text,text,jsonb,jsonb,text,text,text),public.yitan_finish_transcript(text,text,text,text,text),public.yitan_commit_draft(text,text,text,text,jsonb,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
