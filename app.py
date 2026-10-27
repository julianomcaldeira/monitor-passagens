import os, time, calendar, requests
from statistics import median
from datetime import timezone, date, datetime
from zoneinfo import ZoneInfo
from flask import Flask, request, redirect, render_template_string, Response
from db import conectar, init

app = Flask(__name__)
SENHA = os.environ.get("PAINEL_SENHA", "")
TP_TOKEN = os.environ.get("TP_TOKEN", "")
MARKER = "796238"
URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BRT = ZoneInfo("America/Sao_Paulo")
ORIGENS = ["VCP", "GRU", "CGH"]

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
@app.before_request
def auth():
    a = request.authorization
    if SENHA and not (a and a.password == SENHA):
        return Response("Senha", 401, {"WWW-Authenticate": 'Basic realm="painel"'})

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

def analisar(c, tipo, destino):
    hist = c.execute("""select date_trunc('hour', ts) h, min(preco) from precos
        where tipo=%s and destino=%s group by h order by h""", (tipo, destino)).fetchall()
    if not hist: return None
    ultima = hist[-1][0]
    rows = c.execute("""select origem,preco,data_ida,data_volta,companhia,link from precos
        where tipo=%s and destino=%s and date_trunc('hour', ts)=%s""", (tipo, destino, ultima)).fetchall()
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

# ---------- painel ----------
@app.route("/")
def index():
    c = conectar(); init(c)
    dests = c.execute("select id,codigo,nome from destinos order by nome").fetchall()
    cards = [dict(id=i, codigo=cod, nome=nome, a=analisar(c, "destino", cod)) for i, cod, nome in dests]
    cards.sort(key=lambda x: -(x["a"]["pct"] if x["a"] and x["a"]["n"] >= 5 else -999))
    ult = c.execute("select max(ts) from precos").fetchone()[0]
    ult_b = c.execute("select max(date_trunc('hour', ts)) from precos where tipo='barato'").fetchone()[0]
    nac, inter = [], []
    if ult_b:
        cods = [r[0] for r in c.execute("""select destino from precos where tipo='barato'
            and date_trunc('hour', ts)=%s group by destino order by min(preco) limit 300""", (ult_b,)).fetchall()]
        for cod in cods:
            lista = nac if nacional(cod) else inter
            if len(lista) >= 30: continue
            a = analisar(c, "barato", cod)
            if a: lista.append(a)
            if len(nac) >= 30 and len(inter) >= 30: break
        for l in (nac, inter):
            l.sort(key=lambda x: (-x["pct"], x["atual"]["preco"]))
    candidatos = [x["a"] for x in cards if x["a"] and x["a"]["n"] >= 5] + [b for b in nac + inter if b["n"] >= 5]
    destaque = max(candidatos, key=lambda a: a["pct"], default=None)
    if destaque and destaque["pct"] < 15: destaque = None
    ult_txt = ult.replace(tzinfo=ult.tzinfo or timezone.utc).astimezone(BRT).strftime("%d/%m às %H:%M") if ult else None
    return render_template_string(BASE + PAINEL, pagina="painel", cards=cards, nac=nac[:24], inter=inter[:24],
                                  ult=ult_txt, destaque=destaque, brl=brl)

# ---------- busca por data ----------
@app.route("/data")
def por_data():
    hoje = date.today()
    ida = request.args.get("ida", "")
    volta = request.args.get("volta", "")
    dest_txt = request.args.get("destino", "").strip()
    res = None
    if ida:
        try:
            ida_d = date.fromisoformat(ida)
            volta_d = date.fromisoformat(volta) if volta else None
        except ValueError:
            ida_d = None
        if ida_d:
            res = buscar_data(ida_d, volta_d, dest_txt)
    return render_template_string(BASE + BUSCA, pagina="data", ida=ida, volta=volta, destino=dest_txt,
                                  hoje=hoje.isoformat(), res=res, brl=brl, sem_token=not TP_TOKEN)

def buscar_data(ida_d, volta_d, dest_txt):
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
    for o in ORIGENS:
        for x in api(dict(base, origin=o)):
            v = voo(x, o)
            d = v["destino"]
            if d not in melhores or v["preco"] < melhores[d]["preco"]:
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
        out["calendario"] = calendario(ida_d, volta_d, destino[0])
    return out

