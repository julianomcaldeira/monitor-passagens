"""Planejador de viagem: gera um plano completo com IA e salva no banco."""
import json, re, threading
from datetime import date, timedelta
from urllib.parse import quote_plus
from db import conectar
import servicos as sv
import assistente as ia

TIPOS = {"hotel": ("🏨", "Hospedagem"), "restaurante": ("🍽️", "Restaurantes"), "passeio": ("🎟️", "Passeios"),
         "transporte": ("🚆", "Transporte"), "dica": ("💡", "Dicas")}
INTERESSES = ["Gastronomia", "Cultura e museus", "Natureza", "Praia", "Compras", "Vida noturna",
              "Esportes e eventos", "História", "Arte", "Fotografia", "Vinhos", "Relaxar"]

def _fmt(v):
    return json.dumps(v, ensure_ascii=False, default=str)

def iniciar(viagem_id):
    with conectar() as c:
        c.execute("update viagens set plano_status='gerando', plano_iniciado_em=now() where id=%s", (viagem_id,))
    threading.Thread(target=_gerar_seguro, args=(viagem_id,), daemon=True).start()

def _gerar_seguro(viagem_id):
    try:
        gerar(viagem_id)
    except Exception as e:
        print("planejador", viagem_id, e)
        with conectar() as c:
            c.execute("update viagens set plano_status='erro', plano_resumo=%s where id=%s",
                      (f"Erro ao gerar o plano: {e}", viagem_id))

def _passagem(v):
    """Preço real da passagem (Travelpayouts) antes de chamar a IA."""
    if not (v["destino"] and v["data_inicio"]):
        return None
    try:
        r = ia.t_buscar_passagens(v["destino"], str(v["data_inicio"]), str(v["data_fim"] or "") or None,
                                  v.get("origem") or None)
        return None if r.get("erro") else r
    except Exception as e:
        print("passagem", e)
        return None

def _json(texto):
    m = re.search(r"\{.*\}", texto or "", re.S)
    if not m:
        raise ValueError("a IA não devolveu um plano válido")
    return json.loads(m.group(0))

def gerar(viagem_id):
    v = ia.viagem(viagem_id)
    perfil = {k: x for k, x in ia.perfil().items() if x}
    dias = (v["data_fim"] - v["data_inicio"]).days + 1 if v["data_inicio"] and v["data_fim"] else None
    passagem = _passagem(v)
    viajantes = v.get("viajantes") or 1

    sistema = ("Você é um planejador de viagens especialista. Responda SOMENTE com um objeto JSON válido, "
               "sem texto antes ou depois, sem markdown. Valores em reais (BRL), números sem símbolo.")
    pedido = f"""Planeje esta viagem em detalhe, em português do Brasil.

Viagem: {_fmt({k: v[k] for k in ('nome', 'destino', 'data_inicio', 'data_fim', 'orcamento', 'notas')})}
Dias: {dias or 'não definido'} · Viajantes: {viajantes} · Saindo de: {v.get('origem') or perfil.get('aeroporto_padrao') or 'VCP'} (região de Campinas/SP)
Preferências desta viagem: {v.get('preferencias') or '—'}
Perfil do viajante: {_fmt(perfil) if perfil else '—'}
Passagem aérea (preço real, ida e volta por pessoa): {_fmt(passagem) if passagem else 'sem dado; estime'}

Considere eventos na data (ex.: torneios, festivais), clima da época e deslocamentos realistas.
Distribua o roteiro respeitando chegada e partida. Ajuste a faixa de preço ao orçamento e ao perfil.

Formato exato:
{{
 "resumo": "2 a 4 frases sobre o plano e por que combina com o viajante",
 "hoteis": [{{"titulo": "nome do hotel", "local": "bairro", "descricao": "por que escolher", "preco_estimado": 0}}],
 "restaurantes": [{{"titulo": "", "local": "", "descricao": "tipo de comida e destaque", "preco_estimado": 0}}],
 "passeios": [{{"titulo": "", "local": "", "descricao": "", "preco_estimado": 0}}],
 "transporte": [{{"titulo": "ex.: trem aeroporto-centro", "descricao": "como e quando usar", "preco_estimado": 0}}],
 "dicas": [{{"titulo": "", "descricao": ""}}],
 "roteiro": [{{"dia": "AAAA-MM-DD", "horario": "HH:MM", "titulo": "", "descricao": ""}}],
 "custos": [{{"categoria": "passagem|hospedagem|alimentacao|transporte|passeios|compras|seguro|outros", "descricao": "", "valor": 0}}]
}}
Regras: 3 hotéis (preco_estimado = diária total do quarto), 6 a 8 restaurantes (preço médio por pessoa),
5 a 8 passeios (preço por pessoa), 2 a 4 transportes, 4 a 6 dicas práticas, roteiro com 3 a 5 itens por dia
(use as datas reais da viagem), e custos previstos TOTAIS da viagem para {viajantes} pessoa(s), por categoria."""
    plano = _json(ia.gerar_texto(sistema, pedido, max_tokens=8000))
    salvar(v, plano, passagem)

