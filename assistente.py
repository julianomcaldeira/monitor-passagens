"""Assistente de viagens usando a Provider API da Command Code (formato Anthropic Messages)."""
import os, json, requests
from datetime import date
from db import conectar
import servicos as sv

BASE_URL = "https://api.commandcode.ai/provider/v1"
API_KEY = os.environ.get("COMMANDCODE_API_KEY", "")
# Modelos "claude-*" usam /messages; os demais usam /chat/completions.
MODELO = os.environ.get("COMMANDCODE_MODEL", "deepseek/deepseek-v4-flash")
MAX_PASSOS = 8
CATEGORIAS = ["passagem", "hospedagem", "alimentacao", "transporte", "passeios", "compras", "seguro", "outros"]

# ---------- ferramentas ----------
def _d(v):
    return date.fromisoformat(v) if v else None

def _num(v):
    return float(v) if v is not None else None

def t_buscar_passagens(destino, ida, volta=None, origem=None):
    origem = origem or perfil().get("aeroporto_padrao") or "VCP"
    r = sv.buscar_data(_d(ida), _d(volta), destino, origem)
    if r.get("erro"): return r
    out = {"titulo": r["titulo"], "por_aeroporto": [
        {k: v[k] for k in ("aeroporto", "preco", "ida", "volta", "cia", "escalas", "link")} for v in r.get("aeroportos", [])]}
    cal = r.get("calendario")
    if cal:
        out["dia_mais_barato_no_mes"] = {"dia": cal["melhor"]["dia"], "preco": cal["melhor"]["preco"], "link": cal["melhor"]["link"]}
    return out

def t_destinos_baratos(ida, volta=None, origem=None):
    origem = origem or perfil().get("aeroporto_padrao") or "VCP"
    r = sv.buscar_data(_d(ida), _d(volta), "", origem)
    f = lambda l: [{k: v[k] for k in ("nome", "destino", "preco", "ida", "volta", "cia", "link")} for v in l[:12]]
    return {"nacionais": f(r["nac"]), "internacionais": f(r["inter"])}

def t_historico_destino(destino):
    res = sv.resolver(destino)
    if not res: return {"erro": "cidade não encontrada"}
    with conectar() as c:
        a = sv.analisar(c, "destino", res[0]) or sv.analisar(c, "barato", res[0])
    if not a: return {"info": f"Sem histórico de preços para {res[1]}. Sugira salvar o destino para monitorar."}
    return {"destino": a["nome"], "preco_atual": a["atual"]["preco"], "normal": a["mediana"], "minimo_visto": a["minimo"],
            "pct_abaixo_do_normal": a["pct"], "avaliacao": a["selo"], "coletas": a["n"], "voo": a["atual"]}

def t_salvar_destino(cidade):
    r = sv.resolver(cidade)
    if not r: return {"erro": "cidade não encontrada"}
    with conectar() as c:
        c.execute("insert into destinos(codigo,nome) values(%s,%s) on conflict do nothing", r)
    return {"ok": True, "destino": r[1], "codigo": r[0]}

def t_monitorar_passagem(destino, ida, volta=None, origem=None, viagem_id=None):
    r = sv.resolver(destino)
    if not r: return {"erro": "cidade não encontrada"}
    origem = origem or perfil().get("aeroporto_padrao") or "VCP"
    with conectar() as c:
        c.execute("""insert into monitorados(origem,destino,nome_destino,data_ida,data_volta,viagem_id)
                     values(%s,%s,%s,%s,%s,%s) on conflict do nothing""", (origem, r[0], r[1], _d(ida), _d(volta), viagem_id))
    return {"ok": True}

def t_listar_viagens():
    with conectar() as c:
        rows = c.execute("""select v.id,v.nome,v.destino,v.data_inicio,v.data_fim,v.orcamento,v.status,
            coalesce(sum(g.valor),0) from viagens v left join gastos g on g.viagem_id=v.id
            group by v.id order by v.data_inicio nulls last""").fetchall()
    return [dict(id=r[0], nome=r[1], destino=r[2], inicio=str(r[3] or ""), fim=str(r[4] or ""),
                 orcamento=_num(r[5]), status=r[6], total_gastos=float(r[7])) for r in rows]

def t_ver_viagem(viagem_id):
    v = viagem(viagem_id)
    if not v: return {"erro": "viagem não encontrada"}
    return json.loads(json.dumps(v, default=str))

