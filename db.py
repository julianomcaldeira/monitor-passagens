import os
from psycopg_pool import ConnectionPool

_pool = None

def get_pool():
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = ConnectionPool(conninfo=dsn, min_size=2, max_size=20, kwargs={"autocommit": True}, open=True)
    return _pool

def conectar():
    return get_pool().connection()

SCHEMA = """
create table if not exists destinos(
    id serial primary key,
    codigo text unique not null,
    nome text not null,
    criado_em timestamptz default now()
);
create table if not exists precos(
    id bigserial primary key,
    ts timestamptz default now(),
    tipo text not null,
    origem text not null,
    destino text not null,
    data_ida date,
    data_volta date,
    preco numeric not null,
    companhia text,
    link text
);
create index if not exists ix_precos on precos(tipo, destino, ts);
create index if not exists ix_precos_origem on precos(tipo, destino, origem, ts);
create table if not exists monitorados(
    id serial primary key,
    criado_em timestamptz default now(),
    origem text not null,
    destino text not null,
    nome_destino text,
    data_ida date not null,
    data_volta date,
    ativo boolean default true,
    unico boolean default false,
    constraint uq_monitorado unique(origem, destino, data_ida, data_volta)
);

-- gestor de viagens
create table if not exists perfil(
    id int primary key default 1,
    nome text,
    aeroporto_padrao text default 'VCP',
    orcamento_medio numeric,
    estilo text,
    companhias text,
    preferencias text,
    atualizado_em timestamptz default now()
);
insert into perfil(id) values(1) on conflict do nothing;
create table if not exists viagens(
    id serial primary key,
    nome text not null,
    destino text,
    codigo_destino text,
    data_inicio date,
    data_fim date,
    orcamento numeric,
    status text default 'planejando',
    notas text,
    criado_em timestamptz default now()
);
create table if not exists roteiro(
    id serial primary key,
    viagem_id int not null references viagens(id) on delete cascade,
    dia date,
    horario text,
    titulo text not null,
    descricao text,
    criado_em timestamptz default now()
);
create table if not exists gastos(
    id serial primary key,
    viagem_id int not null references viagens(id) on delete cascade,
    categoria text not null default 'outros',
    descricao text,
    valor numeric not null,
    data date,
    pago boolean default true,
    criado_em timestamptz default now()
);
create table if not exists chat(
    id bigserial primary key,
    ts timestamptz default now(),
    papel text not null,
    conteudo text not null
);
alter table monitorados add column if not exists viagem_id int references viagens(id) on delete set null;
"""

def init(c=None):
    if c is None:
        with get_pool().connection() as conn:
            conn.execute(SCHEMA)
        return
    c.execute(SCHEMA)
