import os, requests
from statistics import median
from datetime import timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, redirect, render_template_string, Response
from db import conectar, init

app = Flask(__name__)
SENHA = os.environ.get("PAINEL_SENHA", "")
BRT = ZoneInfo("America/Sao_Paulo")

AEROPORTOS = {"VCP": "Viracopos", "GRU": "Guarulhos", "CGH": "Congonhas"}
CIAS = {"AD": "Azul", "LA": "LATAM", "JJ": "LATAM", "G3": "Gol", "TP": "TAP", "AA": "American",
        "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca", "AR": "Aerolíneas",
        "IB": "Iberia", "AF": "Air France", "KL": "KLM", "LH": "Lufthansa", "BA": "British",
        "EK": "Emirates", "QR": "Qatar", "TK": "Turkish", "UX": "Air Europa", "H2": "Sky",
        "JA": "JetSMART", "AZ": "ITA", "LX": "Swiss", "ET": "Ethiopian"}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

_cidades = {}

def nome_cidade(cod):
    """Traduz código IATA em nome da cidade (lista grátis do Travelpayouts, carregada uma vez)."""
    global _cidades
    if not _cidades:
        try:
            for x in requests.get("https://api.travelpayouts.com/data/pt/cities.json", timeout=15).json():
                _cidades[x["code"]] = x.get("name") or x.get("name_translations", {}).get("en") or x["code"]
        except Exception:
            _cidades = {"_": "_"}
    return _cidades.get(cod, cod)

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

def brl(v):
    return "R$ " + f"{v:,.0f}".replace(",", ".")

@app.route("/")
def index():
    c = conectar(); init(c)
    dests = c.execute("select id,codigo,nome from destinos order by nome").fetchall()
    cards = [dict(id=i, codigo=cod, nome=nome, a=analisar(c, "destino", cod)) for i, cod, nome in dests]
    cards.sort(key=lambda x: -(x["a"]["pct"] if x["a"] and x["a"]["n"] >= 5 else -999))
    ult = c.execute("select max(ts) from precos").fetchone()[0]
    ult_b = c.execute("select max(date_trunc('hour', ts)) from precos where tipo='barato'").fetchone()[0]
    baratos = []
    if ult_b:
        cods = [r[0] for r in c.execute("""select destino from precos where tipo='barato'
            and date_trunc('hour', ts)=%s group by destino order by min(preco) limit 40""", (ult_b,)).fetchall()]
        for cod in cods:
            a = analisar(c, "barato", cod)
            if a: baratos.append(a)
        baratos.sort(key=lambda x: (-x["pct"], x["atual"]["preco"]))
    candidatos = [x["a"] for x in cards if x["a"] and x["a"]["n"] >= 5] + [b for b in baratos if b["n"] >= 5]
    destaque = max(candidatos, key=lambda a: a["pct"], default=None)
    if destaque and destaque["pct"] < 15: destaque = None
    ult_txt = ult.replace(tzinfo=ult.tzinfo or timezone.utc).astimezone(BRT).strftime("%d/%m às %H:%M") if ult else None
    return render_template_string(HTML, cards=cards, baratos=baratos[:24], ult=ult_txt, destaque=destaque, brl=brl)

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

HTML = """<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Monitor de Passagens</title>
<link rel=preconnect href=https://fonts.googleapis.com>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel=stylesheet>
<style>
:root{--bg:#f3f4f6;--card:#fff;--tx:#111827;--mu:#6b7280;--ln:#e5e7eb;--pri:#2563eb;
--verm:#dc2626;--vermbg:#fef2f2;--verde:#059669;--verdebg:#ecfdf5;--azul:#2563eb;--azulbg:#eff6ff;
--cinza:#4b5563;--cinzabg:#f3f4f6;--laranja:#c2410c;--laranjabg:#fff7ed}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--tx)}
.w{max-width:1120px;margin:auto;padding:0 16px 48px}
header{background:#0f172a;color:#fff;padding:28px 0 72px}
header .w{padding-bottom:0}.top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
h1{margin:0;font-size:24px;font-weight:800}.upd{color:#94a3b8;font-size:13px}
.sub{color:#cbd5e1;margin:6px 0 20px;font-size:15px}
form.add{display:flex;gap:8px;max-width:640px}
form.add input{flex:1;padding:14px 16px;border-radius:12px;border:0;font:inherit;font-size:15px}
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
.verm .pill,.pill.verm{background:var(--vermbg);color:var(--verm)}.pill.verde{background:var(--verdebg);color:var(--verde)}
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
.leg{margin-top:36px;background:var(--card);border-radius:14px;padding:16px 20px;font-size:13px;color:var(--mu);line-height:1.8}
.leg .pill{margin:0 6px 0 0;font-size:12px}
@media(max-width:640px){.hero{grid-template-columns:1fr}.hero .btn{width:100%}form.add{flex-direction:column}.preco{font-size:30px}}
</style></head><body>
<header><div class=w>
<div class=top><h1>✈️ Monitor de Passagens</h1><span class=upd>{% if ult %}Atualizado em {{ult}}{% else %}Aguardando primeira coleta{% endif %}</span></div>
<p class=sub>Ida e volta saindo de Viracopos, Guarulhos ou Congonhas. O painel compara o preço de hoje com o que esse trecho costuma custar.</p>
<form class=add method=post action=/destinos><input name=q placeholder="Adicionar destinos: Lisboa, Orlando, Santiago..." required><button class=btn>Adicionar</button></form>
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

<h3>Barato agora para qualquer lugar</h3>
<p class=hint>Destinos que você não salvou, mas que estão com preço bom saindo de Viracopos ou Guarulhos.</p>
<div class=gb>
{% for b in baratos %}<a class=b href="{{b.atual.link}}" target=_blank>
<div class=t><div class=cid>{{b.nome}}</div><span class="pill {{b.cor}}">{% if b.n >= 5 and b.pct > 0 %}{{b.pct}}% abaixo{% else %}{{b.selo}}{% endif %}</span></div>
<div class=pr>{{brl(b.atual.preco)}}</div>
<div class=m>{{b.atual.ida}}{% if b.atual.volta %} a {{b.atual.volta}}{% endif %} · {{b.atual.aeroporto}}{% if b.atual.cia %} · {{b.atual.cia}}{% endif %}</div>
</a>{% else %}<div class=c><div class=vazio-c>⏳ Aguardando a primeira coleta.</div></div>{% endfor %}
</div>

<div class=leg><b style="color:var(--tx)">Como ler:</b> "Normal" é o preço que esse trecho costuma ter, calculado a partir de todas as coletas.
<br><span class="pill verm">Imperdível</span>40% ou mais abaixo do normal
<span class="pill verde">Ótimo preço</span>25% a 39% abaixo
<span class="pill azul">Bom preço</span>15% a 24% abaixo
<span class="pill laranja">Caro agora</span>acima do normal
<br>Preços vêm de buscas recentes e podem ter mudado. Sempre confirme no site antes de comprar.</div>
</main></body></html>"""
