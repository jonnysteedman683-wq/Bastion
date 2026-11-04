-- Task status and kill switch for the command centre (Supabase project UNISYNCARC). Safe to re-run.
-- Applied after bastion_reader.sql. Read-only, in the private `bastion` schema; nothing for anon/authenticated.

-- Task status, folded event by event exactly as Lestinxor's Ledger.status() does
-- ('' -> open | awaiting_approval -> approved | rejected -> claimed -> done | failed).
-- SECURITY DEFINER so bastion_reader can see statuses without any access to unisync_events itself.
create or replace function bastion.task_states()
returns table (task_id text, title text, requested_by text, agent text, status text, updated_seq bigint, updated_ts text)
language plpgsql stable security definer set search_path = ''
as $$
#variable_conflict use_column
declare
  r record;
  cur text := null;
  st text; ttl text; req text; ag text; lseq bigint; lts timestamptz;
begin
  for r in
    select e.seq, e.sender, e.kind, e.task_id as tid, e.body, coalesce(to_timestamp(e.ts), e.inserted) as t
    from public.unisync_events e where e.task_id <> '' order by e.task_id, e.seq
  loop
    if cur is distinct from r.tid then
      if cur is not null and ttl is not null then
        task_id := cur; title := ttl; requested_by := req; agent := ag; status := st; updated_seq := lseq;
        updated_ts := to_char(lts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
        return next;
      end if;
      cur := r.tid; st := ''; ttl := null; req := null; ag := null;
    end if;
    if r.kind = 'task.request' then
      st := case when coalesce((r.body->>'requires_approval')::boolean, false) then 'awaiting_approval' else 'open' end;
      ttl := left(coalesce(r.body->>'title', ''), 200); req := r.sender; ag := r.body->>'agent';
    elsif r.kind = 'approval.request' and st in ('open', 'claimed') then st := 'awaiting_approval';
    elsif r.kind = 'approval.decision' then
      st := case when coalesce((r.body->>'approved')::boolean, false) then 'approved' else 'rejected' end;
    elsif r.kind = 'task.claim' then st := 'claimed'; ag := r.sender;
    elsif r.kind = 'task.result' then st := 'done';
    elsif r.kind = 'task.failed' then st := 'failed';
    end if;
    lseq := r.seq; lts := r.t;
  end loop;
  if cur is not null and ttl is not null then
    task_id := cur; title := ttl; requested_by := req; agent := ag; status := st; updated_seq := lseq;
    updated_ts := to_char(lts at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"');
    return next;
  end if;
end $$;

revoke all on function bastion.task_states() from public, anon, authenticated;
grant execute on function bastion.task_states() to bastion_reader;

create or replace view bastion.tasks as select * from bastion.task_states();

-- Kill switch, as LinX's control.halt_state(): only the owner's linx/control/halt memory writes count.
create or replace view bastion.control as
with last as (
  select e.seq, e.body->'value' as v, coalesce(to_timestamp(e.ts), e.inserted) as t
  from public.unisync_events e
  where e.kind = 'memory.write' and e.sender = 'jonny'
    and e.body->>'scope' = 'linx/control' and e.body->>'key' = 'halt'
  order by e.seq desc limit 1
)
select
  coalesce((select jsonb_typeof(v) = 'object' and coalesce((v->>'halted')::boolean, false) from last), false) as halted,
  (select v->>'reason' from last) as reason,
  (select to_char(t at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') from last) as since;

revoke all on bastion.tasks, bastion.control from public, anon, authenticated;
grant select on bastion.tasks, bastion.control to bastion_reader;