def calendario(ida_d, volta_d, destino):
    """Menor preço de cada dia de ida no mês escolhido, mantendo a mesma duração da viagem."""
    mes = ida_d.strftime("%Y-%m")
    dias_viagem = (volta_d - ida_d).days if volta_d else None
    por_dia = {}
    for o in ORIGENS:
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

# ---------- destinos ----------
@app.post("/destinos")
def add():
    c = conectar(); init(c)
    for t in request.form.get("q", "").split(","):
        if t.strip():
            r = resolver(t)
            if r:
                c.execute("insert into destinos(codigo,nome) values(%s,%s) on conflict do nothing", r)
    return redirect("/")

@app.post("/destinos/<int:i>/del")
def rem(i):
    conectar().execute("delete from destinos where id=%s", (i,))
    return redirect("/")

# ---------- templates ----------
BASE = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Monitor de Passagens</title>
<link rel=preconnect href=https://fonts.googleapis.com>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
:root{--bg:#f3f4f6;--card:#fff;--tx:#111827;--mu:#6b7280;--ln:#e5e7eb;--pri:#2563eb;
--verm:#dc2626;--vermbg:#fef2f2;--verde:#059669;--verdebg:#ecfdf5;--azul:#2563eb;--azulbg:#eff6ff;
--cinza:#4b5563;--cinzabg:#f3f4f6;--laranja:#c2410c;--laranjabg:#fff7ed}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--tx)}
.w{max-width:1120px;margin:auto;padding:0 16px 48px}
header{background:#0f172a;color:#fff;padding:24px 0 72px}header .w{padding-bottom:0}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
h1{margin:0;font-size:24px;font-weight:800}.upd{color:#94a3b8;font-size:13px}
nav.menu{display:flex;gap:6px;background:#1e293b;padding:4px;border-radius:12px}
nav.menu a{color:#cbd5e1;text-decoration:none;padding:8px 14px;border-radius:9px;font-size:14px;font-weight:600}
nav.menu a.on{background:#fff;color:#0f172a}
.sub{color:#cbd5e1;margin:14px 0 20px;font-size:15px}
form.add{display:flex;gap:8px;max-width:640px}
input[type=text],input[type=date]{padding:14px 16px;border-radius:12px;border:1px solid var(--ln);font:inherit;font-size:15px;background:#fff;color:var(--tx)}
form.add input{flex:1;border:0}
.btn{padding:14px 20px;border:0;border-radius:12px;background:var(--pri);color:#fff;font:inherit;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block;text-align:center}
.btn:hover{filter:brightness(1.1)}
main{margin-top:-48px}
.hero{background:var(--card);border-radius:20px;padding:24px;box-shadow:0 10px 30px #0f172a1a;display:grid;grid-template-columns:1fr auto;gap:16px;align-items:center;border-left:6px solid var(--verde)}
.hero.verm{border-left-color:var(--verm)}.hero .lab{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--verde)}
.hero.verm .lab{color:var(--verm)}
.hero h2{margin:6px 0 4px;font-size:28px}.hero .pr{font-size:40px;font-weight:800;line-height:1}
.hero .det{color:var(--mu);margin-top:8px}
.hero.vazio{border-left-color:var(--ln)}.hero.vazio .lab{color:var(--mu)}
h3{font-size:19px;margin:36px 0 4px}.hint{color:var(--mu);font-size:14px;margin:0 0 14px}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.c{background:var(--card);border-radius:16px;padding:20px;box-shadow:0 1px 3px #0000000f;display:flex;flex-direction:column;position:relative}
.c .cid{font-size:20px;font-weight:700;padding-right:28px}.c .cod{color:var(--mu);font-size:13px;font-weight:500;margin-left:4px}
.del{position:absolute;top:14px;right:14px;background:none;border:0;color:#9ca3af;font-size:18px;cursor:pointer;padding:4px}
.del:hover{color:var(--verm)}
.pill{display:inline-block;padding:4px 10px;border-radius:99px;font-size:13px;font-weight:700;margin-top:10px}
.pill.verm{background:var(--vermbg);color:var(--verm)}.pill.verde{background:var(--verdebg);color:var(--verde)}
.pill.azul{background:var(--azulbg);color:var(--azul)}.pill.cinza{background:var(--cinzabg);color:var(--cinza)}.pill.laranja{background:var(--laranjabg);color:var(--laranja)}
.frase{font-size:14px;color:var(--mu);margin-top:6px}
.preco{font-size:34px;font-weight:800;margin-top:14px;line-height:1}.preco small{font-size:14px;font-weight:500;color:var(--mu)}
.voo{margin-top:10px;font-size:14px;line-height:1.6}.voo b{font-weight:600}
.cmp{margin-top:14px;background:var(--bg);border-radius:10px;padding:10px 12px;font-size:13px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px;text-align:center}
.cmp div span{display:block;color:var(--mu);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.cmp div b{font-size:15px}
.cmp .eco b{color:var(--verde)}.cmp .eco.neg b{color:var(--laranja)}
svg.sp{width:100%;height:56px;margin-top:12px}
.c .btn{margin-top:14px}.c .grow{flex:1}
.vazio-c{color:var(--mu);font-size:14px;margin-top:12px}
.gb{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.b{background:var(--card);border-radius:14px;padding:16px;box-shadow:0 1px 3px #0000000f;text-decoration:none;color:inherit;display:block;border:1px solid transparent}
.b:hover{border-color:var(--pri)}.b .t{display:flex;justify-content:space-between;align-items:start;gap:8px}
.b .cid{font-weight:700;font-size:16px}.b .pr{font-size:24px;font-weight:800;margin-top:8px}.b .m{font-size:13px;color:var(--mu);margin-top:4px}
.b .pill{margin-top:0;font-size:12px;white-space:nowrap}
.tabs{display:inline-flex;gap:4px;background:#e5e7eb;padding:4px;border-radius:12px;margin:4px 0 14px}
.tabs button{border:0;background:none;padding:9px 16px;border-radius:9px;font:inherit;font-size:14px;font-weight:600;color:var(--mu);cursor:pointer}
.tabs button.on{background:#fff;color:var(--tx);box-shadow:0 1px 2px #0000001a}
.tabs button span{font-weight:500;color:var(--mu);margin-left:4px}
.painel-tab{display:none}.painel-tab.on{display:grid}
.leg{margin-top:36px;background:var(--card);border-radius:14px;padding:16px 20px;font-size:13px;color:var(--mu);line-height:1.8}
.leg .pill{margin:0 6px 0 0;font-size:12px}
.busca{background:var(--card);border-radius:20px;padding:22px;box-shadow:0 10px 30px #0f172a1a;display:grid;grid-template-columns:1fr 1fr 1.4fr auto;gap:12px;align-items:end}
.busca label{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--mu);display:block;margin-bottom:6px}
.busca input{width:100%}
.aviso{background:var(--laranjabg);color:var(--laranja);border-radius:12px;padding:14px 16px;margin-top:16px;font-size:14px}
.cal{background:var(--card);border-radius:16px;padding:20px;box-shadow:0 1px 3px #0000000f}
.cal .grid{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-top:12px}
.cal .wd{font-size:11px;font-weight:700;color:var(--mu);text-align:center;text-transform:uppercase}
.cal .d{border-radius:10px;padding:8px 4px;text-align:center;min-height:58px;background:var(--bg);text-decoration:none;color:var(--tx);border:2px solid transparent;display:block}
.cal .d .n{font-size:12px;color:var(--mu)}.cal .d .v{font-size:13px;font-weight:700;margin-top:4px}
.cal .d.fora{background:none}.cal .d.fora .n{color:#d1d5db}
.cal .d.n1{background:#d1fae5}.cal .d.n2{background:#ecfdf5}.cal .d.n3{background:#fff7ed}.cal .d.n4{background:#fee2e2}
.cal .d.esc{border-color:var(--pri)}.cal .d.menor .v{color:var(--verde)}
.cal .d:hover{filter:brightness(.96)}
.cal .leg2{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--mu);margin-top:12px}
.cal .leg2 i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:4px}
.duas{display:grid;grid-template-columns:1.3fr 1fr;gap:16px;align-items:start}
.lista .it{display:flex;justify-content:space-between;align-items:center;background:var(--card);border-radius:12px;padding:14px 16px;margin-bottom:8px;text-decoration:none;color:inherit;box-shadow:0 1px 3px #0000000f}
.lista .it:hover{outline:1px solid var(--pri)}.lista .it b{font-size:18px}.lista .it .m{font-size:13px;color:var(--mu)}
@media(max-width:800px){.busca{grid-template-columns:1fr 1fr}.busca .full{grid-column:1/-1}.duas{grid-template-columns:1fr}}
@media(max-width:640px){.hero{grid-template-columns:1fr}.hero .btn{width:100%}form.add{flex-direction:column}.preco{font-size:30px}.cal .d .v{font-size:11px}}
</style></head><body>
<header><div class=w>
<div class=top><h1>✈️ Monitor de Passagens</h1>
<nav class=menu><a href="/" class="{{'on' if pagina=='painel' else ''}}">Painel</a><a href="/data" class="{{'on' if pagina=='data' else ''}}">Buscar por data</a></nav></div>
"""

TABS_JS = """<script>
document.querySelectorAll('.tabs').forEach(t=>t.querySelectorAll('button').forEach(b=>b.onclick=()=>{
 t.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));
 document.querySelectorAll('[data-grupo="'+t.dataset.grupo+'"]').forEach(p=>p.classList.toggle('on',p.dataset.tab===b.dataset.tab));
}));</script>"""

PAINEL = """
<p class=sub>Ida e volta saindo de Viracopos, Guarulhos ou Congonhas. O painel compara o preço de hoje com o que esse trecho costuma custar.
<span class=upd>{% if ult %}Atualizado em {{ult}}.{% else %}Aguardando primeira coleta.{% endif %}</span></p>
<form class=add method=post action=/destinos><input type=text name=q placeholder="Adicionar destinos: Lisboa, Orlando, Santiago..." required><button class=btn>Adicionar</button></form>
</div></header>

<main class=w>
{% if destaque %}
<section class="hero {{'verm' if destaque.cor=='verm' else ''}}">
<div><div class=lab>🔥 Melhor oportunidade agora</div>
<h2>{{destaque.nome}}</h2>
<div class=pr>{{brl(destaque.atual.preco)}} <small style="font-size:16px;font-weight:500;color:var(--mu)">ida e volta</small></div>
<div class=det><b>{{destaque.pct}}% abaixo do normal</b> (economia de {{brl(destaque.economia)}}) · {{destaque.atual.ida}}{% if destaque.atual.volta %} a {{destaque.atual.volta}}{% endif %} · saindo de {{destaque.atual.aeroporto}}{% if destaque.atual.cia %} · {{destaque.atual.cia}}{% endif %}</div></div>
<a class=btn href="{{destaque.atual.link}}" target=_blank>Ver e comprar →</a>
</section>
{% else %}
<section class="hero vazio"><div><div class=lab>Nenhuma oportunidade forte agora</div>
<h2 style="font-size:20px">Os preços estão dentro do normal</h2>
<div class=det>Quando algum trecho ficar pelo menos 15% abaixo do que costuma custar, ele aparece aqui em destaque.</div></div></section>
{% endif %}

<h3>Meus destinos</h3>
<p class=hint>Ordenados do melhor momento de compra para o pior.</p>
<div class=g>
{% for d in cards %}{% set a = d.a %}<div class=c>
<form method=post action="/destinos/{{d.id}}/del" onsubmit="return confirm('Remover {{d.nome}}?')"><button class=del title="Remover">✕</button></form>
<div class=cid>{{d.nome}}<span class=cod>{{d.codigo}}</span></div>
{% if a %}
<div><span class="pill {{a.cor}}">{{a.selo}}{% if a.n >= 5 and a.pct > 0 %} · {{a.pct}}% abaixo{% elif a.n >= 5 and a.pct < 0 %} · {{a.pct|abs}}% acima{% endif %}</span></div>
<div class=frase>{{a.frase}}</div>
<div class=preco>{{brl(a.atual.preco)}} <small>ida e volta</small></div>
<div class=voo>📅 <b>{{a.atual.ida}}{% if a.atual.volta %} a {{a.atual.volta}}{% endif %}</b>{% if a.atual.dias %} ({{a.atual.dias}} dias){% endif %}<br>
🛫 Saindo de <b>{{a.atual.aeroporto}}</b>{% if a.atual.cia %} · {{a.atual.cia}}{% endif %}</div>
<div class=cmp>
<div><span>Normal</span><b>{{brl(a.mediana)}}</b></div>
<div><span>Mais barato já visto</span><b>{{brl(a.minimo)}}</b></div>
<div class="eco {{'neg' if a.economia < 0 else ''}}"><span>{{'Você economiza' if a.economia >= 0 else 'Está mais caro'}}</span><b>{{brl(a.economia|abs)}}</b></div>
</div>
{% if a.serie %}<svg class=sp viewBox="0 0 {{a.serie.w}} {{a.serie.h}}" preserveAspectRatio=none>
<polygon points="{{a.serie.area}}" fill="#2563eb14"/><polyline points="{{a.serie.linha}}" fill=none stroke="#2563eb" stroke-width=2 vector-effect=non-scaling-stroke />
<circle cx="{{a.serie.ux}}" cy="{{a.serie.uy}}" r=3.5 fill="#2563eb"/></svg>
<div class=frase style="margin-top:2px;font-size:12px">Variação do menor preço nas últimas {{a.n if a.n < 60 else 60}} coletas</div>{% endif %}
<div class=grow></div>
<a class=btn href="{{a.atual.link}}" target=_blank>Ver e comprar →</a>
{% else %}<div class=vazio-c>⏳ Aguardando a primeira coleta. Os preços aparecem em até 4 horas.</div>{% endif %}
</div>{% else %}<div class=c><div class=vazio-c>Nenhum destino salvo ainda. Digite uma cidade no campo acima.</div></div>{% endfor %}
</div>

<h3>Voos baratos agora</h3>
<p class=hint>Destinos que você não salvou, mas que estão com preço bom saindo de Viracopos ou Guarulhos.</p>
<div class=tabs data-grupo=bar><button class=on data-tab=nac>🇧🇷 Nacionais<span>{{nac|length}}</span></button><button data-tab=int>🌎 Internacionais<span>{{inter|length}}</span></button></div>
{% for chave, lista in [('nac', nac), ('int', inter)] %}
<div class="gb painel-tab {{'on' if chave=='nac' else ''}}" data-grupo=bar data-tab={{chave}}>
{% for b in lista %}<a class=b href="{{b.atual.link}}" target=_blank>
<div class=t><div class=cid>{{b.nome}}</div><span class="pill {{b.cor}}">{% if b.n >= 5 and b.pct > 0 %}{{b.pct}}% abaixo{% else %}{{b.selo}}{% endif %}</span></div>
<div class=pr>{{brl(b.atual.preco)}}</div>
<div class=m>{{b.atual.ida}}{% if b.atual.volta %} a {{b.atual.volta}}{% endif %} · {{b.atual.aeroporto}}{% if b.atual.cia %} · {{b.atual.cia}}{% endif %}</div>
</a>{% else %}<div class=c><div class=vazio-c>⏳ Aguardando a primeira coleta.</div></div>{% endfor %}
</div>{% endfor %}

<div class=leg><b style="color:var(--tx)">Como ler:</b> "Normal" é o preço que esse trecho costuma ter, calculado a partir de todas as coletas.
<br><span class="pill verm">Imperdível</span>40% ou mais abaixo do normal
<span class="pill verde">Ótimo preço</span>25% a 39% abaixo
<span class="pill azul">Bom preço</span>15% a 24% abaixo
<span class="pill laranja">Caro agora</span>acima do normal
<br>Preços vêm de buscas recentes e podem ter mudado. Sempre confirme no site antes de comprar.</div>
</main>""" + TABS_JS + "</body></html>"

BUSCA = """
<p class=sub>Escolha a data da viagem. O painel mostra o menor preço saindo de Viracopos, Guarulhos ou Congonhas. Com um destino, mostra também o calendário do mês para achar o dia mais barato.</p>
</div></header>
<main class=w>
<form class=busca method=get action=/data>
<div><label>Ida</label><input type=date name=ida value="{{ida}}" min="{{hoje}}" required></div>
<div><label>Volta (opcional)</label><input type=date name=volta value="{{volta}}" min="{{hoje}}"></div>
<div class=full><label>Destino (opcional)</label><input type=text name=destino value="{{destino}}" placeholder="Vazio = todos os destinos"></div>
<div class=full><button class=btn style="width:100%">Buscar menor preço</button></div>
</form>
{% if sem_token %}<div class=aviso>⚠️ Falta configurar a chave TP_TOKEN no Render (Environment). Sem ela a busca não traz resultados.</div>{% endif %}

{% if res %}{% if res.erro %}<div class=aviso>{{res.erro}}</div>{% else %}

{% if res.destino %}
<h3>{{res.destino[1]}} · {{res.titulo}}</h3>
<p class=hint>Menor preço por aeroporto e o mapa de preços do mês{% if res.calendario and res.calendario.dias_viagem %} (viagens de cerca de {{res.calendario.dias_viagem}} dias){% endif %}.</p>
<div class=duas>
<div class=cal>{% set cal = res.calendario %}
{% if cal %}
<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px"><b>{{cal.mes}}</b>
<span class=frase style="margin:0">Dia mais barato: <b style="color:var(--verde)">{{cal.melhor.dia}} · {{brl(cal.melhor.preco)}}</b></span></div>
{% if cal.escolhido %}{% set eco = cal.escolhido.preco - cal.melhor.preco %}{% if eco > 0 %}
<div class=aviso style="background:var(--verdebg);color:var(--verde);margin-top:12px">💡 Mudando para {{cal.melhor.dia}} você economiza {{brl(eco)}}.</div>{% endif %}{% endif %}
<div class=grid>{% for w in ['dom','seg','ter','qua','qui','sex','sáb'] %}<div class=wd>{{w}}</div>{% endfor %}
{% for sem in cal.semanas %}{% for d in sem %}
{% if d.info %}<a class="d {{d.nivel}} {{'esc' if d.escolhido else ''}} {{'menor' if d.menor else ''}}" href="{{d.info.link}}" target=_blank title="Saindo de {{d.info.origem}}"><div class=n>{{d.dia}}</div><div class=v>{{(d.info.preco/1000)|round(1) if d.info.preco >= 10000 else d.info.preco|int}}{{'k' if d.info.preco >= 10000 else ''}}</div></a>
{% else %}<div class="d {{'fora' if d.fora else ''}} {{'esc' if d.escolhido else ''}}"><div class=n>{{d.dia}}</div><div class=v style="color:#d1d5db">–</div></div>{% endif %}
{% endfor %}{% endfor %}</div>
<div class=leg2><span><i style="background:#d1fae5"></i>mais barato</span><span><i style="background:#ecfdf5"></i>barato</span><span><i style="background:#fff7ed"></i>médio</span><span><i style="background:#fee2e2"></i>caro</span><span><i style="border:2px solid var(--pri)"></i>data escolhida</span><span>– sem preço</span></div>
{% else %}<div class=vazio-c>Sem preços para esse mês. Tente outro mês ou outro destino.</div>{% endif %}
</div>
<div class=lista>
{% for v in res.aeroportos %}<a class=it href="{{v.link}}" target=_blank><div><div class=m>Saindo de</div><b style="font-size:16px">{{v.aeroporto}}</b><div class=m>{{v.ida}}{% if v.volta %} a {{v.volta}}{% endif %}{% if v.cia %} · {{v.cia}}{% endif %}{% if v.escalas == 0 %} · direto{% endif %}</div></div><div style="text-align:right"><b>{{brl(v.preco)}}</b><div class=m>ver →</div></div></a>
{% else %}<div class=c><div class=vazio-c>Sem preço exato para essa data. Use o calendário ao lado para ver dias próximos.</div></div>{% endfor %}
</div></div>

{% else %}
<h3>Menor preço para {{res.titulo}}</h3>
<p class=hint>{{'Ida e volta' if res.ida_volta else 'Só ida'}}. O mais barato entre os três aeroportos para cada destino.</p>
<div class=tabs data-grupo=dt><button class=on data-tab=nac>🇧🇷 Nacionais<span>{{res.nac|length}}</span></button><button data-tab=int>🌎 Internacionais<span>{{res.inter|length}}</span></button></div>
{% for chave, lista in [('nac', res.nac), ('int', res.inter)] %}
<div class="gb painel-tab {{'on' if chave=='nac' else ''}}" data-grupo=dt data-tab={{chave}}>
{% for v in lista %}<a class=b href="{{v.link}}" target=_blank>
<div class=t><div class=cid>{{v.nome}}</div>{% if loop.first %}<span class="pill verde">mais barato</span>{% endif %}</div>
<div class=pr>{{brl(v.preco)}}</div>
<div class=m>{{v.ida}}{% if v.volta %} a {{v.volta}}{% endif %} · {{v.aeroporto}}{% if v.cia %} · {{v.cia}}{% endif %}{% if v.escalas == 0 %} · direto{% endif %}</div>
</a>{% else %}<div class=c><div class=vazio-c>Nenhum preço encontrado para essa data.</div></div>{% endfor %}
</div>{% endfor %}
{% endif %}
<p class=hint style="margin-top:20px">Preços de buscas recentes de outros viajantes. Podem ter mudado: confirme no site antes de comprar.</p>
{% endif %}{% endif %}
</main>""" + TABS_JS + "</body></html>"
