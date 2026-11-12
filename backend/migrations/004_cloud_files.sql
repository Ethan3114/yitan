BEGIN;
INSERT INTO storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
VALUES('yitan-private','yitan-private',false,16777216,ARRAY['application/octet-stream'])
ON CONFLICT(id) DO NOTHING;

CREATE POLICY yitan_private_objects_restriction ON storage.objects AS RESTRICTIVE
FOR ALL TO anon,authenticated
USING(bucket_id <> 'yitan-private') WITH CHECK(bucket_id <> 'yitan-private');
CREATE POLICY yitan_private_bucket_restriction ON storage.buckets AS RESTRICTIVE
FOR ALL TO anon,authenticated
USING(id <> 'yitan-private') WITH CHECK(id <> 'yitan-private');

CREATE TABLE public.yitan_private_files (
 id varchar(64) PRIMARY KEY,owner_id varchar(36) NOT NULL REFERENCES public.yitan_owners(id),
 profile_id varchar(36) NOT NULL REFERENCES public.yitan_profiles(id),
 call_id varchar(36) NOT NULL REFERENCES public.yitan_calls(id),
 role varchar(32) NOT NULL,source_sha256 varchar(64) NOT NULL,size_bytes bigint NOT NULL,
 parts jsonb NOT NULL,encryption varchar(32) NOT NULL DEFAULT 'fernet-chunks-v1',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(call_id,role)
);
ALTER TABLE public.yitan_private_files ENABLE ROW LEVEL SECURITY;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.yitan_private_files TO service_role;
REVOKE ALL ON public.yitan_private_files FROM anon,authenticated;
NOTIFY pgrst,'reload schema';
COMMIT;
