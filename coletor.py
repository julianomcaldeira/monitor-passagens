import os, datetime as dt, time, requests
from db import conectar, init

TOKEN = os.environ["TP_TOKEN"]
MARKER = "796238"
ORIGENS = ["VCP", "GRU", "CGH"]
URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

def meses(n=10):
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out

def buscar(origem, mes, destino=None):
    p = {"origin": origem, "departure_at": mes, "currency": "brl",
         "sorting": "price", "one_way": "false", "limit": 1000, "token": TOKEN}
    if destino:
        p["destination"] = destino
    try:
        r = requests.get(URL, params=p, timeout=30)
        r.raise_for_status()
        return r.json().get("data", []) or []
    except Exception as e:
        print("erro", origem, destino, mes, e)
        return []

def link(x):
    l = x.get("link") or ""
    sep = "&" if "?" in l else "?"
    return f"https://www.aviasales.com{l}{sep}marker={MARKER}"

def salvar(c, x, origem, tipo):
    volta = (x.get("return_at") or "")[:10] or None
    c.execute("""insert into precos(tipo,origem,destino,data_ida,data_volta,preco,companhia,link)
                 values(%s,%s,%s,%s,%s,%s,%s,%s)""",
              (tipo, origem, x["destination"], x["departure_at"][:10], volta,
               x["price"], x.get("airline"), link(x)))

def main():
    c = conectar()
    init(c)
    destinos = [r[0] for r in c.execute("select codigo from destinos").fetchall()]

    # Destinos salvos: melhor preço por mês, por aeroporto
    for d in destinos:
        for o in ORIGENS:
            for m in meses():
                rows = buscar(o, m, d)
                if rows:
                    salvar(c, rows[0], o, "destino")
                time.sleep(0.3)

    # Parte "barato": qualquer destino, saindo de VCP e GRU
    for o in ["VCP", "GRU"]:
        melhores = {}
        for m in meses():
            for x in buscar(o, m):
                k = x["destination"]
                if k not in melhores or x["price"] < melhores[k]["price"]:
                    melhores[k] = x
            time.sleep(0.3)
        for x in melhores.values():
            salvar(c, x, o, "barato")

    # Monitorados por data específica
    try:
        mon = c.execute("""select origem, destino, data_ida, data_volta from monitorados where ativo=true""").fetchall()
        for o, d, di, dv in mon:
            p = {"origin": o, "destination": d, "departure_at": di.isoformat(), "currency": "brl",
                 "sorting": "price", "one_way": "false" if dv else "true", "limit": 1000, "token": TOKEN}
            if dv:
                p["return_at"] = dv.isoformat()
            try:
                r = requests.get(URL, params=p, timeout=30)
                r.raise_for_status()
                rows = r.json().get("data", []) or []
                if rows:
                    x = rows[0]
                    volta = (x.get("return_at") or "")[:10] or None
                    c.execute("""insert into precos(tipo,origem,destino,data_ida,data_volta,preco,companhia,link)
                                 values(%s,%s,%s,%s,%s,%s,%s,%s)""",
                              ("monitorado", o, x["destination"], x["departure_at"][:10], volta,
                               x["price"], x.get("airline"), link(x)))
                time.sleep(0.3)
            except Exception as e:
                print("erro monitorado", o, d, di, dv, e)
    except Exception as e:
        print("erro mon", e)

    print("ok", len(destinos), "destinos")

if __name__ == "__main__":
    main()