def t_planejar_viagem(viagem_id):
    import planejador
    planejador.iniciar(viagem_id)
    return {"ok": True, "info": "Plano completo sendo gerado em segundo plano (1 a 2 min).", "link": f"/viagens/{viagem_id}"}

def t_escolher_sugestao(sugestao_id, escolher=True):
    with conectar() as c:
        c.execute("update sugestoes set status=%s where id=%s", ("escolhida" if escolher else "sugerida", sugestao_id))
    return {"ok": True}

def t_criar_viagem(nome, destino=None, data_inicio=None, data_fim=None, orcamento=None, notas=None):
    cod = (sv.resolver(destino) or (None,))[0] if destino else None
    with conectar() as c:
        i = c.execute("""insert into viagens(nome,destino,codigo_destino,data_inicio,data_fim,orcamento,notas)
            values(%s,%s,%s,%s,%s,%s,%s) returning id""",
            (nome, destino, cod, _d(data_inicio), _d(data_fim), orcamento, notas)).fetchone()[0]
    return {"ok": True, "viagem_id": i, "link": f"/viagens/{i}"}

CAMPOS_VIAGEM = ("nome", "destino", "data_inicio", "data_fim", "orcamento", "status", "notas")
def t_atualizar_viagem(viagem_id, **campos):
    sets = {k: v for k, v in campos.items() if k in CAMPOS_VIAGEM}
    if not sets: return {"erro": "nada para atualizar"}
    with conectar() as c:
        c.execute("update viagens set " + ",".join(f"{k}=%s" for k in sets) + " where id=%s", (*sets.values(), viagem_id))
    return {"ok": True}

def t_adicionar_roteiro(viagem_id, itens):
    with conectar() as c:
        for it in itens:
            c.execute("insert into roteiro(viagem_id,dia,horario,titulo,descricao) values(%s,%s,%s,%s,%s)",
                      (viagem_id, _d(it.get("dia")), it.get("horario"), it["titulo"], it.get("descricao")))
    return {"ok": True, "adicionados": len(itens)}

def t_registrar_gasto(viagem_id, valor, categoria="outros", descricao=None, data=None, pago=True):
    categoria = categoria if categoria in CATEGORIAS else "outros"
    with conectar() as c:
        c.execute("insert into gastos(viagem_id,categoria,descricao,valor,data,pago) values(%s,%s,%s,%s,%s,%s)",
                  (viagem_id, categoria, descricao, valor, _d(data), pago))
    return {"ok": True}

CAMPOS_PERFIL = ("nome", "aeroporto_padrao", "orcamento_medio", "estilo", "companhias", "preferencias")
def t_atualizar_perfil(**campos):
    sets = {k: v for k, v in campos.items() if k in CAMPOS_PERFIL}
    if not sets: return {"erro": "nada para atualizar"}
    with conectar() as c:
        c.execute("update perfil set " + ",".join(f"{k}=%s" for k in sets) + ", atualizado_em=now() where id=1",
                  tuple(sets.values()))
    return {"ok": True}

S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
DATA = {"type": "string", "description": "AAAA-MM-DD"}
ORIG = {"type": "string", "enum": ["VCP", "GRU", "CGH", "todos"]}

