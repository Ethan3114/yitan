BEGIN;
CREATE INDEX IF NOT EXISTS yitan_auth_attempts_time ON public.yitan_auth_attempts(created_at);

CREATE OR REPLACE FUNCTION public.yitan_is_admin(p_owner_id text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.yitan_accounts WHERE owner_id=p_owner_id AND enabled AND role='admin');
$$;

CREATE OR REPLACE FUNCTION public.yitan_auth_admit(p_ip_hash text,p_account_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE subject text;
BEGIN
 IF p_ip_hash !~ '^[a-f0-9]{64}$' OR p_account_hash !~ '^[a-f0-9]{64}$' THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_subject';
 END IF;
 FOR subject IN SELECT value FROM unnest(ARRAY[p_ip_hash,p_account_hash]) AS value ORDER BY value LOOP
  PERFORM pg_advisory_xact_lock(hashtextextended('yitan_auth_'||subject,0));
 END LOOP;
 IF (SELECT count(*) FROM public.yitan_auth_attempts WHERE subject_hash=p_ip_hash AND created_at>now()-interval '15 minutes')>=30
 OR (SELECT count(*) FROM public.yitan_auth_attempts WHERE subject_hash=p_account_hash AND created_at>now()-interval '15 minutes')>=10 THEN
  RETURN false;
 END IF;
 INSERT INTO public.yitan_auth_attempts(id,subject_hash) VALUES(gen_random_uuid()::text,p_ip_hash),(gen_random_uuid()::text,p_account_hash);
 DELETE FROM public.yitan_auth_attempts WHERE created_at<now()-interval '1 day';
 RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_password_login(p_account_id text,p_expected_password_hash text,p_session_id text,p_session_days integer,p_new_password_hash text DEFAULT NULL,p_visitor_id text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE account public.yitan_accounts;
BEGIN
 IF p_session_days<1 OR p_session_days>30 OR length(p_session_id)<32 THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_session';
 END IF;
 SELECT * INTO account FROM public.yitan_accounts WHERE id=p_account_id AND enabled AND password_hash=p_expected_password_hash FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF p_new_password_hash IS NOT NULL THEN
  IF length(p_new_password_hash)<50 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_password_hash'; END IF;
  UPDATE public.yitan_accounts SET password_hash=p_new_password_hash WHERE id=account.id;
 END IF;
 INSERT INTO public.yitan_sessions(id,owner_id,expires_at) VALUES(p_session_id,account.owner_id,now()+make_interval(days=>p_session_days));
 UPDATE public.yitan_analytics_visitors SET owner_id=account.owner_id WHERE id=p_visitor_id AND owner_id IS NULL;
 RETURN jsonb_build_object('owner_id',account.owner_id,'role',account.role,'session_id',p_session_id);
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_invitation_create(p_owner_id text,p_id text,p_token_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE invitation public.yitan_registration_invites;
BEGIN
 IF NOT public.yitan_is_admin(p_owner_id) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='admin_required'; END IF;
 INSERT INTO public.yitan_registration_invites(id,token_hash,created_by,expires_at)
 VALUES(p_id,p_token_hash,p_owner_id,now()+interval '7 days') RETURNING * INTO invitation;
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,p_owner_id,'invitation_created',p_id);
 RETURN jsonb_build_object('id',invitation.id,'expires_at',invitation.expires_at);
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_invitation_revoke(p_owner_id text,p_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.yitan_is_admin(p_owner_id) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='admin_required'; END IF;
 UPDATE public.yitan_registration_invites SET revoked=true WHERE id=p_id;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,p_owner_id,'invitation_revoked',p_id);
 RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_recovery_create(p_owner_id text,p_account_id text,p_id text,p_token_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.yitan_is_admin(p_owner_id) THEN RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='admin_required'; END IF;
 PERFORM id FROM public.yitan_accounts WHERE id=p_account_id AND enabled FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 DELETE FROM public.yitan_password_recoveries WHERE account_id=p_account_id;
 INSERT INTO public.yitan_password_recoveries(id,account_id,token_hash,expires_at) VALUES(p_id,p_account_id,p_token_hash,now()+interval '30 minutes');
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,p_owner_id,'recovery_created',p_account_id);
 RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_recover_password(p_token_hash text,p_password_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE recovery public.yitan_password_recoveries; account_owner text; target_account text;
BEGIN
 IF length(p_password_hash)<50 THEN RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_password_hash'; END IF;
 SELECT account_id INTO target_account FROM public.yitan_password_recoveries WHERE token_hash=p_token_hash AND NOT used AND expires_at>now();
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT owner_id INTO account_owner FROM public.yitan_accounts WHERE id=target_account AND enabled FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO recovery FROM public.yitan_password_recoveries WHERE token_hash=p_token_hash AND NOT used AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE public.yitan_accounts SET password_hash=p_password_hash WHERE id=target_account;
 UPDATE public.yitan_password_recoveries SET used=true WHERE id=recovery.id;
 DELETE FROM public.yitan_sessions WHERE owner_id=account_owner;
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result) VALUES(gen_random_uuid()::text,account_owner,'password_recovered','success');
 RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_event_ingest(p_event_id text,p_name text,p_stage text,p_visitor_id text,p_new_visitor_id text,p_owner_id text,p_ip_hash text)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE visitor public.yitan_analytics_visitors; event public.yitan_analytics_events;
BEGIN
 IF p_name NOT IN ('guide_viewed','registration_viewed','registration_submitted','recording_consent','microphone_allowed','microphone_denied')
 OR (p_name='guide_viewed' AND p_stage NOT IN ('0','1','2','3','4','5')) OR (p_name<>'guide_viewed' AND p_stage<>'') THEN
  RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='invalid_event';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_event_'||p_ip_hash,0));
 SELECT * INTO visitor FROM public.yitan_analytics_visitors WHERE id=p_visitor_id FOR UPDATE;
 IF NOT FOUND OR (visitor.owner_id IS NOT NULL AND visitor.owner_id IS DISTINCT FROM p_owner_id) THEN
  INSERT INTO public.yitan_analytics_visitors(id,owner_id) VALUES(p_new_visitor_id,p_owner_id) RETURNING * INTO visitor;
 END IF;
 SELECT * INTO event FROM public.yitan_analytics_events WHERE id=p_event_id;
 IF FOUND THEN
  IF event.visitor_id<>visitor.id OR event.name<>p_name OR event.stage<>p_stage THEN
   RAISE EXCEPTION USING ERRCODE='23505',MESSAGE='event_conflict';
  END IF;
  RETURN visitor.id;
 END IF;
 IF (SELECT count(*) FROM public.yitan_analytics_events WHERE visitor_id=visitor.id AND created_at>now()-interval '1 minute')>=60
 OR (SELECT count(*) FROM public.yitan_auth_attempts WHERE subject_hash=p_ip_hash AND created_at>now()-interval '1 minute')>=120 THEN
  RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='event_rate_limited';
 END IF;
 INSERT INTO public.yitan_auth_attempts(id,subject_hash) VALUES(gen_random_uuid()::text,p_ip_hash);
 INSERT INTO public.yitan_analytics_events(id,name,visitor_id,owner_id,source,stage)
 VALUES(p_event_id,p_name,visitor.id,p_owner_id,'browser',p_stage);
 RETURN visitor.id;
END;
$$;

CREATE OR REPLACE FUNCTION public.yitan_chapter_read(p_owner_id text,p_chapter_id text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM id FROM public.yitan_chapters WHERE id=p_chapter_id AND owner_id=p_owner_id;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_read_'||p_owner_id||p_chapter_id,0));
 IF NOT EXISTS(SELECT 1 FROM public.yitan_analytics_events WHERE owner_id=p_owner_id AND entity_id=p_chapter_id AND name='memoir_read' AND created_at>now()-interval '30 minutes') THEN
  INSERT INTO public.yitan_analytics_events(id,name,owner_id,source,entity_id) VALUES(gen_random_uuid()::text,'memoir_read',p_owner_id,'server',p_chapter_id);
 END IF;
 RETURN true;
END;
$$;

REVOKE ALL ON FUNCTION public.yitan_is_admin(text),public.yitan_auth_admit(text,text),public.yitan_password_login(text,text,text,integer,text,text),public.yitan_invitation_create(text,text,text),public.yitan_invitation_revoke(text,text),public.yitan_recovery_create(text,text,text,text),public.yitan_recover_password(text,text),public.yitan_event_ingest(text,text,text,text,text,text,text),public.yitan_chapter_read(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_is_admin(text),public.yitan_auth_admit(text,text),public.yitan_password_login(text,text,text,integer,text,text),public.yitan_invitation_create(text,text,text),public.yitan_invitation_revoke(text,text),public.yitan_recovery_create(text,text,text,text),public.yitan_recover_password(text,text),public.yitan_event_ingest(text,text,text,text,text,text,text),public.yitan_chapter_read(text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
