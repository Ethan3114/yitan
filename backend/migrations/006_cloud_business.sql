BEGIN;
CREATE TABLE public.yitan_family_publications (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),request_id varchar(160) NOT NULL UNIQUE,
 active boolean NOT NULL DEFAULT true,payload text NOT NULL,content_hash varchar(64) NOT NULL,chapter_ids jsonb NOT NULL,photo_ids jsonb NOT NULL,
 allow_audio boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX yitan_family_one_active ON public.yitan_family_publications(profile_id) WHERE active;
CREATE TABLE public.yitan_family_members (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),name varchar(40) NOT NULL,
 session_hash varchar(64) NOT NULL UNIQUE,expires_at timestamptz NOT NULL,
 allow_audio boolean NOT NULL DEFAULT false,revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE public.yitan_family_invites (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),request_id varchar(160) NOT NULL UNIQUE,
 token_hash varchar(64) NOT NULL UNIQUE,allow_audio boolean NOT NULL DEFAULT false,revoked boolean NOT NULL DEFAULT false,
 expires_at timestamptz NOT NULL,member_id varchar(36) REFERENCES public.yitan_family_members(id),redeem_request varchar(80) NOT NULL DEFAULT ''
);
CREATE TABLE public.yitan_family_comments (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 publication_id varchar(36) NOT NULL REFERENCES public.yitan_family_publications(id),
 member_id varchar(36) NOT NULL REFERENCES public.yitan_family_members(id),chapter_id varchar(36) NOT NULL,
 request_id varchar(160) NOT NULL UNIQUE,payload text NOT NULL,content_hash varchar(64) NOT NULL,
 deleted boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.yitan_text_editions (
 id varchar(36) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),request_id varchar(200) NOT NULL UNIQUE,
 payload text NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.yitan_family_publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_family_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_family_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_family_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_text_editions ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.yitan_family_publications,public.yitan_family_members,
 public.yitan_family_invites,public.yitan_family_comments,public.yitan_text_editions TO service_role;
REVOKE ALL ON public.yitan_family_publications,public.yitan_family_members,
 public.yitan_family_invites,public.yitan_family_comments,public.yitan_text_editions FROM anon,authenticated;

CREATE FUNCTION public.yitan_create_profile(p_owner text,p_id text,p_data jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE result public.yitan_profiles;
BEGIN
 INSERT INTO public.yitan_profiles(id,owner_id,elder_name,birth_year,hometown,family_names,topics_want,topics_avoid)
 VALUES(p_id,p_owner,p_data->>'elder_name',(p_data->>'birth_year')::integer,coalesce(p_data->>'hometown',''),coalesce(p_data->>'family_names',''),coalesce(p_data->>'topics_want',''),coalesce(p_data->>'topics_avoid','')) RETURNING * INTO result;
 INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'profile_created',p_owner,'server',p_id);
 RETURN to_jsonb(result);
END;
$$;
CREATE FUNCTION public.yitan_edit_book_title(p_owner text,p_profile text,p_title text)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM 1 FROM public.yitan_profiles WHERE id=p_profile AND owner_id=p_owner FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 INSERT INTO public.yitan_book_settings(profile_id,title) VALUES(p_profile,p_title)
 ON CONFLICT(profile_id) DO UPDATE SET title=EXCLUDED.title;
 RETURN p_title;
END;
$$;

CREATE FUNCTION public.yitan_family_selection(p_owner text,p_profile text,p_chapters jsonb,p_photos jsonb,p_audio boolean)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT CASE WHEN jsonb_array_length(p_chapters)>0
 AND (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(p_chapters))=jsonb_array_length(p_chapters)
 AND (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(p_photos))=jsonb_array_length(p_photos)
 AND (SELECT count(*) FROM public.yitan_chapters c JOIN public.yitan_calls t ON t.id=c.call_id
      WHERE c.owner_id=p_owner AND t.profile_id=p_profile AND p_chapters ? c.id)=jsonb_array_length(p_chapters)
 AND (SELECT count(*) FROM public.yitan_photos WHERE owner_id=p_owner AND profile_id=p_profile AND p_photos ? id)=jsonb_array_length(p_photos)
 THEN jsonb_build_object('title',coalesce((SELECT title FROM public.yitan_book_settings WHERE profile_id=p_profile),p.elder_name||'的故事'),
  'allow_audio',p_audio,
  'chapters',(SELECT jsonb_agg(jsonb_build_object('id',c.id,'title',c.title,'call_id',c.call_id,
     'paragraphs',coalesce((SELECT jsonb_agg(x.text ORDER BY x.position) FROM public.yitan_paragraphs x WHERE x.chapter_id=c.id AND x.owner_id=p_owner),'[]'::jsonb)) ORDER BY s.position)
     FROM jsonb_array_elements_text(p_chapters) WITH ORDINALITY s(id,position) JOIN public.yitan_chapters c ON c.id=s.id),
  'photos',coalesce((SELECT jsonb_agg(jsonb_build_object('id',f.id,'name',f.original_name) ORDER BY s.position)
     FROM jsonb_array_elements_text(p_photos) WITH ORDINALITY s(id,position) JOIN public.yitan_photos f ON f.id=s.id),'[]'::jsonb))
 ELSE NULL END
 FROM public.yitan_profiles p WHERE p.id=p_profile AND p.owner_id=p_owner;
$$;

CREATE FUNCTION public.yitan_publish_family(p_owner text,p_profile text,p_request text,p_id text,p_book jsonb,p_payload text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE old public.yitan_family_publications; current_book jsonb; chapters jsonb; photos jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_family_'||p_profile,0));
 SELECT * INTO old FROM public.yitan_family_publications WHERE request_id=p_profile||':'||p_request AND owner_id=p_owner;
 IF FOUND THEN
  IF old.content_hash<>encode(sha256(convert_to(p_book::text,'UTF8')),'hex') THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='request_conflict'; END IF;
  RETURN jsonb_build_object('id',old.id);
 END IF;
 chapters:=(SELECT jsonb_agg(c->>'id') FROM jsonb_array_elements(p_book->'chapters') c);
 photos:=coalesce((SELECT jsonb_agg(c->>'id') FROM jsonb_array_elements(p_book->'photos') c),'[]'::jsonb);
 current_book:=public.yitan_family_selection(p_owner,p_profile,chapters,photos,(p_book->>'allow_audio')::boolean);
 IF current_book IS NULL OR current_book<>p_book THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='preview_changed'; END IF;
 UPDATE public.yitan_family_publications SET active=false WHERE profile_id=p_profile AND owner_id=p_owner AND active;
 INSERT INTO public.yitan_family_publications(id,owner_id,profile_id,request_id,payload,content_hash,chapter_ids,photo_ids,allow_audio)
 VALUES(p_id,p_owner,p_profile,p_profile||':'||p_request,p_payload,encode(sha256(convert_to(p_book::text,'UTF8')),'hex'),chapters,photos,(p_book->>'allow_audio')::boolean);
 INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'family_publication_created',p_owner,'server',p_id);
 RETURN jsonb_build_object('id',p_id);
END;
$$;

CREATE FUNCTION public.yitan_invite_family(p_owner text,p_profile text,p_request text,p_id text,p_token_hash text,p_audio boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE invitation public.yitan_family_invites; publication public.yitan_family_publications;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_family_'||p_profile,0));
 SELECT * INTO publication FROM public.yitan_family_publications WHERE profile_id=p_profile AND owner_id=p_owner AND active;
 IF NOT FOUND OR (p_audio AND NOT publication.allow_audio) THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='publication_required'; END IF;
 SELECT * INTO invitation FROM public.yitan_family_invites WHERE request_id=p_profile||':'||p_request AND owner_id=p_owner;
 IF FOUND THEN
  IF invitation.allow_audio<>p_audio THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='request_conflict'; END IF;
  IF invitation.revoked OR invitation.member_id IS NOT NULL OR invitation.expires_at<=now() THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='invite_closed'; END IF;
  RETURN to_jsonb(invitation);
 END IF;
 INSERT INTO public.yitan_family_invites(id,owner_id,profile_id,request_id,token_hash,allow_audio,expires_at)
 VALUES(p_id,p_owner,p_profile,p_profile||':'||p_request,p_token_hash,p_audio,now()+interval '7 days') RETURNING * INTO invitation;
 RETURN to_jsonb(invitation);
END;
$$;

CREATE FUNCTION public.yitan_redeem_family(p_hash text,p_name text,p_request text,p_member_id text,p_session_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE invitation public.yitan_family_invites; member public.yitan_family_members;
BEGIN
 SELECT * INTO invitation FROM public.yitan_family_invites WHERE token_hash=p_hash AND NOT revoked AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='invite_closed'; END IF;
 IF invitation.member_id IS NOT NULL THEN
  IF invitation.redeem_request<>p_request THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='invite_used'; END IF;
  SELECT * INTO member FROM public.yitan_family_members WHERE id=invitation.member_id AND NOT revoked AND expires_at>now();
  IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='member_removed'; END IF;
  RETURN to_jsonb(member);
 END IF;
 INSERT INTO public.yitan_family_members(id,owner_id,profile_id,name,session_hash,expires_at,allow_audio)
 VALUES(p_member_id,invitation.owner_id,invitation.profile_id,p_name,p_session_hash,now()+interval '30 days',invitation.allow_audio) RETURNING * INTO member;
 UPDATE public.yitan_family_invites SET member_id=member.id,redeem_request=p_request WHERE id=invitation.id;
 RETURN to_jsonb(member);
END;
$$;

CREATE FUNCTION public.yitan_comment_family(p_session_hash text,p_publication text,p_chapter text,p_request text,p_id text,p_payload text,p_content_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE member public.yitan_family_members; publication public.yitan_family_publications; old public.yitan_family_comments;
BEGIN
 SELECT * INTO member FROM public.yitan_family_members WHERE session_hash=p_session_hash AND NOT revoked AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='member_removed'; END IF;
 SELECT * INTO publication FROM public.yitan_family_publications WHERE id=p_publication AND profile_id=member.profile_id AND owner_id=member.owner_id AND active FOR SHARE;
 IF NOT FOUND OR NOT publication.chapter_ids ? p_chapter THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='publication_changed'; END IF;
 SELECT * INTO old FROM public.yitan_family_comments WHERE request_id=member.id||':'||p_request;
 IF FOUND THEN
  IF old.publication_id<>p_publication OR old.chapter_id<>p_chapter OR old.content_hash<>p_content_hash THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='request_conflict'; END IF;
  RETURN jsonb_build_object('id',old.id);
 END IF;
 INSERT INTO public.yitan_family_comments(id,owner_id,publication_id,member_id,chapter_id,request_id,payload,content_hash)
 VALUES(p_id,member.owner_id,p_publication,member.id,p_chapter,member.id||':'||p_request,p_payload,p_content_hash);
 RETURN jsonb_build_object('id',p_id);
END;
$$;

CREATE FUNCTION public.yitan_record_family_read(p_session_hash text,p_publication text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE member public.yitan_family_members;
BEGIN
 SELECT * INTO member FROM public.yitan_family_members WHERE session_hash=p_session_hash AND NOT revoked AND expires_at>now() FOR UPDATE;
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.yitan_family_publications WHERE id=p_publication AND profile_id=member.profile_id AND owner_id=member.owner_id AND active) THEN RETURN false; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.yitan_analytics_events WHERE owner_id=member.owner_id AND name='family_read' AND entity_id=p_publication AND created_at>now()-interval '30 minutes') THEN
  INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'family_read',member.owner_id,'server',p_publication);
 END IF;
 RETURN true;
END;
$$;

CREATE FUNCTION public.yitan_restore_edition(p_owner text,p_call text,p_edition text,p_request text,p_previous text,p_chapters jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE c jsonb; p jsonb; r jsonb; cid text; pid text; position integer; chapter_position integer:=0;
BEGIN
 PERFORM 1 FROM public.yitan_calls WHERE id=p_call AND owner_id=p_owner FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='call_missing'; END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_text_editions WHERE request_id=p_call||':'||p_edition||':'||p_request AND owner_id=p_owner) THEN RETURN jsonb_build_object('ok',true); END IF;
 IF EXISTS(SELECT 1 FROM public.yitan_draft_tasks WHERE call_id=p_call AND status IN ('queued','running')) THEN RAISE EXCEPTION USING ERRCODE='55000',MESSAGE='writing_active'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.yitan_text_editions WHERE id=p_edition AND call_id=p_call AND owner_id=p_owner) THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='edition_missing'; END IF;
 INSERT INTO public.yitan_text_editions(id,owner_id,call_id,request_id,payload) VALUES(gen_random_uuid()::text,p_owner,p_call,p_call||':'||p_edition||':'||p_request,p_previous);
 DELETE FROM public.yitan_paragraph_evidence WHERE owner_id=p_owner AND paragraph_id IN (SELECT id FROM public.yitan_paragraphs WHERE chapter_id IN (SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner));
 DELETE FROM public.yitan_paragraphs WHERE owner_id=p_owner AND chapter_id IN (SELECT id FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner);
 DELETE FROM public.yitan_chapters WHERE call_id=p_call AND owner_id=p_owner;
 FOR c IN SELECT value FROM jsonb_array_elements(p_chapters) LOOP
  cid:=gen_random_uuid()::text;
  INSERT INTO public.yitan_chapters(id,owner_id,call_id,title,created_at) VALUES(cid,p_owner,p_call,c->>'title',now()+chapter_position*interval '1 millisecond');
  chapter_position:=chapter_position+1; position:=0;
  FOR p IN SELECT value FROM jsonb_array_elements(c->'paragraphs') LOOP
   pid:=gen_random_uuid()::text;
   INSERT INTO public.yitan_paragraphs(id,owner_id,chapter_id,position,text,source_quote,score) VALUES(pid,p_owner,cid,position,p->>'text',coalesce(p->>'source_quote',''),(p->>'score')::integer);
   position:=position+1;
   FOR r IN SELECT value FROM jsonb_array_elements(coalesce(p->'source_quotes','[]'::jsonb)) LOOP
    INSERT INTO public.yitan_paragraph_evidence(id,owner_id,paragraph_id,source_line,quote) VALUES(gen_random_uuid()::text,p_owner,pid,(r->>'line')::integer,r->>'quote');
   END LOOP;
  END LOOP;
 END LOOP;
 RETURN jsonb_build_object('ok',true);
END;
$$;

REVOKE ALL ON FUNCTION public.yitan_create_profile(text,text,jsonb),public.yitan_edit_book_title(text,text,text),
 public.yitan_family_selection(text,text,jsonb,jsonb,boolean),public.yitan_publish_family(text,text,text,text,jsonb,text),
 public.yitan_invite_family(text,text,text,text,text,boolean),public.yitan_redeem_family(text,text,text,text,text),
 public.yitan_comment_family(text,text,text,text,text,text,text),public.yitan_record_family_read(text,text),public.yitan_restore_edition(text,text,text,text,text,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_create_profile(text,text,jsonb),public.yitan_edit_book_title(text,text,text),
 public.yitan_family_selection(text,text,jsonb,jsonb,boolean),public.yitan_publish_family(text,text,text,text,jsonb,text),
 public.yitan_invite_family(text,text,text,text,text,boolean),public.yitan_redeem_family(text,text,text,text,text),
 public.yitan_comment_family(text,text,text,text,text,text,text),public.yitan_record_family_read(text,text),public.yitan_restore_edition(text,text,text,text,text,jsonb) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
