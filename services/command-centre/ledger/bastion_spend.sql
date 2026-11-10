-- Spend per agent per UTC day for the command centre (Supabase project UNISYNCARC). Safe to re-run.
-- Applied after bastion_reader.sql. Read-only, in the private `bastion` schema; nothing for anon/authenticated.
--
-- Usage contract (backlog G1): any ledger event may carry a `usage` object in its body, normally on
-- task.result or task.failed:
--   "usage": {"model": "claude-...", "input_tokens": 1234, "output_tokens": 567, "cost_micro_usd": 12300}
-- Every field is optional. Cost is integer micro-dollars because the UniSync gateway rejects non-integer numbers
-- in envelopes (`tools/summon finish --usage` converts an agent's `cost_usd`); a `cost_usd` number written
-- directly to Supabase is still counted. Agents write these bodies, so the view trusts nothing: a field counts only
-- when it is a JSON number, negatives count as 0 and values are clamped, so one bad event can never
-- break the view or inflate a total past the clamp.

create or replace view bastion.spend_daily as
with usage as (
  select
    to_char(coalesce(to_timestamp(e.ts), e.inserted) at time zone 'UTC', 'YYYY-MM-DD') as day,
    e.sender as agent,
    e.body->'usage' as u
  from public.unisync_events e
  where jsonb_typeof(e.body->'usage') = 'object'
)
select
  day,
  agent,
  count(*)::int as runs,
  sum(case when jsonb_typeof(u->'input_tokens') = 'number'
      then least(greatest((u->>'input_tokens')::numeric, 0), 1e9) else 0 end)::bigint as input_tokens,
  sum(case when jsonb_typeof(u->'output_tokens') = 'number'
      then least(greatest((u->>'output_tokens')::numeric, 0), 1e9) else 0 end)::bigint as output_tokens,
  round(sum(least(
      case when jsonb_typeof(u->'cost_usd') = 'number'
        then greatest((u->>'cost_usd')::numeric, 0) else 0 end
    + case when jsonb_typeof(u->'cost_micro_usd') = 'number'
        then greatest((u->>'cost_micro_usd')::numeric, 0) / 1e6 else 0 end,
    1e4)), 6) as cost_usd
from usage
group by day, agent;

revoke all on bastion.spend_daily from public, anon, authenticated;
grant select on bastion.spend_daily to bastion_reader;
