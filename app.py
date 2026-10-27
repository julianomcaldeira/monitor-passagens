import os, requests
from statistics import median
from flask import Flask, request, redirect, render_template_string, Response
from db import conectar, init

app = Flask(__name__)
SENHA = os.environ.get("PAINEL_SENHA", "")

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
    if n < 5: return ("formando histórico", "cinza")
    if pct >= 40: return ("IMPERDÍVEL", "verm")
    if pct >= 25: return ("ótimo", "verde")
    if pct >= 15: return ("bom", "azul")
    return ("normal", "cinza")

def escolher(por_origem):
    vcp = por_origem.get("VCP")
    melhor = vcp
    for o in ["GRU", "CGH"]:
        x = por_origem.get(o)
        if x and (not melhor or x["preco"] < melhor["preco"] * 0.85):
            melhor = x
    return melhor or (min(por_origem.values(), key=lambda x: x["preco"]) if por_origem else None)

def analisar(c, tipo, destino):
    # mínimo por rodada de coleta (hora)
    hist = c.execute("""select date_trunc('hour', ts) h, min(preco) from precos
        where tipo=%s and destino=%s group by h order by h""", (tipo, destino)).fetchall()
    if not hist: return None
    ultima = hist[-1][0]
    rows = c.execute("""select origem,preco,data_ida,data_volta,companhia,link from precos
        where tipo=%s and destino=%s and date_trunc('hour', ts)=%s""", (tipo, destino, ultima)).fetchall()
    por_origem = {}
    for o, p, di, dv, cia, l in rows:
        if o not in por_origem or p < por_origem[o]["preco"]:
            por_origem[o] = dict(origem=o, preco=float(p), ida=di, volta=dv, cia=cia, link=l)
    atual = escolher(por_origem)
    med = median([float(h[1]) for h in hist])
    pct = round((med - atual["preco"]) / med * 100) if med else 0
    s, cor = selo(pct, len(hist))
    return dict(destino=destino, atual=atual, mediana=round(med), pct=pct, selo=s, cor=cor,
                serie=[float(h[1]) for h in hist][-60:], n=len(hist))

@app.route("/")
def index():
    c = conectar(); init(c)
    dests = c.execute("select id,codigo,nome from destinos order by nome").fetchall()
    cards = []
    for i, cod, nome in dests:
        a = analisar(c, "destino", cod)
        cards.append(dict(id=i, codigo=cod, nome=nome, a=a))
    cards.sort(key=lambda x: -(x["a"]["pct"] if x["a"] else -999))
    ult = c.execute("select max(date_trunc('hour', ts)) from precos where tipo='barato'").fetchone()[0]
    baratos = []
    if ult:
        cods = [r[0] for r in c.execute("""select destino from precos where tipo='barato'
            and date_trunc('hour', ts)=%s group by destino order by min(preco) limit 40""", (ult,)).fetchall()]
        for cod in cods:
            a = analisar(c, "barato", cod)
            if a: baratos.append(a)
        baratos.sort(key=lambda x: (-x["pct"], x["atual"]["preco"]))
    return render_template_string(HTML, cards=cards, baratos=baratos[:30], ult=ult)

@app.post("/destinos")
def add():
    c = conectar(); init(c)
    erros = []
    for t in request.form.get("q", "").split(","):
        if not t.strip(): continue
        r = resolver(t)
        if r:
            c.execute("insert into destinos(codigo,nome) values(%s,%s) on conflict do nothing", r)
        else:
            erros.append(t)
    return redirect("/")

@app.post("/destinos/<int:i>/del")
def rem(i):
    conectar().execute("delete from destinos where id=%s", (i,))
    return redirect("/")

