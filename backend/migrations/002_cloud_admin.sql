BEGIN;
CREATE OR REPLACE FUNCTION public.yitan_bootstrap_admin(p_owner_id text,p_account_id text,p_username text,p_password_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('yitan_bootstrap_admin',0));
 IF EXISTS(SELECT 1 FROM public.yitan_accounts WHERE role='admin') THEN
  RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='administrator_exists';
 END IF;
 IF length(p_password_hash)<50 THEN
  RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='invalid_password_hash';
 END IF;
 INSERT INTO public.yitan_owners(id) VALUES(p_owner_id);
 INSERT INTO public.yitan_accounts(id,owner_id,username,password_hash,role)
 VALUES(p_account_id,p_owner_id,lower(p_username),p_password_hash,'admin');
 INSERT INTO public.yitan_audit_logs(id,owner_id,action,result)
 VALUES(gen_random_uuid()::text,p_owner_id,'admin_bootstrapped','success');
 RETURN jsonb_build_object('owner_id',p_owner_id,'account_id',p_account_id,'username',lower(p_username));
END;
$$;
REVOKE ALL ON FUNCTION public.yitan_bootstrap_admin(text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.yitan_bootstrap_admin(text,text,text,text) TO service_role;
NOTIFY pgrst,'reload schema';
COMMIT;
