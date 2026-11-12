BEGIN;

ALTER TABLE public.yitan_accounts ADD COLUMN IF NOT EXISTS analytics_excluded boolean NOT NULL DEFAULT false;
ALTER TABLE public.yitan_analytics_events ADD COLUMN IF NOT EXISTS session_id varchar(36);
ALTER TABLE public.yitan_analytics_events ADD COLUMN IF NOT EXISTS page_id varchar(32);
ALTER TABLE public.yitan_analytics_events ADD COLUMN IF NOT EXISTS page_visit_id varchar(36);
ALTER TABLE public.yitan_analytics_events ADD COLUMN IF NOT EXISTS duration_ms integer;
CREATE INDEX IF NOT EXISTS yitan_events_session_time ON public.yitan_analytics_events(session_id,created_at);
CREATE TABLE IF NOT EXISTS public.yitan_analytics_browser_sessions (
 id varchar(36) PRIMARY KEY,
 visitor_id varchar(64) NOT NULL REFERENCES public.yitan_analytics_visitors(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.yitan_analytics_browser_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.yitan_analytics_browser_sessions FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.yitan_analytics_browser_sessions TO service_role;

DROP FUNCTION IF EXISTS public.yitan_event_ingest(text,text,text,text,text,text,text);
CREATE OR REPLACE FUNCTION public.yitan_event_ingest(
 p_event_id text,p_name text,p_stage text,p_visitor_id text,p_new_visitor_id text,p_owner_id text,p_ip_hash text,
 p_session_id text DEFAULT NULL,p_page_id text DEFAULT NULL,p_page_visit_id text DEFAULT NULL,p_duration_ms integer DEFAULT NULL
) RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE visitor public.yitan_analytics_visitors; event public.yitan_analytics_events; linked_visitor text; page_event boolean;
BEGIN
 page_event := p_name IN ('page_view','page_heartbeat','page_leave');
 IF p_name NOT IN ('guide_viewed','registration_viewed','registration_submitted','recording_consent','microphone_allowed','microphone_denied','page_view','page_heartbeat','page_leave')
 OR (p_name='guide_viewed' AND p_stage NOT IN ('0','1','2','3','4','5')) OR (p_name<>'guide_viewed' AND p_stage<>'') THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_event';
 END IF;
 IF page_event AND (p_session_id IS NULL OR p_page_visit_id IS NULL OR p_page_id IS NULL OR p_duration_ms IS NULL
 OR p_session_id !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
 OR p_page_visit_id !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
 OR p_page_id NOT IN ('brand','guide_memory','guide_voice','guide_book','guide_family','guide_role','sample_book','login','register','recover','books','profile_create','profile','call','chapter','memoir','family','account','photos','reviews','editions','other')
 OR p_duration_ms<0 OR p_duration_ms>86400000) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_page_event';
 END IF;
 IF p_session_id IS NOT NULL AND p_session_id !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_session';
 END IF;
 IF NOT page_event AND (p_page_id IS NOT NULL OR p_page_visit_id IS NOT NULL OR p_duration_ms IS NOT NULL) THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='unexpected_page_fields';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_event_'||p_ip_hash,0));
 IF p_session_id IS NOT NULL THEN
  PERFORM pg_advisory_xact_lock(hashtextextended('yitan_browser_session_'||p_session_id,0));
  SELECT visitor_id INTO linked_visitor FROM public.yitan_analytics_browser_sessions WHERE id=p_session_id;
 END IF;
 SELECT * INTO visitor FROM public.yitan_analytics_visitors WHERE id=coalesce(linked_visitor,p_visitor_id) FOR UPDATE;
 IF NOT FOUND OR (visitor.owner_id IS NOT NULL AND visitor.owner_id IS DISTINCT FROM p_owner_id) THEN
  INSERT INTO public.yitan_analytics_visitors(id,owner_id) VALUES(p_new_visitor_id,p_owner_id) RETURNING * INTO visitor;
 ELSIF p_owner_id IS NOT NULL AND visitor.owner_id IS NULL THEN
  UPDATE public.yitan_analytics_visitors SET owner_id=p_owner_id WHERE id=visitor.id RETURNING * INTO visitor;
 END IF;
 IF p_session_id IS NOT NULL THEN
  INSERT INTO public.yitan_analytics_browser_sessions(id,visitor_id) VALUES(p_session_id,visitor.id)
  ON CONFLICT(id) DO UPDATE SET visitor_id=excluded.visitor_id;
 END IF;
 SELECT * INTO event FROM public.yitan_analytics_events WHERE id=p_event_id;
 IF FOUND THEN
  IF event.visitor_id<>visitor.id OR event.name<>p_name OR event.stage<>p_stage
  OR event.session_id IS DISTINCT FROM p_session_id OR event.page_id IS DISTINCT FROM p_page_id
  OR event.page_visit_id IS DISTINCT FROM p_page_visit_id OR event.duration_ms IS DISTINCT FROM p_duration_ms THEN
   RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='event_conflict';
  END IF;
  RETURN visitor.id;
 END IF;
 IF (SELECT count(*) FROM public.yitan_analytics_events WHERE visitor_id=visitor.id AND created_at>now()-interval '1 minute')>=60
 OR (SELECT count(*) FROM public.yitan_auth_attempts WHERE subject_hash=p_ip_hash AND created_at>now()-interval '1 minute')>=120 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='event_rate_limited';
 END IF;
 INSERT INTO public.yitan_auth_attempts(id,subject_hash) VALUES(gen_random_uuid()::text,p_ip_hash);
 INSERT INTO public.yitan_analytics_events(id,name,visitor_id,owner_id,source,stage,session_id,page_id,page_visit_id,duration_ms,version)
 VALUES(p_event_id,p_name,visitor.id,p_owner_id,'browser',p_stage,p_session_id,p_page_id,p_page_visit_id,p_duration_ms,CASE WHEN page_event THEN 2 ELSE 1 END);
 RETURN visitor.id;
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_event_ingest(text,text,text,text,text,text,text,text,text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_event_ingest(text,text,text,text,text,text,text,text,text,text,integer) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
