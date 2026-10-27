import os
import psycopg

def conectar():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

def init(c):
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
    """)
