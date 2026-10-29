import os, time, hmac, hashlib, threading
from datetime import timezone, date, timedelta
from flask import Flask, request, redirect, render_template, jsonify, session, url_for
from db import conectar, init
from servicos import (AEROPORTOS, BRT, OPCOES_ORIGEM, TP_TOKEN, analisar, nacional, resolver,
                      buscar_data, nome_cidade, brl, data_br)
import assistente as ia

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000
SENHA = os.environ.get("PAINEL_SENHA", "")
STATUS = {"planejando": "Planejando", "confirmada": "Confirmada", "em_andamento": "Em andamento", "concluida": "Concluída"}
CAT_NOME = {"passagem": "Passagem", "hospedagem": "Hospedagem", "alimentacao": "Alimentação", "transporte": "Transporte",
            "passeios": "Passeios", "compras": "Compras", "seguro": "Seguro", "outros": "Outros"}

app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(("gv:" + SENHA).encode()).hexdigest()
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
LIVRES = ("login", "static")

@app.before_request
def auth():
    if not SENHA or request.endpoint in LIVRES or session.get("ok"):
        return
    if request.path.startswith(("/assistente/enviar", "/assistente/tarefa")):
        return jsonify(erro="Sessão expirada. Recarregue a página e entre de novo."), 401
    return redirect(url_for("login", prox=request.full_path.rstrip("?")))

@app.after_request
def sem_cache(r):
    # Páginas sempre frescas: evita ver dados antigos após editar ou após deploy.
    if r.mimetype in ("text/html", "application/json"):
        r.headers["Cache-Control"] = "no-store"
    if request.method == "POST":
        _painel_cache["key"] = None  # qualquer alteração atualiza o painel na hora
    return r

@app.route("/login", methods=["GET", "POST"])
def login():
    erro = None
    prox = request.values.get("prox") or "/"
    if not prox.startswith("/") or prox.startswith("//"):
        prox = "/"
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("senha", ""), SENHA):
            session.clear()
            session.permanent = True
            session["ok"] = True
            return redirect(prox)
        erro = "Senha incorreta."
    return render_template("login.html", erro=erro, prox=prox)

@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

_init_ok = False

@app.before_request
def preparar_banco():
    # Cria as tabelas no primeiro request de cada processo (não no import, para não abrir conexão antes do fork).
    global _init_ok
    if not _init_ok:
        try:
            init()
            _init_ok = True
        except Exception as e:
            print("init", e)

def origem_escolhida():
    o = request.args.get("origem", "VCP")
    return o if o in ("VCP", "GRU", "CGH", "todos") else "VCP"

@app.context_processor
def globais():
    return dict(brl=brl, data_br=data_br, origem=origem_escolhida(), opcoes=OPCOES_ORIGEM,
                STATUS=STATUS, CAT_NOME=CAT_NOME, hoje=date.today())

def _f(v):
    v = (v or "").strip().replace(",", ".")
    return float(v) if v else None

def _dt(v):
    try: return date.fromisoformat(v) if v else None
    except ValueError: return None

# ---------- painel ----------
_painel_cache = {"key": None, "html": None, "ts": 0}

def proximas_viagens(c, n=3):
    rows = c.execute("""select v.id,v.nome,v.destino,v.data_inicio,v.data_fim,v.orcamento,v.status,coalesce(sum(g.valor),0)
        from viagens v left join gastos g on g.viagem_id=v.id where v.status<>'concluida'
        group by v.id order by v.data_inicio nulls last limit %s""", (n,)).fetchall()
    return [dict(id=r[0], nome=r[1], destino=r[2], inicio=r[3], fim=r[4], orcamento=float(r[5]) if r[5] else None, status=r[6], gasto=float(r[7]))
            for r in rows]

