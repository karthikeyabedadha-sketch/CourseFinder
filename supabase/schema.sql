-- Run this in the Supabase SQL editor before switching DATA_BACKEND=supabase.
-- Safe to run on an existing project (uses IF NOT EXISTS and ADD COLUMN IF NOT EXISTS).
-- Do not paste service-role keys into this file.

create table if not exists public.owner_sessions (
  token_hash text primary key,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  email text unique not null,
  display_name text,
  role text not null default 'user' check (role in ('user', 'admin')),
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

alter table public.profiles add column if not exists is_active boolean not null default true;

create table if not exists public.categories (
  id uuid primary key,
  slug text unique not null,
  name text not null,
  description text,
  sort_order int not null default 0,
  is_active boolean not null default true
);

create table if not exists public.providers (
  id uuid primary key,
  slug text unique not null,
  name text not null,
  website_url text
);

create table if not exists public.courses (
  id uuid primary key,
  slug text unique not null,
  title text not null,
  provider_id uuid references public.providers (id),
  category_id uuid references public.categories (id),
  subcategory text,
  official_url text not null,
  url_key text unique,
  short_description text,
  description text,
  language text,
  is_free boolean,
  price numeric check (price is null or price >= 0),
  currency text,
  pricing_notes text,
  difficulty text,
  prerequisites text,
  duration text,
  duration_verified boolean not null default false,
  certificate_available boolean,
  certificate_verified boolean not null default false,
  teaching_format text,
  skills_taught text,
  learning_outcomes text not null default '',
  intended_audience text,
  strengths text,
  limitations text,
  editorial_assessment text,
  editorial_score numeric check (editorial_score is null or (editorial_score >= 1 and editorial_score <= 10)),
  editorial_score_public boolean not null default false,
  editorial_why_recommend text,
  editorial_who_should_take text,
  editorial_who_should_avoid text,
  editorial_value_for_money text,
  editorial_approved boolean not null default false,
  recommendation_status text default 'none',
  last_verified_at date,
  thumbnail_path text,
  status text not null default 'draft' check (status in ('draft', 'pending_review', 'approved', 'published', 'archived')),
  is_featured boolean not null default false,
  is_underrated boolean not null default false,
  needs_verification boolean not null default false,
  keywords text,
  origin text not null default 'admin',
  source_url text,
  source_checked_at date,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Non-destructive column migrations for existing v1 databases:
alter table public.courses add column if not exists url_key text;
alter table public.courses add column if not exists language text;
alter table public.courses add column if not exists is_underrated boolean not null default false;
alter table public.courses add column if not exists origin text not null default 'admin';
alter table public.courses add column if not exists source_url text;
alter table public.courses add column if not exists source_checked_at date;
alter table public.courses add column if not exists published_at timestamptz;
alter table public.courses add column if not exists learning_outcomes text not null default '';

create table if not exists public.course_admin_notes (
  course_id uuid primary key references public.courses (id) on delete cascade,
  notes text not null default '',
  updated_at timestamptz not null default now()
);

create table if not exists public.reviews (
  id uuid primary key,
  course_id uuid not null references public.courses (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  rating int not null check (rating between 1 and 5),
  body text,
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  created_at timestamptz not null default now(),
  updated_at timestamptz,
  unique (course_id, user_id)
);

alter table public.reviews add column if not exists updated_at timestamptz;

create table if not exists public.saved_courses (
  user_id uuid not null references public.profiles (id) on delete cascade,
  course_id uuid not null references public.courses (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, course_id)
);

create table if not exists public.contact_messages (
  id uuid primary key,
  name text not null,
  email text not null,
  message text not null,
  is_handled boolean not null default false,
  handled_at timestamptz,
  created_at timestamptz not null default now()
);

alter table public.contact_messages add column if not exists is_handled boolean not null default false;
alter table public.contact_messages add column if not exists handled_at timestamptz;

create table if not exists public.reports (
  id uuid primary key,
  target_type text not null check (target_type in ('course', 'review')),
  target_id uuid not null,
  reporter_id uuid references public.profiles (id) on delete set null,
  reason text not null,
  details text,
  status text not null default 'pending' check (status in ('pending', 'resolved', 'dismissed')),
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolved_by uuid references public.profiles (id) on delete set null
);

create table if not exists public.course_submissions (
  id uuid primary key,
  submitter_id uuid not null references public.profiles (id) on delete cascade,
  title text not null,
  official_url text not null,
  provider_name text,
  note text,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'rejected')),
  course_id uuid references public.courses (id) on delete set null,
  created_at timestamptz not null default now(),
  reviewed_at timestamptz,
  reviewed_by uuid references public.profiles (id) on delete set null
);

create table if not exists public.moderation_actions (
  id uuid primary key,
  actor_id uuid references public.profiles (id) on delete set null,
  entity_type text not null,
  entity_id text,
  label text,
  action text not null,
  from_status text,
  to_status text,
  note text,
  created_at timestamptz not null default now()
);

create table if not exists public.import_batches (
  id uuid primary key,
  admin_id uuid not null references public.profiles (id) on delete cascade,
  filename text,
  payload text not null,
  created_at timestamptz not null default now()
);
alter table public.import_batches alter column admin_id drop not null;

-- Database triggers enforcing editorial publication rules:
create or replace function public.enforce_course_workflow()
returns trigger language plpgsql as $$
begin
  if (tg_op = 'INSERT') then
    if new.status <> 'draft' then
      raise exception 'a new course must start as a draft';
    end if;
    if new.origin in ('csv_import', 'user_submission', 'ai_suggestion', 'sample')
       and (new.is_featured or new.is_underrated or new.editorial_approved or new.editorial_score_public) then
      raise exception 'untrusted course origins cannot auto-feature or auto-approve';
    end if;
  elsif (tg_op = 'UPDATE') then
    if new.status = 'published' and old.status not in ('approved', 'published') then
      raise exception 'a course must be approved before it can be published';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_enforce_course_workflow on public.courses;
create trigger trg_enforce_course_workflow
before insert or update on public.courses
for each row execute function public.enforce_course_workflow();

-- Enable Row Level Security on all tables:
alter table public.profiles enable row level security;
alter table public.categories enable row level security;
alter table public.providers enable row level security;
alter table public.courses enable row level security;
alter table public.course_admin_notes enable row level security;
alter table public.reviews enable row level security;
alter table public.saved_courses enable row level security;
alter table public.contact_messages enable row level security;
alter table public.reports enable row level security;
alter table public.course_submissions enable row level security;
alter table public.moderation_actions enable row level security;
alter table public.import_batches enable row level security;
alter table public.owner_sessions enable row level security;

-- Public read of published catalogue data. Writes go through the Flask server
-- using the service-role key, which bypasses these policies.
drop policy if exists "public read categories" on public.categories;
create policy "public read categories" on public.categories for select using (is_active = true);

drop policy if exists "public read providers" on public.providers;
create policy "public read providers" on public.providers for select using (true);

drop policy if exists "public read published courses" on public.courses;
create policy "public read published courses" on public.courses for select using (status = 'published');

drop policy if exists "public read approved reviews" on public.reviews;
create policy "public read approved reviews" on public.reviews for select using (status = 'approved');

drop policy if exists "users read own profile" on public.profiles;
create policy "users read own profile" on public.profiles for select using (auth.uid() = id);

drop policy if exists "users read own saved" on public.saved_courses;
create policy "users read own saved" on public.saved_courses for select using (auth.uid() = user_id);

drop policy if exists "users write own saved" on public.saved_courses;
create policy "users write own saved" on public.saved_courses for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "users insert own pending reviews" on public.reviews;
create policy "users insert own pending reviews" on public.reviews for insert with check (auth.uid() = user_id and status = 'pending');

-- Role and admin tables must never be writable by clients.
revoke update on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;
revoke all on public.course_admin_notes, public.moderation_actions, public.import_batches from anon, authenticated;
revoke all on public.owner_sessions from anon, authenticated;