def tool(nome, desc, props, req=()):
    return {"name": nome, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": list(req)}}

TOOLS = [
    tool("buscar_passagens", "Preço ao vivo de passagem para um destino e datas, por aeroporto, e o dia mais barato do mês.",
         {"destino": S, "ida": DATA, "volta": DATA, "origem": ORIG}, ["destino", "ida"]),
    tool("destinos_baratos", "Destinos mais baratos para uma data, nacionais e internacionais.",
         {"ida": DATA, "volta": DATA, "origem": ORIG}, ["ida"]),
    tool("historico_destino", "Histórico de preços coletados de um destino: atual vs. normal.", {"destino": S}, ["destino"]),
    tool("salvar_destino", "Adiciona cidade aos destinos monitorados no painel.", {"cidade": S}, ["cidade"]),
    tool("monitorar_passagem", "Monitora o preço de uma data específica.",
         {"destino": S, "ida": DATA, "volta": DATA, "origem": ORIG, "viagem_id": I}, ["destino", "ida"]),
    tool("listar_viagens", "Lista as viagens do usuário com orçamento e gastos.", {}),
    tool("ver_viagem", "Detalhes de uma viagem: roteiro, gastos e passagens monitoradas.", {"viagem_id": I}, ["viagem_id"]),
    tool("criar_viagem", "Cria uma viagem nova.",
         {"nome": S, "destino": S, "data_inicio": DATA, "data_fim": DATA, "orcamento": N, "notas": S}, ["nome"]),
    tool("atualizar_viagem", "Altera campos de uma viagem. status: planejando, confirmada, em_andamento, concluida.",
         {"viagem_id": I, "nome": S, "destino": S, "data_inicio": DATA, "data_fim": DATA, "orcamento": N,
          "status": {"type": "string", "enum": ["planejando", "confirmada", "em_andamento", "concluida"]}, "notas": S}, ["viagem_id"]),
    tool("adicionar_roteiro", "Adiciona itens ao roteiro dia a dia de uma viagem.",
         {"viagem_id": I, "itens": {"type": "array", "items": {"type": "object", "properties": {
             "dia": DATA, "horario": S, "titulo": S, "descricao": S}, "required": ["titulo"]}}}, ["viagem_id", "itens"]),
    tool("registrar_gasto", "Registra um gasto (pago=true) ou previsão de gasto (pago=false) numa viagem, em reais.",
         {"viagem_id": I, "valor": N, "categoria": {"type": "string", "enum": CATEGORIAS}, "descricao": S,
          "data": DATA, "pago": B}, ["viagem_id", "valor"]),
    tool("planejar_viagem", "Gera o plano completo de uma viagem já criada (hotéis, restaurantes, passeios, transporte, "
         "dicas, roteiro e custos) e monitora a passagem. Use depois de criar_viagem.", {"viagem_id": I}, ["viagem_id"]),
    tool("escolher_sugestao", "Marca (ou desmarca) uma sugestão do plano como escolhida pelo usuário.",
         {"sugestao_id": I, "escolher": B}, ["sugestao_id"]),
    tool("atualizar_perfil", "Salva preferências do usuário quando ele contar algo novo sobre como gosta de viajar.",
         {"nome": S, "aeroporto_padrao": ORIG, "orcamento_medio": N, "estilo": S, "companhias": S, "preferencias": S}),
]
FUNCS = {t["name"]: globals()["t_" + t["name"]] for t in TOOLS}

# ---------- dados auxiliares ----------
def perfil():
    with conectar() as c:
        r = c.execute("select nome,aeroporto_padrao,orcamento_medio,estilo,companhias,preferencias from perfil where id=1").fetchone()
    return dict(zip(CAMPOS_PERFIL, r)) if r else {}

def viagem(i):
    with conectar() as c:
        campos = ("id", "nome", "destino", "codigo_destino", "data_inicio", "data_fim", "orcamento", "status", "notas",
                  "origem", "viajantes", "preferencias", "plano_status", "plano_resumo", "fechada_em")
        r = c.execute("select " + ",".join(campos) + " from viagens where id=%s", (i,)).fetchone()
        if not r: return None
        v = dict(zip(campos, r))
        v["sugestoes"] = [dict(zip(("id", "tipo", "titulo", "descricao", "preco_estimado", "local", "link", "status"), x))
                          for x in c.execute("""select id,tipo,titulo,descricao,preco_estimado,local,link,status
                              from sugestoes where viagem_id=%s order by (status='escolhida') desc, id""", (i,))]
        v["roteiro"] = [dict(zip(("id", "dia", "horario", "titulo", "descricao"), x)) for x in c.execute(
            "select id,dia,horario,titulo,descricao from roteiro where viagem_id=%s order by dia nulls last, horario nulls last, id", (i,))]
        v["gastos"] = [dict(zip(("id", "categoria", "descricao", "valor", "data", "pago"), x)) for x in c.execute(
            "select id,categoria,descricao,valor,data,pago from gastos where viagem_id=%s order by data nulls last, id", (i,))]
        v["monitorados"] = [dict(zip(("id", "origem", "destino", "nome", "data_ida", "data_volta"), x)) for x in c.execute(
            "select id,origem,destino,nome_destino,data_ida,data_volta from monitorados where viagem_id=%s and ativo", (i,))]
    return v

def sistema():
    p = {k: v for k, v in perfil().items() if v}
    return f"""Você é o assistente pessoal de viagens do usuário, dentro do app "Gestor de Viagens".
Hoje é {date.today().isoformat()}. Responda em português do Brasil, de forma direta e prática, usando markdown.
Perfil do usuário: {json.dumps(p, ensure_ascii=False, default=str) or 'ainda vazio'}.
Aeroportos de origem possíveis: VCP (Viracopos, preferido), GRU, CGH.
Use as ferramentas para consultar preços reais e para criar e alterar viagens, roteiros e gastos. Nunca invente preços.
Quando o usuário contar uma preferência nova, salve no perfil. Antes de apagar ou mudar algo importante, confirme.
Ao citar uma passagem, inclua o link de compra. Ao criar ou mudar uma viagem, cite o link /viagens/<id>.
Preços vêm de buscas recentes e podem mudar: lembre de confirmar no site."""

# ---------- conversa ----------
def historico(limite=30):
    with conectar() as c:
        rows = c.execute("select papel,conteudo from chat order by id desc limit %s", (limite,)).fetchall()[::-1]
    while rows and rows[0][0] != "user":
        rows = rows[1:]
    return [{"role": p, "content": t} for p, t in rows]

def post(caminho, corpo):
    r = requests.post(BASE_URL + caminho, timeout=90, json=corpo,
                      headers={"Authorization": f"Bearer {API_KEY}", "content-type": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"API {r.status_code}: {r.text[:300]}")
    return r.json()

def executar(nome, args):
    try:
        out = FUNCS[nome](**(args or {}))
    except Exception as e:
        out = {"erro": str(e)}
    return json.dumps(out, ensure_ascii=False, default=str)[:12000]

def loop_anthropic(msgs):
    """Modelos Claude: endpoint /messages."""
    final = ""
    for _ in range(MAX_PASSOS):
        r = post("/messages", {"model": MODELO, "max_tokens": 2048, "system": sistema(), "tools": TOOLS, "messages": msgs})
        blocos = r.get("content", [])
        final = "\n".join(b["text"] for b in blocos if b.get("type") == "text")
        usos = [b for b in blocos if b.get("type") == "tool_use"]
        if not usos: break
        msgs.append({"role": "assistant", "content": blocos})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": u["id"], "content": executar(u["name"], u.get("input"))} for u in usos]})
    return final

TOOLS_OPENAI = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                 "parameters": t["input_schema"]}} for t in TOOLS]

