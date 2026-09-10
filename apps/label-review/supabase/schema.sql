-- 라벨 검수 앱 (PER-178) — Supabase 스키마. service role 키로 서버에서만 접근한다 (RLS 는 anon 차단용).
-- 정본은 git 의 eval/gold/v5_concern_golden_labels.jsonl 이고, 이 테이블은 여러 사람이 동시에 쓰는 수집 버퍼다.

create table if not exists labelers (
  labeler_id text primary key,
  name       text not null,
  created_at timestamptz not null default now()
);

-- bundle_id 가 기본키다: 두 사람이 동시에 같은 번들을 잡으면 두 번째 insert 가 실패하고 서버가 다음 번들로 넘어간다.
create table if not exists assignments (
  bundle_id    text primary key,
  labeler_id   text not null references labelers(labeler_id),
  labeler_name text not null,
  status       text not null check (status in ('active', 'done')),
  updated_at   timestamptz not null default now()
);

create table if not exists labels (
  label_id   text primary key,          -- golden_contract 의 labelId (B06-1 …)
  bundle_id  text not null,
  labeler_id text references labelers(labeler_id),
  label      jsonb not null,            -- 계약(validate_label)을 통과한 라벨 그대로
  created_at timestamptz not null default now()
);
create index if not exists labels_bundle on labels(bundle_id);

create table if not exists decisions (
  id         bigint generated always as identity primary key,
  bundle_id  text not null,
  labeler_id text references labelers(labeler_id),
  decision   jsonb not null,            -- {bundleId, labelerId, candidateId, kind, reason, labelId, contractError}
  created_at timestamptz not null default now()
);
create index if not exists decisions_bundle on decisions(bundle_id);

alter table labelers    enable row level security;
alter table assignments enable row level security;
alter table labels      enable row level security;
alter table decisions   enable row level security;
-- 정책을 만들지 않는다 → anon/authenticated 는 접근 불가, service role 만 통과.