HTML = """<!doctype html><html lang=pt><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Monitor de Passagens</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{font-family:system-ui,sans-serif;background:#f4f5f7;margin:0;color:#1d2433}
.w{max-width:1100px;margin:auto;padding:16px}h1{font-size:22px}h2{font-size:18px;margin-top:32px}
form.add{display:flex;gap:8px}form.add input{flex:1;padding:10px;border:1px solid #ccd;border-radius:8px;font-size:15px}
button{padding:10px 14px;border:0;border-radius:8px;background:#1d4ed8;color:#fff;cursor:pointer}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-top:12px}
.c{background:#fff;border-radius:12px;padding:14px;box-shadow:0 1px 3px #0001}
.p{font-size:26px;font-weight:700}.m{color:#667;font-size:13px}
.s{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px;font-weight:600}
.verm{background:#fee2e2;color:#b91c1c}.verde{background:#dcfce7;color:#15803d}.azul{background:#dbeafe;color:#1d4ed8}.cinza{background:#eee;color:#555}
a.b{display:inline-block;margin-top:8px;color:#1d4ed8;font-weight:600}
.x{float:right;background:none;color:#999;padding:0}
table{width:100%;background:#fff;border-radius:12px;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:14px}
</style></head><body><div class=w>
<h1>✈️ Monitor de Passagens</h1>
<form class=add method=post action=/destinos><input name=q placeholder="Digite destinos (ex.: Lisboa, Orlando, Santiago)"><button>Salvar</button></form>

<h2>Meus destinos</h2><div class=g>
{% for d in cards %}<div class=c>
<form method=post action="/destinos/{{d.id}}/del"><button class=x title=remover>✕</button></form>
<b>{{d.nome}}</b> <span class=m>{{d.codigo}}</span><br>
{% if d.a %}
<span class="s {{d.a.cor}}">{{d.a.selo}}</span>
<div class=p>R$ {{'{:,.0f}'.format(d.a.atual.preco).replace(',','.')}}</div>
<div class=m>saindo de {{d.a.atual.origem}} · {{d.a.atual.ida.strftime('%d/%m/%y')}}{% if d.a.atual.volta %} → {{d.a.atual.volta.strftime('%d/%m/%y')}}{% endif %} · {{d.a.atual.cia or ''}}</div>
<div class=m>mediana R$ {{'{:,.0f}'.format(d.a.mediana).replace(',','.')}} · {{d.a.pct}}% abaixo · {{d.a.n}} coletas</div>
<canvas height=60 data-s="{{d.a.serie}}"></canvas>
<a class=b href="{{d.a.atual.link}}" target=_blank>Confirmar preço e comprar →</a>
{% else %}<div class=m>aguardando a primeira coleta</div>{% endif %}
</div>{% endfor %}</div>

<h2>Barato agora (qualquer destino)</h2>
<table><tr><th>Destino</th><th>Preço</th><th>Origem</th><th>Datas</th><th>vs mediana</th><th></th></tr>
{% for b in baratos %}<tr><td><b>{{b.destino}}</b></td><td>R$ {{'{:,.0f}'.format(b.atual.preco).replace(',','.')}}</td>
<td>{{b.atual.origem}}</td><td>{{b.atual.ida.strftime('%d/%m')}}{% if b.atual.volta %}–{{b.atual.volta.strftime('%d/%m')}}{% endif %}</td>
<td><span class="s {{b.cor}}">{{b.selo}} {{b.pct}}%</span></td><td><a href="{{b.atual.link}}" target=_blank>ver</a></td></tr>
{% else %}<tr><td colspan=6 class=m>aguardando a primeira coleta</td></tr>{% endfor %}</table>
<p class=m>Última coleta: {{ult or '—'}} (UTC). Preços vêm de cache; confirme antes de comprar.</p>
</div>
<script>document.querySelectorAll('canvas[data-s]').forEach(c=>{const s=JSON.parse(c.dataset.s);
new Chart(c,{type:'line',data:{labels:s.map((_,i)=>i),datasets:[{data:s,borderColor:'#1d4ed8',pointRadius:0,tension:.3}]},
options:{plugins:{legend:{display:false}},scales:{x:{display:false},y:{display:false}}}})})</script>
</body></html>"""
