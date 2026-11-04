-- UniSync ledger: append-only, hash-chained event log. Agents write; dashboards read.
-- Apply once in the Supabase SQL editor (or as a migration) when the project exists.
-- Safe to re-run.

create extension if not exists pgcrypto;

create table if not exists public.ledger_events (
  id         bigint generated always as identity primary key,
  ts         timestamptz not null default now(),
  agent      text not null check (length(agent) between 1 and 64),
  kind       text not null check (kind ~ '^[a-z]+(\.[a-z_]+)+$'),   -- e.g. task.request, deploy.record
  summary    text not null check (length(summary) <= 500),         -- one line, safe to show on a dashboard
  payload    jsonb not null default '{}'::jsonb,
  prev_hash  text not null,
  hash       text not null unique
);

create index if not exists ledger_events_agent_id on public.ledger_events (agent, id desc);

-- Each row's hash covers its content and the previous row's hash, so any edit breaks the chain.
-- The advisory lock serialises writers so the chain never forks.
create or replace function public.ledger_chain() returns trigger
language plpgsql as $$
declare
  last_hash text;
begin
  perform pg_advisory_xact_lock(hashtext('ledger_events'));
  select hash into last_hash from public.ledger_events order by id desc limit 1;
  new.prev_hash := coalesce(last_hash, repeat('0', 64));
  new.hash := encode(digest(
    new.prev_hash || '|' || new.ts::text || '|' || new.agent || '|' || new.kind || '|' || new.summary || '|' || new.payload::text,
    'sha256'), 'hex');
  return new;
end $$;

create or replace function public.ledger_append_only() returns trigger
language plpgsql as $$
begin
  raise exception 'ledger_events is append-only';
end $$;

drop trigger if exists ledger_chain on public.ledger_events;
create trigger ledger_chain before insert on public.ledger_events
  for each row execute function public.ledger_chain();

drop trigger if exists ledger_no_update on public.ledger_events;
create trigger ledger_no_update before update or delete on public.ledger_events
  for each row execute function public.ledger_append_only();

-- Row level security on, with no policy for anon or authenticated: the public API keys see nothing.
alter table public.ledger_events enable row level security;
revoke all on public.ledger_events from anon, authenticated;

-- bastion_reader: what the command centre uses. SELECT only, and only these columns.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'bastion_reader') then
    create role bastion_reader nologin;
  end if;
end $$;
grant usage on schema public to bastion_reader;
grant select (id, ts, agent, kind, summary) on public.ledger_events to bastion_reader;
grant bastion_reader to authenticator;  -- lets Supabase's REST API act as this role for a JWT that names it
drop policy if exists bastion_reader_select on public.ledger_events;
create policy bastion_reader_select on public.ledger_events for select to bastion_reader using (true);

-- Writers (agents, Quartermaster) get their own insert-only role later (backlog Q4, S3).