def _link(tipo, titulo, local, destino):
    q = quote_plus(f"{titulo} {local or ''} {destino or ''}".strip())
    if tipo == "hotel":
        return f"https://www.booking.com/searchresults.html?ss={q}"
    if tipo in ("restaurante", "passeio"):
        return f"https://www.google.com/maps/search/?api=1&query={q}"
    return None

def salvar(v, plano, passagem):
    i = v["id"]
    with conectar() as c:
        # recomeça o plano: limpa só o que a IA gerou antes (sugestões ainda não escolhidas e previsões)
        c.execute("delete from sugestoes where viagem_id=%s and status<>'escolhida'", (i,))
        c.execute("delete from gastos where viagem_id=%s and not pago", (i,))
        c.execute("delete from roteiro where viagem_id=%s", (i,))
        for chave, tipo in (("hoteis", "hotel"), ("restaurantes", "restaurante"), ("passeios", "passeio"),
                            ("transporte", "transporte"), ("dicas", "dica")):
            for s in plano.get(chave) or []:
                if not s.get("titulo"): continue
                c.execute("""insert into sugestoes(viagem_id,tipo,titulo,descricao,preco_estimado,local,link)
                             values(%s,%s,%s,%s,%s,%s,%s)""",
                          (i, tipo, s["titulo"], s.get("descricao"), s.get("preco_estimado") or None, s.get("local"),
                           _link(tipo, s["titulo"], s.get("local"), v["destino"])))
        for r in plano.get("roteiro") or []:
            if not r.get("titulo"): continue
            try: dia = date.fromisoformat(r.get("dia") or "")
            except ValueError: dia = None
            c.execute("insert into roteiro(viagem_id,dia,horario,titulo,descricao) values(%s,%s,%s,%s,%s)",
                      (i, dia, r.get("horario"), r["titulo"], r.get("descricao")))
        for g in plano.get("custos") or []:
            try: valor = float(g.get("valor") or 0)
            except (TypeError, ValueError): valor = 0
            if valor <= 0: continue
            cat = g.get("categoria") if g.get("categoria") in ia.CATEGORIAS else "outros"
            c.execute("insert into gastos(viagem_id,categoria,descricao,valor,pago) values(%s,%s,%s,%s,false)",
                      (i, cat, g.get("descricao") or "Estimativa do planejador", valor))
        # monitora a passagem da viagem
        if v["destino"] and v["data_inicio"]:
            r = sv.resolver(v["destino"])
            if r:
                c.execute("""insert into monitorados(origem,destino,nome_destino,data_ida,data_volta,viagem_id)
                             values(%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                          (v.get("origem") or ia.perfil().get("aeroporto_padrao") or "VCP", r[0], r[1],
                           v["data_inicio"], v["data_fim"], i))
        c.execute("update viagens set plano_status='pronto', plano_resumo=%s where id=%s", (plano.get("resumo"), i))

# ---------- progresso / fechamento ----------
def progresso(v, sugestoes):
    escolhidas = {s["tipo"] for s in sugestoes if s["status"] == "escolhida"}
    passagem = any(g["categoria"] == "passagem" and g["pago"] for g in v["gastos"])
    itens = [
        ("Passagem comprada", passagem, "Registre o gasto da passagem como pago"),
        ("Hospedagem escolhida", "hotel" in escolhidas, "Escolha um hotel nas sugestões"),
        ("Roteiro montado", bool(v["roteiro"]), "Gere o plano ou adicione atividades"),
        ("Passeios escolhidos", "passeio" in escolhidas, "Marque os passeios que quer fazer"),
        ("Restaurantes escolhidos", "restaurante" in escolhidas, "Marque restaurantes de interesse"),
        ("Transporte definido", "transporte" in escolhidas, "Escolha como se deslocar"),
    ]
    feitos = sum(1 for _, ok, _ in itens if ok)
    return dict(itens=itens, feitos=feitos, total=len(itens), pct=round(feitos / len(itens) * 100))
