#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TORRE DE COMANDO LGM — Monitor da API Umbler Talk (UTalk v1)  [versao 2]
=========================================================================
Resolve, com 3 endpoints, os dois problemas invisiveis da auditoria:
  1) conversas abertas em que o CLIENTE falou por ultimo e ninguem respondeu
  2) canal de WhatsApp fora do ar (hoje ninguem percebe que caiu)
E permite agendar follow-up proativo que se auto-cancela se o cliente responder.

COMO USAR
---------
  pip install requests
  set UTALK_TOKEN=seu_token_bearer          (Windows)  | export UTALK_TOKEN=...
  set UTALK_ORG=id_da_organizacao           (opcional; o script descobre sozinho)

  python monitor_umbler.py fila 120         # fila parada ha mais de 120 min
  python monitor_umbler.py canais           # checa se algum canal caiu
  python monitor_umbler.py sla 30           # tempo de 1a resposta dos ultimos 30 dias
  python monitor_umbler.py debug            # imprime o JSON cru de 1 conversa
  python monitor_umbler.py followup TEL_CLIENTE TEL_CANAL "texto" [dias] --confirmar

CODIGO DE SAIDA (para o Agendador de Tarefas do Windows)
  0 = tudo certo | 1 = falha na consulta/erro | 2 = ALERTA (fila parada ou canal fora)

ALERTAS AUTOMATICOS (opcionais; configure por variaveis de ambiente)
  E-mail: ALERTA_SMTP_HOST, ALERTA_SMTP_PORT (587), ALERTA_SMTP_USER,
          ALERTA_SMTP_SENHA, ALERTA_EMAIL_PARA
  Webhook (Slack/Zapier/Flowera etc.): ALERTA_WEBHOOK_URL  (recebe {"text": "..."})

