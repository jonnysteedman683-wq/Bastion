-- Lock-down for the views that run with their owner's rights (Supabase project UNISYNCARC). Safe to re-run.
-- Apply last, after every other file in this folder, and again after any migration that grants on
-- "all tables in schema bastion" (in Postgres that phrase includes views).
--
-- Why: bastion_core.sql grants signed-in users every table and view in the schema. The tables are protected by
-- row-level security; these views are not, because a view without security_invoker reads and writes as its
-- owner. Without this file a signed-in user could read the ledger feed and change the roster.
-- Check the result with audit_grants.sql: it must return no rows.

-- The ledger views: only bastion_reader may read them, and nobody writes through them.
revoke all on bastion.feed, bastion.roster from public, anon, authenticated;

-- The public agent summary stays readable, but nobody writes bastion.agents through it.
do $$ begin
  if to_regclass('bastion.public_agent_status') is not null then
    revoke insert, update, delete, truncate, references, trigger
      on bastion.public_agent_status from public, anon, authenticated;
  end if;
end $$;
