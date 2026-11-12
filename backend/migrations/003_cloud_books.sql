BEGIN;
CREATE TABLE IF NOT EXISTS public.yitan_profiles (
 id varchar(36) PRIMARY KEY, owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 elder_name varchar(40) NOT NULL, birth_year integer, hometown varchar(80) NOT NULL DEFAULT '',
 family_names varchar(200) NOT NULL DEFAULT '',topics_want text NOT NULL DEFAULT '',topics_avoid text NOT NULL DEFAULT '',
 source varchar(16) NOT NULL DEFAULT 'live', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS yitan_profiles_owner ON public.yitan_profiles(owner_id);
CREATE TABLE IF NOT EXISTS public.yitan_calls (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),status varchar(20) NOT NULL DEFAULT 'live',
 failure_code varchar(60) NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now(),ended_at timestamptz
);
CREATE INDEX IF NOT EXISTS yitan_calls_profile ON public.yitan_calls(profile_id,created_at);
CREATE TABLE IF NOT EXISTS public.yitan_book_settings (
 profile_id varchar(36) PRIMARY KEY REFERENCES public.yitan_profiles(id),title varchar(60) NOT NULL
);
CREATE TABLE IF NOT EXISTS public.yitan_chapters (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),title varchar(80) NOT NULL,
 review_status varchar(20) NOT NULL DEFAULT '待人工审校',created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS yitan_chapters_call ON public.yitan_chapters(call_id,created_at);
CREATE TABLE IF NOT EXISTS public.yitan_paragraphs (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 chapter_id varchar(36) NOT NULL REFERENCES public.yitan_chapters(id),position integer NOT NULL,
 text text NOT NULL,source_quote text NOT NULL DEFAULT '',score integer CHECK(score BETWEEN 1 AND 3),
 UNIQUE(chapter_id,position)
);
CREATE TABLE IF NOT EXISTS public.yitan_paragraph_evidence (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 paragraph_id varchar(36) NOT NULL REFERENCES public.yitan_paragraphs(id),source_line integer NOT NULL,quote text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.yitan_transcripts (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),track varchar(32) NOT NULL,text text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(call_id,track)
);
CREATE TABLE IF NOT EXISTS public.yitan_audio_assets (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),kind varchar(20) NOT NULL,
 relpath varchar(240) NOT NULL,size_bytes bigint NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(call_id,kind)
);
CREATE TABLE IF NOT EXISTS public.yitan_photos (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),original_name varchar(80) NOT NULL,
 content_type varchar(40) NOT NULL,size_bytes bigint NOT NULL,relpath varchar(240) NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_memory_items (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),
 kind varchar(20) NOT NULL,name varchar(80) NOT NULL,detail text NOT NULL DEFAULT '',source_quote text NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS public.yitan_fact_items (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),topic varchar(80) NOT NULL,
 detail text NOT NULL DEFAULT '',source_quote text NOT NULL DEFAULT '',status varchar(20) NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS public.yitan_imports (
 id varchar(64) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),source_sha256 varchar(64) NOT NULL,
 chapter_count integer NOT NULL,body_characters integer NOT NULL,source_book jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.yitan_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_book_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_chapters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_paragraphs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_paragraph_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_audio_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_photos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_memory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_fact_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_imports ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.yitan_profiles,public.yitan_calls,public.yitan_book_settings,
 public.yitan_chapters,public.yitan_paragraphs,public.yitan_paragraph_evidence,public.yitan_transcripts,
 public.yitan_audio_assets,public.yitan_photos,public.yitan_memory_items,public.yitan_fact_items,public.yitan_imports TO service_role;
REVOKE ALL ON public.yitan_profiles,public.yitan_calls,public.yitan_book_settings,
 public.yitan_chapters,public.yitan_paragraphs,public.yitan_paragraph_evidence,public.yitan_transcripts,
 public.yitan_audio_assets,public.yitan_photos,public.yitan_memory_items,public.yitan_fact_items,public.yitan_imports FROM anon,authenticated;

CREATE OR REPLACE FUNCTION public.yitan_import_book(p_import_id text,p_owner_id text,p_profile_id text,p_call_id text,p_name text,p_title text,p_source_sha256 text,p_book jsonb,p_transcript text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE prior public.yitan_imports; chapter jsonb; paragraph jsonb; evidence jsonb;
 chapter_id text; paragraph_id text; chapter_index integer:=0; paragraph_index integer; characters integer:=0;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_import_'||p_import_id,0));
 SELECT * INTO prior FROM public.yitan_imports WHERE id=p_import_id;
 IF FOUND THEN
  IF prior.owner_id<>p_owner_id OR prior.source_sha256<>p_source_sha256 THEN
   RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='import_conflict';
  END IF;
  RETURN to_jsonb(prior);
 END IF;
 IF jsonb_typeof(p_book->'chapters') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='empty_book';
 END IF;
 IF jsonb_array_length(p_book->'chapters')=0 OR length(p_transcript)=0 OR p_source_sha256 !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_source';
 END IF;
 INSERT INTO public.yitan_profiles(id,owner_id,elder_name,source) VALUES(p_profile_id,p_owner_id,p_name,'imported');
 INSERT INTO public.yitan_book_settings(profile_id,title) VALUES(p_profile_id,p_title);
 INSERT INTO public.yitan_calls(id,owner_id,profile_id,status,ended_at) VALUES(p_call_id,p_owner_id,p_profile_id,'ready',now());
 INSERT INTO public.yitan_transcripts(id,owner_id,call_id,track,text) VALUES(gen_random_uuid()::text,p_owner_id,p_call_id,'full',p_transcript);
 FOR chapter IN SELECT value FROM jsonb_array_elements(p_book->'chapters') LOOP
  chapter_id:=gen_random_uuid()::text;
  INSERT INTO public.yitan_chapters(id,owner_id,call_id,title,created_at)
  VALUES(chapter_id,p_owner_id,p_call_id,chapter->>'title',now()+chapter_index*interval '1 millisecond');
  chapter_index:=chapter_index+1;
  paragraph_index:=0;
  FOR paragraph IN SELECT value FROM jsonb_array_elements(chapter->'paragraphs') LOOP
   paragraph_id:=gen_random_uuid()::text;
   INSERT INTO public.yitan_paragraphs(id,owner_id,chapter_id,position,text,source_quote)
   VALUES(paragraph_id,p_owner_id,chapter_id,paragraph_index,paragraph->>'text',coalesce(paragraph->>'source_quote',''));
   paragraph_index:=paragraph_index+1;
   characters:=characters+length(paragraph->>'text');
   FOR evidence IN SELECT value FROM jsonb_array_elements(coalesce(paragraph->'evidence','[]'::jsonb)) LOOP
    IF evidence ? 'line' AND evidence ? 'quote' THEN
     INSERT INTO public.yitan_paragraph_evidence(id,owner_id,paragraph_id,source_line,quote)
     VALUES(gen_random_uuid()::text,p_owner_id,paragraph_id,(evidence->>'line')::integer,evidence->>'quote');
    ELSE
     RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_evidence';
    END IF;
   END LOOP;
  END LOOP;
 END LOOP;
 INSERT INTO public.yitan_imports(id,owner_id,profile_id,source_sha256,chapter_count,body_characters,source_book)
 VALUES(p_import_id,p_owner_id,p_profile_id,p_source_sha256,chapter_index,characters,p_book) RETURNING * INTO prior;
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,p_owner_id,'book_imported',p_import_id);
 RETURN to_jsonb(prior);
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_import_book(text,text,text,text,text,text,text,jsonb,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_import_book(text,text,text,text,text,text,text,jsonb,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
