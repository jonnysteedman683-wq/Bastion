-- Bastion core tables for the UniSync ledger (Supabase project UNISYNCARC): members and their roles, agents,
-- runs, memory, failures, cost events, GitHub events and the audit log, each behind row-level security.
--
-- Captured from the live migration `bastion_core` (20261105093507) so the repository matches the database.
-- Applied once; NOT safe to re-run (it creates types, tables and policies without `if not exists`).
-- Apply order on a fresh database: bastion_reader.sql, bastion_core.sql, bastion_spend.sql,
-- bastion_tasks_control.sql, bastion_lockdown.sql.
--
-- The last block grants signed-in users every table *and view* in the schema. That is right for the
-- row-level-secured tables above, but it also reached bastion.feed and bastion.roster, which run with their
-- owner's rights over public.unisync_*. bastion_lockdown.sql takes that back; always apply it after this file.

create schema if not exists bastion;
set local search_path = bastion, public, extensions;

create extension if not exists "pgcrypto" with schema extensions;

create type bastion.member_role as enum ('owner', 'admin', 'operator', 'viewer');

create table bastion.members (
  id          uuid primary key references auth.users(id) on delete cascade,
  email       text not null unique,
  role        bastion.member_role not null default 'viewer',
  created_at  timestamptz not null default now()
);

create or replace function bastion.is_role(min_role bastion.member_role) returns boolean
language sql stable security definer set search_path = bastion, public as $$
  select exists (
    select 1 from bastion.members m
    where m.id = auth.uid()
      and array_position(enum_range(null::bastion.member_role), m.role)
          <= array_position(enum_range(null::bastion.member_role), min_role)
  );
$$;

create table bastion.agents (
  id            uuid primary key default gen_random_uuid(),
  slug          text not null unique,
  name          text not null,
  repo          text,
  trust_rung    smallint not null default 0 check (trust_rung between 0 and 4),
  daily_cost_cap_usd numeric(10,4) not null default 1.00,
  status        text not null default 'idle' check (status in ('idle','running','paused','failed')),
  created_at    timestamptz not null default now()
);

create table bastion.agent_runs (
  id          uuid primary key default gen_random_uuid(),
  agent_id    uuid not null references bastion.agents(id) on delete cascade,
  summoned_by uuid references bastion.members(id),
  task        text not null,
  status      text not null default 'queued'
              check (status in ('queued','running','succeeded','failed','cancelled')),
  started_at  timestamptz,
  finished_at timestamptz,
  result      jsonb,
  created_at  timestamptz not null default now()
);
create index on bastion.agent_runs (agent_id, created_at desc);
create index on bastion.agent_runs (summoned_by);

create table bastion.memory_entries (
  id          uuid primary key default gen_random_uuid(),
  agent_id    uuid references bastion.agents(id) on delete cascade,
  run_id      uuid references bastion.agent_runs(id) on delete set null,
  kind        text not null check (kind in ('fact','decision','lesson','preference')),
  content     text not null,
  source      text,
  created_at  timestamptz not null default now()
);
create index on bastion.memory_entries (agent_id, created_at desc);
create index on bastion.memory_entries (run_id);

create table bastion.failures (
  id          uuid primary key default gen_random_uuid(),
  agent_id    uuid references bastion.agents(id) on delete cascade,
  run_id      uuid references bastion.agent_runs(id) on delete set null,
  category    text not null,
  message     text not null,
  context     jsonb,
  resolved    boolean not null default false,
  lesson_id   uuid references bastion.memory_entries(id) on delete set null,
  created_at  timestamptz not null default now()
);
create index on bastion.failures (agent_id, created_at desc);
create index on bastion.failures (run_id);
create index on bastion.failures (lesson_id);

create table bastion.cost_events (
  id          uuid primary key default gen_random_uuid(),
  agent_id    uuid not null references bastion.agents(id) on delete cascade,
  run_id      uuid references bastion.agent_runs(id) on delete set null,
  provider    text not null,
  model       text,
  tokens_in   integer not null default 0,
  tokens_out  integer not null default 0,
  cost_usd    numeric(12,6) not null default 0,
  created_at  timestamptz not null default now()
);
create index on bastion.cost_events (agent_id, created_at desc);
create index on bastion.cost_events (run_id);