@app.route("/")
def index():
    origem = origem_escolhida()
    now = time.time()
    if _painel_cache["key"] == origem and _painel_cache["html"] and now - _painel_cache["ts"] < 60:
        return _painel_cache["html"]
    f = "" if origem == "todos" else " and origem=%s"
    extra = () if origem == "todos" else (origem,)
    with conectar() as c:
        dests = c.execute("select id,codigo,nome from destinos order by nome").fetchall()
        cards = [dict(id=i, codigo=cod, nome=nome, a=analisar(c, "destino", cod, origem)) for i, cod, nome in dests]
        cards.sort(key=lambda x: -(x["a"]["pct"] if x["a"] and x["a"]["n"] >= 5 else -999))
        ult = c.execute("select max(ts) from precos").fetchone()[0]
        ult_b = c.execute("select max(date_trunc('hour', ts)) from precos where tipo='barato'").fetchone()[0]
        nac, inter = [], []
        if ult_b:
            cods = [r[0] for r in c.execute("""select distinct destino from precos where tipo='barato'
                and date_trunc('hour', ts)=%s""" + f + """ limit 150""", (ult_b,) + extra).fetchall()]
            for cod in cods:
                lista = nac if nacional(cod) else inter
                if len(lista) >= 24: continue
                a = analisar(c, "barato", cod, origem)
                if a and a.get("n", 0) >= 3:
                    lista.append(a)
                if len(nac) >= 24 and len(inter) >= 24: break
            for l in (nac, inter):
                l.sort(key=lambda x: (-x["pct"], x["atual"]["preco"]))
        candidatos = [x["a"] for x in cards if x["a"] and x["a"]["n"] >= 5] + [b for b in nac + inter if b["n"] >= 5]
        destaque = max(candidatos, key=lambda a: a["pct"], default=None)
        if destaque and destaque["pct"] < 15: destaque = None
        ult_txt = ult.replace(tzinfo=ult.tzinfo or timezone.utc).astimezone(BRT).strftime("%d/%m às %H:%M") if ult else None
        viagens = proximas_viagens(c)
    html = render_template("painel.html", pagina="painel", cards=cards, nac=nac[:24], inter=inter[:24],
                           ult=ult_txt, destaque=destaque, viagens=viagens)
    _painel_cache.update(key=origem, html=html, ts=now)
    return html

# ---------- busca por data ----------
@app.route("/data")
def por_data():
    ida, volta = request.args.get("ida", ""), request.args.get("volta", "")
    dest_txt = request.args.get("destino", "").strip()
    res = None
    ida_d = _dt(ida)
    if ida_d:
        res = buscar_data(ida_d, _dt(volta), dest_txt, origem_escolhida())
    return render_template("busca.html", pagina="data", ida=ida, volta=volta, destino=dest_txt,
                           res=res, sem_token=not TP_TOKEN)

# ---------- destinos ----------
@app.post("/destinos")
def add():
    _painel_cache["key"] = None
    with conectar() as c:
        for t in request.form.get("q", "").split(","):
            if t.strip():
                r = resolver(t)
                if r:
                    c.execute("insert into destinos(codigo,nome) values(%s,%s) on conflict do nothing", r)
    return redirect(request.referrer or "/")

@app.post("/destinos/<int:i>/del")
def rem(i):
    _painel_cache["key"] = None
    with conectar() as c:
        c.execute("delete from destinos where id=%s", (i,))
    return redirect(request.referrer or "/")

# ---------- monitorados ----------
@app.post("/monitorar")
def monitorar():
    origem = request.form.get("origem") or origem_escolhida()
    dest_txt = request.form.get("destino", "").strip()
    ida_d, volta_d = _dt(request.form.get("ida")), _dt(request.form.get("volta"))
    viagem_id = request.form.get("viagem_id", type=int)
    r = resolver(dest_txt) if dest_txt and ida_d else None
    if r:
        with conectar() as c:
            c.execute("""insert into monitorados(origem,destino,nome_destino,data_ida,data_volta,viagem_id)
                         values(%s,%s,%s,%s,%s,%s) on conflict do nothing""", (origem, r[0], r[1], ida_d, volta_d, viagem_id))
    return redirect(request.referrer or "/data")

@app.post("/monitorados/<int:i>/del")
def desmonitorar(i):
    with conectar() as c:
        c.execute("delete from monitorados where id=%s", (i,))
    return redirect(request.referrer or "/monitorados")

@app.route("/monitorados")
def listar_monitorados():
    with conectar() as c:
        rows = c.execute("""select m.id,m.origem,m.destino,m.nome_destino,m.data_ida,m.data_volta,v.id,v.nome,
            (select min(preco) from precos p where p.tipo='monitorado' and p.destino=m.destino and p.origem=m.origem
               and p.data_ida=m.data_ida and p.data_volta is not distinct from m.data_volta and p.ts > now()-interval '1 day')
            from monitorados m left join viagens v on v.id=m.viagem_id where m.ativo order by m.data_ida""").fetchall()
    itens = [dict(id=r[0], origem=r[1], destino=r[2], nome=r[3] or nome_cidade(r[2]), data_ida=r[4], data_volta=r[5],
                  viagem_id=r[6], viagem=r[7], preco=float(r[8]) if r[8] else None) for r in rows]
    return render_template("monitorados.html", pagina="monitorados", itens=itens)

