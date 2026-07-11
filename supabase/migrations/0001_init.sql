-- Corner: accounts + subscriptions schema
-- Run this in the Supabase SQL Editor (or via `supabase db push` if you're
-- using the Supabase CLI) once your project exists. Safe to run once; re-running
-- will error on the "already exists" objects rather than silently duplicating.
--
-- Security model in one paragraph: every table below has Row Level Security
-- (RLS) turned on, so Postgres itself enforces "you can only ever see/write
-- your own rows" -- even if a bug in the app forgot to filter by user, the
-- database refuses the request. Values are always sent as parameterized data
-- through Supabase's client library, never concatenated into a SQL string, so
-- there is no SQL-injection path here regardless of what a user types into a
-- text field (that's true for a plain string like `1-2-3` and equally true
-- for something like `1-2-3'; DROP TABLE users;--` -- it's just inert text in
-- a column, never interpreted as SQL). The CHECK constraints below are a
-- second, independent line of defense: sane length/enum limits so malformed
-- or abusive data can't be written even if a client-side bug or a tampered
-- request tries to send it.

-- ── Profile: one row per account, created automatically on signup ──
create table if not exists public.users_profile (
  id          uuid primary key references auth.users(id) on delete cascade,
  name        text default '' check (char_length(name) <= 60),
  skill       text default 'beginner' check (skill in ('beginner','intermediate','advanced')),
  goal        text default 'fitness' check (goal in ('fitness','technique','fight')),
  created_at  timestamptz not null default now()
);

alter table public.users_profile enable row level security;

create policy "select own profile" on public.users_profile
  for select using (auth.uid() = id);
create policy "update own profile" on public.users_profile
  for update using (auth.uid() = id);
-- No insert policy for authenticated users -- the trigger below (running as
-- the table owner) creates the row automatically on signup, so a user never
-- needs to (and shouldn't be able to) create their own profile row directly.

-- Auto-create a blank profile row the moment someone signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.users_profile (id) values (new.id);
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- ── Subscriptions: status is only ever written by the Stripe webhook Edge
-- Function (using the service-role key, which bypasses RLS entirely) -- a
-- logged-in user can READ their own row to check status, but there is
-- deliberately no insert/update/delete policy for the `authenticated` role,
-- so nobody can grant themselves "active" by calling the API directly. ──
create table if not exists public.subscriptions (
  id                      uuid primary key default gen_random_uuid(),
  user_id                 uuid not null references auth.users(id) on delete cascade,
  stripe_customer_id      text,
  stripe_subscription_id  text,
  plan                    text check (plan in ('monthly','yearly')),
  status                  text not null default 'incomplete'
                            check (status in ('incomplete','trialing','active','past_due','canceled')),
  current_period_end      timestamptz,
  updated_at              timestamptz not null default now()
);

alter table public.subscriptions enable row level security;

create policy "select own subscription" on public.subscriptions
  for select using (auth.uid() = user_id);

-- ── Session history: replaces corner_history / the IndexedDB "sessions" store ──
create table if not exists public.session_history (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  date        text not null,
  label       text check (label is null or char_length(label) <= 80),
  rounds      int check (rounds between 0 and 50),
  total_sec   int check (total_sec between 0 and 86400),
  punches     int check (punches between 0 and 20000),
  hardest     text,
  complete    text,
  feel        text,
  created_at  timestamptz not null default now(),
  unique (user_id, date)
);

alter table public.session_history enable row level security;

create policy "manage own history" on public.session_history
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── Favorites: replaces corner_favs ──
create table if not exists public.favorites (
  user_id     uuid not null references auth.users(id) on delete cascade,
  combo_key   text not null check (char_length(combo_key) <= 120),
  created_at  timestamptz not null default now(),
  primary key (user_id, combo_key)
);

alter table public.favorites enable row level security;

create policy "manage own favorites" on public.favorites
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── Saved sessions: replaces corner_saved_sessions ──
create table if not exists public.saved_sessions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text check (name is null or char_length(name) <= 60),
  seq         jsonb not null,
  goals       jsonb default '[]'::jsonb,
  source      text,
  created_at  timestamptz not null default now()
);

alter table public.saved_sessions enable row level security;

create policy "manage own saved sessions" on public.saved_sessions
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── Custom combos: replaces corner_custom_combos ──
create table if not exists public.custom_combos (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  level       text not null check (level in ('beginner','intermediate','advanced')),
  n           text not null check (char_length(trim(n)) > 0 and char_length(n) <= 120),
  name        text check (name is null or char_length(name) <= 60),
  tags        jsonb default '[]'::jsonb,
  created_at  timestamptz not null default now()
);

alter table public.custom_combos enable row level security;

create policy "manage own custom combos" on public.custom_combos
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ── Rate limit counters: written only by Edge Functions via the
-- service-role key (which bypasses RLS), never by end users directly --
-- RLS is enabled with zero policies for authenticated/anon, which denies
-- all access from the browser by default. ──
create table if not exists public.rate_limits (
  key           text primary key,
  window_start  timestamptz not null,
  count         int not null default 0
);

alter table public.rate_limits enable row level security;
