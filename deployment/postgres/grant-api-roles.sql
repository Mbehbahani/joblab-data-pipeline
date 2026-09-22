GRANT USAGE ON SCHEMA public TO joblab_readonly, joblab_writer;
GRANT SELECT ON public.jobs, public.job_details TO joblab_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.jobs, public.job_details, public.job_chunks TO joblab_writer;

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