# ---------- viagens ----------
@app.route("/viagens")
def viagens():
    with conectar() as c:
        rows = c.execute("""select v.id,v.nome,v.destino,v.data_inicio,v.data_fim,v.orcamento,v.status,
            coalesce(sum(g.valor),0),(select count(*) from roteiro r where r.viagem_id=v.id)
            from viagens v left join gastos g on g.viagem_id=v.id group by v.id
            order by (v.status='concluida'), v.data_inicio nulls last""").fetchall()
    lista = [dict(id=r[0], nome=r[1], destino=r[2], inicio=r[3], fim=r[4], orcamento=float(r[5]) if r[5] else None, status=r[6],
                  gasto=float(r[7]), itens=r[8]) for r in rows]
    return render_template("viagens.html", pagina="viagens", viagens=lista)

@app.post("/viagens")
def criar_viagem():
    fm = request.form
    destino = fm.get("destino", "").strip() or None
    cod = (resolver(destino) or (None,))[0] if destino else None
    with conectar() as c:
        i = c.execute("""insert into viagens(nome,destino,codigo_destino,data_inicio,data_fim,orcamento,notas)
            values(%s,%s,%s,%s,%s,%s,%s) returning id""",
            (fm.get("nome") or destino or "Nova viagem", destino, cod, _dt(fm.get("data_inicio")),
             _dt(fm.get("data_fim")), _f(fm.get("orcamento")), fm.get("notas") or None)).fetchone()[0]
    return redirect(f"/viagens/{i}")

@app.route("/viagens/<int:i>")
def ver_viagem(i):
    v = ia.viagem(i)
    if not v: return redirect("/viagens")
    dias = {}
    for it in v["roteiro"]:
        dias.setdefault(it["dia"], []).append(it)
    gasto = sum(float(g["valor"]) for g in v["gastos"] if g["pago"])
    previsto = sum(float(g["valor"]) for g in v["gastos"] if not g["pago"])
    por_cat = {}
    for g in v["gastos"]:
        por_cat[g["categoria"]] = por_cat.get(g["categoria"], 0) + float(g["valor"])
    return render_template("viagem.html", pagina="viagens", v=v, dias=dias, gasto=gasto, previsto=previsto,
                           por_cat=sorted(por_cat.items(), key=lambda x: -x[1]), cats=ia.CATEGORIAS)

@app.post("/viagens/<int:i>/editar")
def editar_viagem(i):
    fm = request.form
    with conectar() as c:
        c.execute("""update viagens set nome=%s,destino=%s,data_inicio=%s,data_fim=%s,orcamento=%s,status=%s,notas=%s
            where id=%s""", (fm.get("nome"), fm.get("destino") or None, _dt(fm.get("data_inicio")), _dt(fm.get("data_fim")),
                             _f(fm.get("orcamento")), fm.get("status") if fm.get("status") in STATUS else "planejando",
                             fm.get("notas") or None, i))
    return redirect(f"/viagens/{i}")

@app.post("/viagens/<int:i>/del")
def apagar_viagem(i):
    with conectar() as c:
        c.execute("delete from viagens where id=%s", (i,))
    return redirect("/viagens")

@app.post("/viagens/<int:i>/roteiro")
def add_roteiro(i):
    fm = request.form
    if fm.get("titulo", "").strip():
        with conectar() as c:
            c.execute("insert into roteiro(viagem_id,dia,horario,titulo,descricao) values(%s,%s,%s,%s,%s)",
                      (i, _dt(fm.get("dia")), fm.get("horario") or None, fm["titulo"].strip(), fm.get("descricao") or None))
    return redirect(f"/viagens/{i}#roteiro")

@app.post("/roteiro/<int:r>/del")
def del_roteiro(r):
    with conectar() as c:
        c.execute("delete from roteiro where id=%s", (r,))
    return redirect((request.referrer or "/viagens").split("#")[0] + "#roteiro")

