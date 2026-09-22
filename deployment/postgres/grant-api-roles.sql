GRANT USAGE ON SCHEMA public TO joblab_readonly, joblab_writer;
GRANT SELECT ON public.jobs, public.job_details TO joblab_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.jobs, public.job_details, public.job_chunks TO joblab_writer;

-- The restored cloud schema enables RLS and its original policies target
-- Supabase's anon/service_role roles.  PostgREST authenticates as these
-- local roles instead, so grant privileges *and* define the matching RLS
-- policies. Re-running this file safely refreshes only the local policies.
DROP POLICY IF EXISTS "joblab API read jobs" ON public.jobs;
CREATE POLICY "joblab API read jobs"
    ON public.jobs FOR SELECT TO joblab_readonly USING (true);
DROP POLICY IF EXISTS "joblab API write jobs" ON public.jobs;
CREATE POLICY "joblab API write jobs"
    ON public.jobs FOR ALL TO joblab_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "joblab API read job details" ON public.job_details;
CREATE POLICY "joblab API read job details"
    ON public.job_details FOR SELECT TO joblab_readonly USING (true);
DROP POLICY IF EXISTS "joblab API write job details" ON public.job_details;
CREATE POLICY "joblab API write job details"
    ON public.job_details FOR ALL TO joblab_writer USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "joblab API write job chunks" ON public.job_chunks;
CREATE POLICY "joblab API write job chunks"
    ON public.job_chunks FOR ALL TO joblab_writer USING (true) WITH CHECK (true);

DO $$
DECLARE
    table_name text;
    sequence_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['jobs', 'job_details', 'job_chunks'] LOOP
        sequence_name := pg_get_serial_sequence(format('public.%I', table_name), 'id');
        IF sequence_name IS NOT NULL THEN
            EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO joblab_writer', sequence_name);
        END IF;
    END LOOP;
END
$$;
