import os, time, calendar, requests
from statistics import median
from datetime import timezone, date, datetime, timedelta
from zoneinfo import ZoneInfo
TP_TOKEN = os.environ.get("TP_TOKEN", "")
MARKER = "796238"
URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BRT = ZoneInfo("America/Sao_Paulo")
ORIGENS = ["VCP", "GRU", "CGH"]
OPCOES_ORIGEM = [("VCP", "Viracopos"), ("GRU", "Guarulhos"), ("CGH", "Congonhas"), ("todos", "Todos")]

def origens_de(o):
    return ORIGENS if o == "todos" else [o]

AEROPORTOS = {"VCP": "Viracopos", "GRU": "Guarulhos", "CGH": "Congonhas"}
CIAS = {"AD": "Azul", "LA": "LATAM", "JJ": "LATAM", "G3": "Gol", "TP": "TAP", "AA": "American",
        "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca", "AR": "Aerolíneas",
        "IB": "Iberia", "AF": "Air France", "KL": "KLM", "LH": "Lufthansa", "BA": "British",
        "EK": "Emirates", "QR": "Qatar", "TK": "Turkish", "UX": "Air Europa", "H2": "Sky",
        "JA": "JetSMART", "AZ": "ITA", "LX": "Swiss", "ET": "Ethiopian"}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
MESES_LONGO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
               "setembro", "outubro", "novembro", "dezembro"]

# ---------- cidades (nome + país), lista grátis carregada uma vez ----------
_cidades = {}

def _carregar_cidades():
    global _cidades
    if _cidades: return
    try:
        for x in requests.get("https://api.travelpayouts.com/data/pt/cities.json", timeout=20).json():
            nome = x.get("name") or (x.get("name_translations") or {}).get("en") or x["code"]
            _cidades[x["code"]] = (nome, x.get("country_code") or "")
    except Exception:
        _cidades = {"_": ("_", "")}

def nome_cidade(cod):
    _carregar_cidades()
    return _cidades.get(cod, (cod, ""))[0]

def nacional(cod):
    _carregar_cidades()
    return _cidades.get(cod, ("", ""))[1] == "BR"

# ---------- consulta ao vivo (busca por data), com cache de 30 min ----------
_cache = {}

def api(params):
    chave = tuple(sorted(params.items()))
    hit = _cache.get(chave)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    p = dict(params, currency="brl", sorting="price", limit=1000, token=TP_TOKEN)
    try:
        r = requests.get(URL, params=p, timeout=25)
        r.raise_for_status()
        dados = r.json().get("data", []) or []
    except Exception as e:
        print("erro api", params, e)
        dados = []
    _cache[chave] = (time.time(), dados)
    return dados

def link_compra(x):
    l = x.get("link") or ""
    sep = "&" if "?" in l else "?"
    return f"https://www.aviasales.com{l}{sep}marker={MARKER}"

def voo(x, origem):
    di = (x.get("departure_at") or "")[:10]
    dv = (x.get("return_at") or "")[:10]
    di_d = date.fromisoformat(di) if di else None
    dv_d = date.fromisoformat(dv) if dv else None
    return dict(destino=x["destination"], nome=nome_cidade(x["destination"]), origem=origem,
                aeroporto=AEROPORTOS.get(origem, origem), preco=float(x["price"]),
                ida=data_br(di_d), volta=data_br(dv_d), dias=(dv_d - di_d).days if di_d and dv_d else None,
                cia=CIAS.get(x.get("airline"), x.get("airline") or ""), escalas=x.get("transfers"),
                link=link_compra(x))

# ---------- utilidades ----------
def resolver(t):
    t = t.strip()
    try:
        r = requests.get("https://autocomplete.travelpayouts.com/places2",
                         params={"term": t, "locale": "pt", "types[]": "city"}, timeout=10).json()
        if r:
            return r[0]["code"], r[0]["name"]
    except Exception:
        pass
    if len(t) == 3:
        return t.upper(), t.upper()
    return None

def selo(pct, n):
    if n < 5: return ("Ainda aprendendo", "cinza", "Poucas coletas para saber se o preço é bom.")
    if pct >= 40: return ("Imperdível", "verm", "Muito abaixo do normal. Compre logo.")
    if pct >= 25: return ("Ótimo preço", "verde", "Bem abaixo do normal. Vale comprar.")
    if pct >= 15: return ("Bom preço", "azul", "Abaixo do normal. Vale considerar.")
    if pct >= -5: return ("Preço normal", "cinza", "Dentro do que costuma custar. Pode esperar.")
    return ("Caro agora", "laranja", "Acima do normal. Melhor esperar.")

def escolher(por_origem):
    vcp = por_origem.get("VCP")
    melhor = vcp
    for o in ["GRU", "CGH"]:
        x = por_origem.get(o)
        if x and (not melhor or x["preco"] < melhor["preco"] * 0.85):
            melhor = x
    return melhor or (min(por_origem.values(), key=lambda x: x["preco"]) if por_origem else None)

def data_br(d):
    return f"{d.day} {MESES[d.month-1]}" if d else ""

def brl(v):
    return "R$ " + f"{v:,.0f}".replace(",", ".")

def sparkline(serie, w=280, h=56):
    if len(serie) < 2: return ""
    lo, hi = min(serie), max(serie)
    rng = (hi - lo) or 1
    pts = [(i * w / (len(serie) - 1), h - 4 - (v - lo) / rng * (h - 8)) for i, v in enumerate(serie)]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{h} " + linha + f" {w},{h}"
    return dict(linha=linha, area=area, ux=pts[-1][0], uy=pts[-1][1], w=w, h=h)

