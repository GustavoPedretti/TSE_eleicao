-- Cole no SQL Editor do Supabase (uma vez).
-- Depois rode o app com DATABASE_URL apontando pro Postgres.

create table if not exists snapshots (
  id bigserial primary key,
  eleicao text not null,
  abrangencia text not null default 'br',
  turno text,
  idg text,
  gerado_em_tse text,
  secoes_pct text,
  baixado_em timestamptz not null,
  mudou boolean not null default true,
  resumo_json jsonb not null
);

create index if not exists idx_snapshots_eleicao_abr_id
  on snapshots (eleicao, abrangencia, id desc);

create table if not exists polls (
  id bigserial primary key,
  eleicao text not null,
  abrangencia text not null default 'br',
  ok boolean not null,
  mudou boolean not null default false,
  idg text,
  erro text,
  em timestamptz not null
);

create index if not exists idx_polls_eleicao_em
  on polls (eleicao, em desc);
