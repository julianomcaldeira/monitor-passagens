import os
import psycopg
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

def init(c=None):
    if c is None:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
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
                """)
        return
    c.execute("""
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
    """)
