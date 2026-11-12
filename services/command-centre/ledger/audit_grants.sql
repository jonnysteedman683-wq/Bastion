-- Audit: which views in `bastion` that run with their owner's rights can an API role reach? Read-only.
-- Run after any migration. It must return NO rows. A row is a view that bypasses row-level security
-- for `anon`, `authenticated` or PUBLIC; fix it with bastion_lockdown.sql or a targeted revoke.
--
-- Allowed: SELECT on bastion.public_agent_status (a deliberate public summary). Views created
-- `with (security_invoker = true)` are skipped: they read as the caller, so row-level security applies.

select c.relname as view_name,
  case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as role,
  a.privilege_type as privilege
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
cross join lateral aclexplode(c.relacl) a
where n.nspname = 'bastion'
  and c.relkind in ('v', 'm')
  and not coalesce(c.reloptions @> array['security_invoker=true'], false)
  and (a.grantee = 0 or a.grantee::regrole::text in ('anon', 'authenticated'))
  and not (c.relname = 'public_agent_status' and a.privilege_type = 'SELECT')
order by 1, 2, 3;
