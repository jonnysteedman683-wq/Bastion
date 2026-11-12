-- Read-only access for the command centre to the UniSync ledger (Supabase project UNISYNCARC).
-- Applied as a migration. Safe to re-run.
--
-- The dashboard never touches public.unisync_* directly. It reads two views in a private schema
-- (`bastion`, not exposed by Supabase's REST API) that show only what a dashboard needs:
-- no event bodies beyond a one-line summary, and nothing from unisync_tokens.

create schema if not exists bastion;
revoke all on schema bastion from public, anon, authenticated;

create or replace view bastion.feed as
select
  e.seq as id,
  to_char(coalesce(to_timestamp(e.ts), e.inserted) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as ts,
  e.sender as agent,
  e.kind,
  left(coalesce(
    nullif(e.body->>'summary', ''), nullif(e.body->>'title', ''), nullif(e.body->>'text', ''),
    nullif(e.body->>'message', ''), nullif(e.task_id, ''), e.kind
  ), 200) as summary
from public.unisync_events e;

create or replace view bastion.roster as
select a.name, a.role, a.repos from public.unisync_agents a;

-- Only these two views: re-running this file must not strip grants on tables other files create.
revoke all on bastion.feed, bastion.roster from public, anon, authenticated;

-- bastion_reader: can log in (once the owner sets a password), can only SELECT the two views,
-- every transaction is read-only, and no query runs longer than 5 seconds.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'bastion_reader') then
    create role bastion_reader login;
  end if;
end $$;
alter role bastion_reader set default_transaction_read_only = on;
alter role bastion_reader set statement_timeout = '5s';
grant usage on schema bastion to bastion_reader;
grant select on bastion.feed, bastion.roster to bastion_reader;