@app.post("/viagens/<int:i>/gastos")
def add_gasto(i):
    fm = request.form
    valor = _f(fm.get("valor"))
    if valor:
        cat = fm.get("categoria") if fm.get("categoria") in ia.CATEGORIAS else "outros"
        with conectar() as c:
            c.execute("insert into gastos(viagem_id,categoria,descricao,valor,data,pago) values(%s,%s,%s,%s,%s,%s)",
                      (i, cat, fm.get("descricao") or None, valor, _dt(fm.get("data")), fm.get("pago") == "1"))
    return redirect(f"/viagens/{i}#gastos")

@app.post("/gastos/<int:g>/del")
def del_gasto(g):
    with conectar() as c:
        c.execute("delete from gastos where id=%s", (g,))
    return redirect((request.referrer or "/orcamento").split("#")[0] + "#gastos")

# ---------- orçamento ----------
@app.route("/orcamento")
def orcamento():
    with conectar() as c:
        rows = c.execute("""select v.id,v.nome,v.orcamento,v.status,
            coalesce(sum(g.valor) filter (where g.pago),0), coalesce(sum(g.valor) filter (where not g.pago),0)
            from viagens v left join gastos g on g.viagem_id=v.id group by v.id order by v.data_inicio nulls last""").fetchall()
        cats = c.execute("select categoria,sum(valor) from gastos where pago group by categoria order by 2 desc").fetchall()
    lista = [dict(id=r[0], nome=r[1], orcamento=float(r[2]) if r[2] else None, status=r[3], gasto=float(r[4]),
                  previsto=float(r[5])) for r in rows]
    tot = dict(orcamento=sum(x["orcamento"] or 0 for x in lista), gasto=sum(x["gasto"] for x in lista),
               previsto=sum(x["previsto"] for x in lista))
    cats = [(k, float(v)) for k, v in cats]
    return render_template("orcamento.html", pagina="orcamento", viagens=lista, tot=tot, cats=cats,
                           cat_max=max([v for _, v in cats], default=1))

# ---------- perfil ----------
@app.route("/perfil", methods=["GET", "POST"])
def perfil():
    if request.method == "POST":
        fm = request.form
        with conectar() as c:
            c.execute("""update perfil set nome=%s,aeroporto_padrao=%s,orcamento_medio=%s,estilo=%s,companhias=%s,
                preferencias=%s,atualizado_em=now() where id=1""",
                (fm.get("nome") or None, fm.get("aeroporto_padrao") or "VCP", _f(fm.get("orcamento_medio")),
                 fm.get("estilo") or None, fm.get("companhias") or None, fm.get("preferencias") or None))
        return redirect("/perfil?ok=1")
    return render_template("perfil.html", pagina="perfil", p=ia.perfil(), ok=request.args.get("ok"),
                           aeroportos=AEROPORTOS)

# ---------- assistente ----------
@app.route("/assistente")
def assistente():
    return render_template("assistente.html", pagina="assistente", msgs=ia.historico(100),
                           sem_chave=not ia.API_KEY, q=request.args.get("q", ""))

@app.post("/assistente/enviar")
def assistente_enviar():
    # Responde na hora com o id da tarefa; o processamento (que pode levar minutos) roda em segundo plano.
    texto = (request.get_json(silent=True) or {}).get("texto", "").strip()
    if not texto: return jsonify(erro="mensagem vazia"), 400
    with conectar() as c:
        tid = c.execute("insert into chat_tarefas default values returning id").fetchone()[0]
    threading.Thread(target=_processar, args=(tid, texto), daemon=True).start()
    return jsonify(tarefa=tid)

def _processar(tid, texto):
    try:
        status, resp = "ok", ia.responder(texto)
    except Exception as e:
        print("assistente", e)
        status, resp = "erro", f"Erro ao falar com o assistente: {e}"
    _painel_cache["key"] = None
    with conectar() as c:
        c.execute("update chat_tarefas set status=%s, resposta=%s where id=%s", (status, resp, tid))

@app.route("/assistente/tarefa/<int:tid>")
def assistente_tarefa(tid):
    with conectar() as c:
        r = c.execute("select status,resposta,extract(epoch from now()-criado_em) from chat_tarefas where id=%s", (tid,)).fetchone()
    if not r: return jsonify(status="erro", resposta="Tarefa não encontrada."), 404
    if r[0] == "processando" and r[2] > 600:  # servidor reiniciou no meio
        return jsonify(status="erro", resposta="A resposta demorou demais. Tente de novo.")
    return jsonify(status=r[0], resposta=r[1])

@app.post("/assistente/limpar")
def assistente_limpar():
    ia.limpar()
    return redirect("/assistente")