def analisar(c, tipo, destino, origem="todos"):
    f = "" if origem == "todos" else " and origem=%s"
    extra = () if origem == "todos" else (origem,)
    hist = c.execute("""select date_trunc('hour', ts) h, min(preco) from precos
        where tipo=%s and destino=%s""" + f + """ group by h order by h""", (tipo, destino) + extra).fetchall()
    if not hist: return None
    ultima = hist[-1][0]
    rows = c.execute("""select origem,preco,data_ida,data_volta,companhia,link from precos
        where tipo=%s and destino=%s and date_trunc('hour', ts)=%s""" + f, (tipo, destino, ultima) + extra).fetchall()
    por_origem = {}
    for o, p, di, dv, cia, l in rows:
        if o not in por_origem or p < por_origem[o]["preco"]:
            por_origem[o] = dict(origem=o, aeroporto=AEROPORTOS.get(o, o), preco=float(p),
                                 ida=data_br(di), volta=data_br(dv), dias=(dv - di).days if di and dv else None,
                                 cia=CIAS.get(cia, cia or ""), link=l)
    atual = escolher(por_origem)
    valores = [float(h[1]) for h in hist]
    med = median(valores)
    pct = round((med - atual["preco"]) / med * 100) if med else 0
    s, cor, frase = selo(pct, len(hist))
    return dict(destino=destino, nome=nome_cidade(destino), atual=atual, mediana=round(med),
                minimo=round(min(valores)), pct=pct, selo=s, cor=cor, frase=frase,
                economia=round(med - atual["preco"]), serie=sparkline(valores[-60:]), n=len(hist))

def buscar_data(ida_d, volta_d, dest_txt, origem="VCP"):
    destino = None
    if dest_txt:
        r = resolver(dest_txt)
        if not r:
            return dict(erro=f"Não encontrei a cidade \"{dest_txt}\".")
        destino = r
    base = {"departure_at": ida_d.isoformat(), "one_way": "false" if volta_d else "true"}
    if volta_d: base["return_at"] = volta_d.isoformat()
    if destino: base["destination"] = destino[0]

    melhores = {}   # por destino, o mais barato entre os 3 aeroportos
    por_aero = {}   # quando há destino: o mais barato de cada aeroporto
    for o in origens_de(origem):
        for x in api(dict(base, origin=o)):
            v = voo(x, o)
            d = v["destino"]
            atual = melhores.get(d)
            if not atual or melhor_que(v, atual):
                melhores[d] = v
            if destino and (o not in por_aero or v["preco"] < por_aero[o]["preco"]):
                por_aero[o] = v
    lista = sorted(melhores.values(), key=lambda v: v["preco"])
    titulo = f"{data_br(ida_d)}" + (f" a {data_br(volta_d)}" if volta_d else " (só ida)")
    out = dict(titulo=titulo, ida_volta=bool(volta_d), destino=destino,
               nac=[v for v in lista if nacional(v["destino"])][:30],
               inter=[v for v in lista if not nacional(v["destino"])][:30])
    if destino:
        out["aeroportos"] = sorted(por_aero.values(), key=lambda v: v["preco"])
        out["calendario"] = calendario(ida_d, volta_d, destino[0], origem)
    return out

def melhor_que(novo, atual):
    """Viracopos tem prioridade: outro aeroporto só ganha se for 15% mais barato."""
    if novo["origem"] == "VCP" and atual["origem"] != "VCP":
        return novo["preco"] < atual["preco"] / 0.85
    if atual["origem"] == "VCP" and novo["origem"] != "VCP":
        return novo["preco"] < atual["preco"] * 0.85
    return novo["preco"] < atual["preco"]

def calendario(ida_d, volta_d, destino, origem="VCP"):
    """Menor preço de cada dia de ida no mês escolhido, mantendo a mesma duração da viagem."""
    mes = ida_d.strftime("%Y-%m")
    dias_viagem = (volta_d - ida_d).days if volta_d else None
    por_dia = {}
    for o in origens_de(origem):
        for x in api({"origin": o, "destination": destino, "departure_at": mes,
                      "one_way": "false" if volta_d else "true"}):
            di = (x.get("departure_at") or "")[:10]
            if not di: continue
            if dias_viagem is not None:
                dv = (x.get("return_at") or "")[:10]
                if not dv or abs((date.fromisoformat(dv) - date.fromisoformat(di)).days - dias_viagem) > 2:
                    continue
            d = date.fromisoformat(di)
            if d not in por_dia or x["price"] < por_dia[d]["preco"]:
                por_dia[d] = dict(preco=float(x["price"]), link=link_compra(x), origem=AEROPORTOS.get(o, o))
    if not por_dia:
        return None
    precos = [v["preco"] for v in por_dia.values()]
    lo, hi = min(precos), max(precos)
    semanas = []
    for sem in calendar.Calendar(firstweekday=6).monthdatescalendar(ida_d.year, ida_d.month):
        linha = []
        for d in sem:
            info = por_dia.get(d) if d.month == ida_d.month else None
            nivel = None
            if info:
                f = (info["preco"] - lo) / ((hi - lo) or 1)
                nivel = "n1" if f < 0.2 else "n2" if f < 0.5 else "n3" if f < 0.8 else "n4"
            linha.append(dict(dia=d.day, fora=d.month != ida_d.month, info=info, nivel=nivel,
                              escolhido=d == ida_d, menor=info and info["preco"] == lo))
        semanas.append(linha)
    melhor_dia = min(por_dia.items(), key=lambda kv: kv[1]["preco"])
    return dict(mes=f"{MESES_LONGO[ida_d.month-1].capitalize()} de {ida_d.year}", semanas=semanas, lo=lo, hi=hi,
                melhor=dict(dia=data_br(melhor_dia[0]), **melhor_dia[1]),
                escolhido=por_dia.get(ida_d), dias_viagem=dias_viagem)
