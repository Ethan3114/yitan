BEGIN;

CREATE TABLE IF NOT EXISTS public.yitan_owners (
    id varchar(36) PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_accounts (
    id varchar(36) PRIMARY KEY,
    owner_id varchar(36) NOT NULL UNIQUE REFERENCES public.yitan_owners(id),
    username varchar(64) NOT NULL UNIQUE,
    password_hash varchar(256) NOT NULL,
    role varchar(20) NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'admin')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_sessions (
    id varchar(64) PRIMARY KEY,
    owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_registration_invites (
    id varchar(36) PRIMARY KEY,
    token_hash varchar(64) NOT NULL UNIQUE,
    created_by varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    used_by varchar(36) REFERENCES public.yitan_owners(id),
    revoked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_auth_attempts (
    id varchar(36) PRIMARY KEY,
    subject_hash varchar(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS yitan_auth_attempts_subject_time ON public.yitan_auth_attempts(subject_hash, created_at);
CREATE TABLE IF NOT EXISTS public.yitan_password_recoveries (
    id varchar(36) PRIMARY KEY,
    account_id varchar(36) NOT NULL REFERENCES public.yitan_accounts(id),
    token_hash varchar(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    used boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS public.yitan_analytics_visitors (
    id varchar(64) PRIMARY KEY,
    owner_id varchar(36) REFERENCES public.yitan_owners(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.yitan_analytics_events (
    id varchar(64) PRIMARY KEY,
    name varchar(48) NOT NULL,
    visitor_id varchar(64) REFERENCES public.yitan_analytics_visitors(id),
    owner_id varchar(36) REFERENCES public.yitan_owners(id),
    source varchar(16) NOT NULL CHECK (source IN ('server','browser')),
    stage varchar(32) NOT NULL DEFAULT '',
    error_code varchar(48) NOT NULL DEFAULT '',
    entity_id varchar(64) NOT NULL DEFAULT '',
    duration_seconds integer,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS yitan_events_created ON public.yitan_analytics_events(created_at);
CREATE INDEX IF NOT EXISTS yitan_events_owner_name ON public.yitan_analytics_events(owner_id, name, created_at);
CREATE TABLE IF NOT EXISTS public.yitan_audit_logs (
    id varchar(36) PRIMARY KEY,
    owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
    action varchar(40) NOT NULL,
    result varchar(80) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.yitan_owners ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_registration_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_auth_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_password_recoveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_analytics_visitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_analytics_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.yitan_audit_logs ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.yitan_owners, public.yitan_accounts,
 public.yitan_sessions, public.yitan_registration_invites, public.yitan_auth_attempts,
 public.yitan_password_recoveries, public.yitan_analytics_visitors,
 public.yitan_analytics_events, public.yitan_audit_logs TO service_role;
REVOKE ALL ON public.yitan_owners, public.yitan_accounts,
 public.yitan_sessions, public.yitan_registration_invites, public.yitan_auth_attempts,
 public.yitan_password_recoveries, public.yitan_analytics_visitors,
 public.yitan_analytics_events, public.yitan_audit_logs FROM anon, authenticated;

CREATE OR REPLACE FUNCTION public.yitan_register(
 p_invite_hash text, p_username text, p_password_hash text,
 p_owner_id text, p_account_id text, p_session_id text, p_session_days integer,
 p_visitor_id text DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
DECLARE invitation public.yitan_registration_invites;
BEGIN
 IF p_session_days < 1 OR p_session_days > 30 OR length(p_password_hash) < 50 THEN
   RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid_registration';
 END IF;
 SELECT * INTO invitation FROM public.yitan_registration_invites
 WHERE token_hash = p_invite_hash AND used_at IS NULL AND NOT revoked AND expires_at > now()
 FOR UPDATE;
 IF NOT FOUND THEN
   RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invitation_unavailable';
 END IF;
 INSERT INTO public.yitan_owners(id) VALUES(p_owner_id);
 INSERT INTO public.yitan_accounts(id, owner_id, username, password_hash, role)
 VALUES(p_account_id, p_owner_id, lower(p_username), p_password_hash, 'member');
 UPDATE public.yitan_registration_invites SET used_at=now(), used_by=p_owner_id WHERE id=invitation.id;
 INSERT INTO public.yitan_sessions(id, owner_id, expires_at)
 VALUES(p_session_id, p_owner_id, now() + make_interval(days=>p_session_days));
 UPDATE public.yitan_analytics_visitors SET owner_id=p_owner_id WHERE id=p_visitor_id AND owner_id IS NULL;
 INSERT INTO public.yitan_analytics_events(id,name,owner_id,source)
 VALUES(gen_random_uuid()::text,'account_registered',p_owner_id,'server');
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result)
 VALUES(gen_random_uuid()::text,p_owner_id,'account_registered','success');
 RETURN jsonb_build_object('owner_id',p_owner_id,'account_id',p_account_id,'session_id',p_session_id,'role','member');
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_register(text,text,text,text,text,text,integer,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_register(text,text,text,text,text,text,integer,text) TO service_role;

CREATE OR REPLACE FUNCTION public.yitan_recover_password(p_token_hash text,p_password_hash text)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE recovery public.yitan_password_recoveries; account_owner text;
BEGIN
 SELECT * INTO recovery FROM public.yitan_password_recoveries
 WHERE token_hash=p_token_hash AND NOT used AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT owner_id INTO account_owner FROM public.yitan_accounts WHERE id=recovery.account_id AND enabled FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE public.yitan_accounts SET password_hash=p_password_hash WHERE id=recovery.account_id;
 UPDATE public.yitan_password_recoveries SET used=true WHERE id=recovery.id;
 DELETE FROM public.yitan_sessions WHERE owner_id=account_owner;
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result)
 VALUES(gen_random_uuid()::text,account_owner,'password_recovered','success');
 RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_recover_password(text,text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_recover_password(text,text) TO service_role;

NOTIFY pgrst, 'reload schema';
COMMIT;
