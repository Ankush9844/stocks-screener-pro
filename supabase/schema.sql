-- StockPro tables. Run once in the Supabase dashboard: SQL Editor -> New query -> paste -> Run. Safe to run again.
-- Each user sees only their own watchlist and holdings (row-level security); app_state is server-only.

create table if not exists public.watchlist (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  symbol     text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, symbol)
);

create table if not exists public.holdings (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  symbol     text not null,
  qty        double precision not null check (qty > 0),
  price      double precision not null check (price > 0),
  buy_date   date not null,
  note       text not null default '',
  created_at timestamptz not null default now()
);
create index if not exists holdings_user_idx on public.holdings (user_id, created_at);

-- Shared, not per user: the screener's signal history (which stock flipped HOLD -> BUY and when).
create table if not exists public.app_state (
  key        text primary key,
  data       jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.watchlist enable row level security;
alter table public.holdings  enable row level security;
alter table public.app_state enable row level security;   -- no policies: only the service-role key can touch it

drop policy if exists "own rows" on public.watchlist;
create policy "own rows" on public.watchlist for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "own rows" on public.holdings;
create policy "own rows" on public.holdings for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.watchlist, public.holdings, public.app_state from anon;
revoke all on public.app_state from authenticated;
grant select, insert, update, delete on public.watchlist, public.holdings to authenticated;
grant select, insert, update, delete on public.app_state to service_role;