Token: Umbler Talk > Configuracoes > API/Integracoes (botao "Autorizar" do Swagger).
"""

import json
import logging
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

try:
    import requests
except ImportError:
    sys.exit("Falta a biblioteca requests. Rode: pip install requests")

# --------------------------------------------------------------------------
# CONFIGURACAO
# --------------------------------------------------------------------------
BASE = "https://app-utalk.umbler.com/api/v1"
TOKEN = os.environ.get("UTALK_TOKEN", "").strip()
ORG = os.environ.get("UTALK_ORG", "").strip()
TIMEOUT = 30                  # segundos por requisicao
TENTATIVAS = 3                # tentativas em erro temporario (429, 5xx, rede)
PAGINA = 100                  # itens por pagina
MAX_PAGINAS = 20              # trava de seguranca (20 x 100 = 2.000 conversas)
FUSO = timezone(timedelta(hours=-3))   # America/Sao_Paulo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("monitor_umbler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("umbler")


class FalhaConsulta(Exception):
    """A API nao respondeu de forma utilizavel.
    Diferente de 'lista vazia': aqui NAO sabemos se esta tudo bem."""


# --------------------------------------------------------------------------
# CAMADA DE REDE
# Toda chamada passa por aqui. Erros temporarios (429, 5xx, queda de rede)
# sao repetidos com espera crescente. Erros definitivos (401, 403, 4xx)
# levantam FalhaConsulta, para o monitor nunca confundir "falhou" com "fila limpa".
# --------------------------------------------------------------------------
def _requisitar(metodo, caminho, params=None, corpo=None):
    if not TOKEN:
        raise FalhaConsulta("UTALK_TOKEN nao definido. Defina a variavel de ambiente.")

    url = f"{BASE}{caminho}"
    cabecalhos = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if corpo is not None:
        cabecalhos["Content-Type"] = "application/json"

    ultimo_erro = "desconhecido"
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            r = requests.request(metodo, url, headers=cabecalhos, params=params,
                                 json=corpo, timeout=TIMEOUT)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            ultimo_erro = f"rede/timeout: {e}"
        except requests.exceptions.RequestException as e:
            raise FalhaConsulta(f"{metodo} {caminho}: erro de requisicao: {e}")
        else:
            if r.status_code == 401:
                raise FalhaConsulta("Token invalido ou expirado (401). Gere um novo token no Umbler.")
            if r.status_code == 403:
                raise FalhaConsulta(f"Sem permissao (403) em {caminho}. Use um usuario administrador.")
            if r.status_code == 429 or r.status_code >= 500:
                ultimo_erro = f"HTTP {r.status_code}"
                # respeita Retry-After quando a API informa
                try:
                    espera_api = int(r.headers.get("Retry-After", 0))
                except ValueError:
                    espera_api = 0
                if tentativa < TENTATIVAS and espera_api:
                    time.sleep(min(espera_api, 60))
                    continue
            elif not r.ok:
                raise FalhaConsulta(f"{metodo} {caminho} devolveu HTTP {r.status_code}: {r.text[:300]}")
            else:
                try:
                    return r.json()
                except json.JSONDecodeError:
                    if metodo == "POST":      # alguns POSTs respondem vazio
                        return {"ok": True}
                    raise FalhaConsulta(f"Resposta de {caminho} nao e JSON valido: {r.text[:200]}")

        if tentativa < TENTATIVAS:
            espera = 2 ** tentativa           # 2s, 4s
            log.warning("Falha temporaria em %s (%s). Nova tentativa em %ss (%s/%s).",
                        caminho, ultimo_erro, espera, tentativa, TENTATIVAS)
            time.sleep(espera)

    raise FalhaConsulta(f"{metodo} {caminho} falhou apos {TENTATIVAS} tentativas ({ultimo_erro}).")


def api_get(caminho, params=None):
    return _requisitar("GET", caminho, params=params)


def api_post(caminho, corpo):
    return _requisitar("POST", caminho, corpo=corpo)


def descobrir_org():
    """Se UTALK_ORG nao foi informado, pega a primeira organizacao do usuario logado."""
    global ORG
    if ORG:
        return ORG
    eu = api_get("/members/me/")
    # A estrutura pode variar; tenta os caminhos mais provaveis sem quebrar.
    orgs = (eu or {}).get("organizations") or (eu or {}).get("Organizations") or []
    for o in orgs:
        ident = (o.get("organization") or {}).get("id") or o.get("id")
        if ident:
            ORG = ident
            log.info("Organizacao detectada automaticamente: %s", ORG)
            return ORG
    raise FalhaConsulta("Nao foi possivel detectar a organizacao. Defina UTALK_ORG.")


# --------------------------------------------------------------------------
# UTILITARIOS
# --------------------------------------------------------------------------
def ler_data(valor):
    """Converte data ISO da API em datetime com fuso UTC. Devolve None se invalida.
    Trata: sufixo 'Z', fracao de segundo com 7 digitos (padrao .NET, quebra o
    Python < 3.11) e datas sem fuso (assume UTC)."""
    if not valor:
        return None
    try:
        texto = str(valor).strip().replace("Z", "+00:00")
        texto = re.sub(r"(\.\d{6})\d+", r"\1", texto)       # corta p/ 6 casas
        dt = datetime.fromisoformat(texto)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def telefone(chat):
    """Extrai o primeiro telefone do contato, sem quebrar se a estrutura mudar."""
    try:
        fones = (chat.get("contact") or {}).get("phoneNumbers") or []
        if not fones:
            return "-"
        primeiro = fones[0]
        if isinstance(primeiro, dict):
            return primeiro.get("phone") or primeiro.get("number") or str(primeiro)
        return str(primeiro)
    except (AttributeError, TypeError, IndexError):
        return "-"


def nome(chat):
    return (chat.get("contact") or {}).get("name") or "(sem nome)"


def itens(resposta):
    """A API devolve paginacao; esta funcao acha a lista independente do nome do campo."""
    if resposta is None:
        return []
    if isinstance(resposta, list):
        return resposta
    if isinstance(resposta, dict):
        for chave in ("items", "Items", "data", "results"):
            if isinstance(resposta.get(chave), list):
                return resposta[chave]
    return []


def listar_todos(caminho, params):
    """Busca TODAS as paginas (Skip/Take). Para quando a pagina vem menor que o
    tamanho, quando nao traz ids novos (API ignorou o Skip) ou na trava MAX_PAGINAS.
    Devolve (lista, completo). completo=False => resultado pode estar truncado."""
    todos, vistos = [], set()
    for pagina in range(MAX_PAGINAS):
        p = dict(params, Take=PAGINA, Skip=pagina * PAGINA)
        lote = itens(api_get(caminho, p))
        novos = [c for c in lote if not isinstance(c, dict) or c.get("id") not in vistos]
        for c in novos:
            if isinstance(c, dict) and c.get("id") is not None:
                vistos.add(c["id"])
        todos.extend(novos)
        if len(lote) < PAGINA:
            return todos, True
        if not novos:
            log.warning("A API repetiu a mesma pagina (Skip ignorado?). Paginacao interrompida.")
            return todos, False
    log.warning("Atingido o limite de %s paginas; ha mais conversas alem das lidas.", MAX_PAGINAS)
    return todos, False


# --------------------------------------------------------------------------
# ALERTAS (e-mail e/ou webhook; ambos opcionais)
# --------------------------------------------------------------------------
def enviar_alerta(assunto, texto):
    """Dispara alerta pelos canais configurados. Falha no alerta nunca derruba o script."""
    webhook = os.environ.get("ALERTA_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            requests.post(webhook, json={"text": f"{assunto}\n{texto}"}, timeout=TIMEOUT)
            log.info("Alerta enviado ao webhook.")
        except requests.exceptions.RequestException as e:
            log.error("Falha ao enviar alerta ao webhook: %s", e)

    host = os.environ.get("ALERTA_SMTP_HOST", "").strip()
    destino = os.environ.get("ALERTA_EMAIL_PARA", "").strip()
    if host and destino:
        usuario = os.environ.get("ALERTA_SMTP_USER", "").strip()
        senha = os.environ.get("ALERTA_SMTP_SENHA", "")
        try:
            porta = int(os.environ.get("ALERTA_SMTP_PORT", "587"))
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = assunto, usuario or destino, destino
            msg.set_content(texto)
            with smtplib.SMTP(host, porta, timeout=TIMEOUT) as smtp:
                smtp.starttls()
                if usuario:
                    smtp.login(usuario, senha)
                smtp.send_message(msg)
            log.info("Alerta enviado por e-mail para %s.", destino)
        except (smtplib.SMTPException, OSError, ValueError) as e:
            log.error("Falha ao enviar alerta por e-mail: %s", e)


# --------------------------------------------------------------------------
# 1) FILA PARADA — o cliente falou por ultimo e ninguem respondeu
#    GET /v1/chats/?ChatState=Open&LastMessage=Contact&ChatOrderBy=WaitingSince
# --------------------------------------------------------------------------
def fila_parada(minutos_sla=120):
    org = descobrir_org()
    conversas, completo = listar_todos("/chats/", {
        "organizationId": org,
        "ChatState": "Open",        # somente conversas abertas
        "LastMessage": "Contact",   # a ultima mensagem e do CLIENTE
        "ChatOrderBy": "WaitingSince",
    })

    agora = datetime.now(timezone.utc)
    estouradas = []
    for c in conversas:
        marco = ler_data(c.get("waitingSinceUTC")) or ler_data(c.get("eventAtUTC"))
        minutos = int((agora - marco).total_seconds() // 60) if marco else None
        if minutos is None or minutos >= minutos_sla:
            estouradas.append({
                "nome": nome(c),
                "telefone": telefone(c),
                "minutos": minutos,
                "atendente": ((c.get("organizationMember") or {}).get("name") or "SEM TITULAR"),
                "setor": ((c.get("sector") or {}).get("name") or "-"),
                "etiquetas": [t.get("name") for t in (c.get("tags") or []) if isinstance(t, dict)],
                "id": c.get("id"),
            })

    # 'sem data' (None) vai para o topo; 0 minuto e tratado como 0 mesmo
    estouradas.sort(key=lambda x: -(x["minutos"] if x["minutos"] is not None else 10**6))

    print(f"\n=== FILA PARADA (SLA {minutos_sla} min) — {len(estouradas)} conversa(s) "
          f"de {len(conversas)} aguardando ===")
    if not completo:
        print("  ATENCAO: leitura incompleta (limite de paginas). Ha conversas nao verificadas.")
    linhas = []
    for e in estouradas:
        horas = f"{e['minutos']//60}h{e['minutos']%60:02d}" if e["minutos"] is not None else "?"
        linha = (f"[{horas:>7}] {e['nome'][:32]:<32} {e['telefone']:<16} "
                 f"titular: {e['atendente'][:18]:<18} setor: {e['setor'][:12]}")
        linhas.append(linha)
        print("  " + linha)
    if not estouradas:
        print("  Nada acima do SLA.")
    else:
        enviar_alerta(f"[LGM] {len(estouradas)} conversa(s) sem resposta ha mais de {minutos_sla} min",
                      "\n".join(linhas[:50]))
    return estouradas


# --------------------------------------------------------------------------
# 2) CANAL FORA DO AR — GET /v1/channels/  (state deve ser "Live")
# --------------------------------------------------------------------------
def canais():
    org = descobrir_org()
    lista = itens(api_get("/channels/", {"organizationId": org}))
    if not lista:
        raise FalhaConsulta("Nenhum canal retornado (ou a organizacao nao tem canais).")

    problemas = []
    print("\n=== CANAIS ===")
    for ch in lista:
        estado = ch.get("state") or ch.get("channelState") or "?"
        rotulo = ch.get("displayName") or ch.get("name") or ch.get("id")
        ok = str(estado).lower() == "live"
        print(f"  {'OK  ' if ok else 'FORA'} | {str(rotulo)[:28]:<28} | estado: {estado}")
        if not ok:
            problemas.append({"canal": rotulo, "estado": estado, "id": ch.get("id")})

    if problemas:
        resumo = "\n".join(f"- {p['canal']}: {p['estado']}" for p in problemas)
        log.error("ALERTA: %s canal(is) fora do ar. Nenhuma mensagem sai e nenhum "
                  "gatilho dispara ate reconectar.", len(problemas))
        enviar_alerta(f"[LGM] {len(problemas)} canal(is) de WhatsApp FORA DO AR", resumo)
    return problemas


# --------------------------------------------------------------------------
# 3) SLA REAL DE PRIMEIRA RESPOSTA
#    A API entrega firstContactMessage e firstMemberReplyMessage em cada conversa.
# --------------------------------------------------------------------------
def _data_msg(chat, campo):
    msg = chat.get(campo)
    return ler_data(msg.get("eventAtUTC")) if isinstance(msg, dict) else None


def sla(dias=30):
    org = descobrir_org()
    inicio = (datetime.now(timezone.utc) - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conversas, completo = listar_todos("/chats/", {
        "organizationId": org,
        "ChatState": "All",
        "DateStartCreatedAtUTC": inicio,
    })
    if not conversas:
        log.warning("Sem conversas no periodo de %s dias.", dias)
        return

    tempos, sem_resposta = [], 0
    for c in conversas:
        inicio_cliente = _data_msg(c, "firstContactMessage")
        primeira_resposta = _data_msg(c, "firstMemberReplyMessage")
        if inicio_cliente and primeira_resposta:
            minutos = (primeira_resposta - inicio_cliente).total_seconds() / 60
            if minutos >= 0:
                tempos.append(minutos)
        elif inicio_cliente and not primeira_resposta:
            sem_resposta += 1

    print(f"\n=== SLA DE 1a RESPOSTA — ultimos {dias} dias ({len(conversas)} conversas) ===")
    if not completo:
        print("  ATENCAO: amostra incompleta (limite de paginas); numeros sao parciais.")
    if tempos:
        tempos.sort()
        media = sum(tempos) / len(tempos)
        n = len(tempos)
        mediana = tempos[n // 2] if n % 2 else (tempos[n // 2 - 1] + tempos[n // 2]) / 2
        dentro_1h = sum(1 for t in tempos if t <= 60) / n * 100
        print(f"  respondidas .......... {n}")
        print(f"  media ................ {media/60:.1f} h")
        print(f"  mediana .............. {mediana/60:.1f} h")
        print(f"  dentro de 1 hora ..... {dentro_1h:.0f} %")
    else:
        print("  Nenhuma conversa com 1a resposta registrada no periodo.")
    print(f"  NUNCA respondidas .... {sem_resposta}")


# --------------------------------------------------------------------------
# 4) FOLLOW-UP PROATIVO COM AUTO-CANCELAMENTO
#    POST /v1/scheduled-messages/ com cancelUpon:
#      CustomerMessage / OrganizationMemberMessage / ClosedChat
#    ATENCAO (LGPD/OAB): use apenas com clientes que ja tem relacionamento
#    ativo com o escritorio. Teste primeiro com o seu proprio numero.
# --------------------------------------------------------------------------
def agendar_followup(telefone_destino, telefone_canal, texto, dias_a_frente=15,
                     nome_contato=None):
    org = descobrir_org()
    quando = datetime.now(timezone.utc) + timedelta(days=dias_a_frente)
    corpo = {
        "organizationId": org,
        "dateSendAtUTC": quando.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "toPhone": telefone_destino,
        "fromPhone": telefone_canal,
        "message": texto,
        "cancelUpon": ["CustomerMessage", "OrganizationMemberMessage", "ClosedChat"],
    }
    if nome_contato:
        corpo["contactName"] = nome_contato

    r = api_post("/scheduled-messages/", corpo)
    log.info("Follow-up agendado para %s em %s (auto-cancela se houver conversa antes).",
             telefone_destino, quando.astimezone(FUSO).strftime("%d/%m/%Y %H:%M"))
    return r


# --------------------------------------------------------------------------
# 5) DEBUG — imprime o JSON cru de uma conversa.
#    Use na primeira execucao para confirmar os nomes exatos dos campos.
# --------------------------------------------------------------------------
def debug():
    org = descobrir_org()
    r = api_get("/chats/", {"organizationId": org, "ChatState": "Open", "Take": 1})
    lista = itens(r)
    if not lista:
        print("Nenhuma conversa aberta para inspecionar.")
        print("Resposta crua:", json.dumps(r, ensure_ascii=False)[:1500])
        return
    print(json.dumps(lista[0], ensure_ascii=False, indent=2)[:4000])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _inteiro(texto, padrao):
    """Converte argumento em inteiro positivo; valor invalido vira o padrao com aviso."""
    if texto is None:
        return padrao
    try:
        valor = int(texto)
        if valor > 0:
            return valor
    except ValueError:
        pass
    log.warning("Argumento '%s' invalido; usando o padrao %s.", texto, padrao)
    return padrao


def main(argv):
    comando = argv[1].lower() if len(argv) > 1 else "fila"
    argumento = argv[2] if len(argv) > 2 else None

    try:
        if comando == "fila":
            return 2 if fila_parada(_inteiro(argumento, 120)) else 0
        if comando == "canais":
            return 2 if canais() else 0
        if comando == "sla":
            sla(_inteiro(argumento, 30))
            return 0
        if comando == "debug":
            debug()
            return 0
        if comando == "followup":
            args = [a for a in argv[2:] if a != "--confirmar"]
            if len(args) < 3:
                print('Uso: followup TEL_CLIENTE TEL_CANAL "texto" [dias] --confirmar')
                return 1
            if "--confirmar" not in argv:
                print("Simulacao (nada agendado). Para agendar de verdade, adicione --confirmar.")
                print(f"  para: {args[0]} | canal: {args[1]} | texto: {args[2]}")
                return 0
            agendar_followup(args[0], args[1], args[2],
                             _inteiro(args[3] if len(args) > 3 else None, 15))
            return 0
        print(__doc__)
        return 0
    except FalhaConsulta as e:
        log.error("FALHA NA CONSULTA: %s", e)
        enviar_alerta("[LGM] Monitor Umbler com FALHA", str(e))
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuario.")
        return 1
    except Exception as e:                      # rede de seguranca final
        log.exception("Erro nao previsto: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