create view bastion.agent_cost_today with (security_invoker = true) as
  select a.id as agent_id, a.slug, a.daily_cost_cap_usd,
         coalesce(sum(c.cost_usd), 0) as spent_today_usd,
         coalesce(sum(c.cost_usd), 0) >= a.daily_cost_cap_usd as cap_reached
  from bastion.agents a
  left join bastion.cost_events c
    on c.agent_id = a.id and c.created_at >= date_trunc('day', now())
  group by a.id;

create table bastion.github_events (
  id           uuid primary key default gen_random_uuid(),
  delivery_id  text not null unique,
  repo         text not null,
  event        text not null,
  ref          text,
  sha          text,
  actor        text,
  payload      jsonb not null,
  processed_at timestamptz,
  created_at   timestamptz not null default now()
);
create index on bastion.github_events (repo, created_at desc);

create table bastion.audit_log (
  id          bigint generated always as identity primary key,
  actor_id    uuid references bastion.members(id),
  agent_id    uuid references bastion.agents(id),
  action      text not null,
  detail      jsonb,
  created_at  timestamptz not null default now()
);
create index on bastion.audit_log (actor_id);
create index on bastion.audit_log (agent_id);

alter table bastion.members         enable row level security;
alter table bastion.agents          enable row level security;
alter table bastion.agent_runs      enable row level security;
alter table bastion.memory_entries  enable row level security;
alter table bastion.failures        enable row level security;
alter table bastion.cost_events     enable row level security;
alter table bastion.github_events   enable row level security;
alter table bastion.audit_log       enable row level security;

create policy members_self   on bastion.members for select using (id = (select auth.uid()) or bastion.is_role('admin'));
create policy members_owner  on bastion.members for all    using (bastion.is_role('owner')) with check (bastion.is_role('owner'));

create policy agents_read    on bastion.agents for select using (bastion.is_role('viewer'));
create policy agents_write   on bastion.agents for all    using (bastion.is_role('admin')) with check (bastion.is_role('admin'));

create policy runs_read      on bastion.agent_runs for select using (bastion.is_role('viewer'));
create policy runs_summon    on bastion.agent_runs for insert with check (bastion.is_role('operator'));
create policy runs_update    on bastion.agent_runs for update using (bastion.is_role('operator'));

create policy mem_read       on bastion.memory_entries for select using (bastion.is_role('viewer'));
create policy mem_write      on bastion.memory_entries for insert with check (bastion.is_role('operator'));

create policy fail_read      on bastion.failures for select using (bastion.is_role('viewer'));
create policy cost_read      on bastion.cost_events for select using (bastion.is_role('viewer'));
create policy gh_read        on bastion.github_events for select using (bastion.is_role('viewer'));
create policy audit_read     on bastion.audit_log for select using (bastion.is_role('admin'));

revoke update, delete on bastion.audit_log from public, anon, authenticated;

-- Public, read-only summary of each agent. It runs with its owner's rights so it can count runs and
-- failures; it shows no task text, results or memory.
create view bastion.public_agent_status as
  select a.slug, a.name, a.trust_rung, a.status,
         (select count(*) from bastion.agent_runs r where r.agent_id = a.id and r.status = 'succeeded') as runs_succeeded,
         (select count(*) from bastion.failures f where f.agent_id = a.id) as failures_logged,
         (select max(r.finished_at) from bastion.agent_runs r where r.agent_id = a.id) as last_run_at
  from bastion.agents a;

grant usage on schema bastion to anon, authenticated, service_role;
grant select on bastion.public_agent_status to anon, authenticated;
grant select, insert, update, delete on all tables in schema bastion to authenticated;
grant all on all tables in schema bastion to service_role;
revoke update, delete on bastion.audit_log from authenticated;