def loop_openai(msgs):
    """Demais modelos (DeepSeek, GPT, Kimi, Qwen...): endpoint /chat/completions."""
    msgs = [{"role": "system", "content": sistema()}] + msgs
    final = ""
    for _ in range(MAX_PASSOS):
        r = post("/chat/completions", {"model": MODELO, "max_tokens": 2048, "tools": TOOLS_OPENAI, "messages": msgs})
        m = r["choices"][0]["message"]
        final = m.get("content") or ""
        chamadas = m.get("tool_calls") or []
        if not chamadas: break
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": chamadas})
        for tc in chamadas:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": executar(tc["function"]["name"], args)})
    return final

def gerar_texto(system, prompt, max_tokens=4000, json_mode=False):
    """Uma chamada simples, sem ferramentas (usada pelo planejador)."""
    if not API_KEY:
        raise RuntimeError("falta configurar COMMANDCODE_API_KEY")
    msgs = [{"role": "user", "content": prompt}]
    if MODELO.startswith("claude"):
        r = post("/messages", {"model": MODELO, "max_tokens": max_tokens, "system": system, "messages": msgs})
        return "".join(b.get("text", "") for b in r.get("content", []))
    corpo = {"model": MODELO, "max_tokens": max_tokens, "messages": [{"role": "system", "content": system}] + msgs}
    if json_mode:
        try:
            r = post("/chat/completions", dict(corpo, response_format={"type": "json_object"}))
            return r["choices"][0]["message"].get("content") or ""
        except RuntimeError as e:
            if "API 4" not in str(e): raise  # modelo sem suporte a modo JSON: tenta sem
    r = post("/chat/completions", corpo)
    return r["choices"][0]["message"].get("content") or ""

def responder(texto):
    if not API_KEY:
        return "⚠️ Falta configurar `COMMANDCODE_API_KEY` no Render (Environment)."
    msgs = historico() + [{"role": "user", "content": texto}]
    final = (loop_anthropic if MODELO.startswith("claude") else loop_openai)(msgs)
    final = final or "Não consegui concluir. Tente reformular."
    with conectar() as c:
        c.execute("insert into chat(papel,conteudo) values('user',%s),('assistant',%s)", (texto, final))
    return final

def limpar():
    with conectar() as c:
        c.execute("delete from chat")
