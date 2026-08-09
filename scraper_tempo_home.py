"""
Scraper de tempo-na-home — cobertura eleitoral (Folha)
========================================================

O QUE FAZ:
Roda periodicamente (ex: a cada 5-10 min, via cron ou loop com sleep),
tira uma "foto" da homepage, e rastreia quanto tempo cada matéria
sobre os candidatos (Lula, Flávio, Tarcísio, Haddad) ficou exposta.

Gera uma linha no CSV toda vez que uma matéria SAI da home, com:
categoria_texto, data_coleta, editoria_texto, entrada, local, saida,
tempo_minutos, total_minutos, url

IMPORTANTE — ISSO NÃO É UM SCRIPT DE RODAR UMA VEZ:
Só funciona observando a home repetidamente ao longo do tempo. Rodar
uma vez só só cria os registros de "entrada" (sem saída, sem duração).
Quanto mais frequente a execução, mais precisa a medição de tempo —
mas também mais chamadas ao site. 5-10 minutos é um equilíbrio razoável.

ANTES DE RODAR — AJUSTE:
1. HOMEPAGE_URL — confirme que é a home certa (nacional? SP?).
2. TERMOS_CANDIDATOS — mesma lista de termos usada nas queries SQL,
   pra manter os critérios de "é sobre esse candidato" consistentes.

Seletores de manchete (c-main-headline, c-list-links,
c-columns-blogs-section) já foram confirmados a partir do HTML real
da home em 09/08/2026. Se a Folha mudar o layout, extrair_manchetes()
vai passar a retornar lista vazia — nesse caso repita a inspeção do
HTML (F12 ou Ctrl+U) e ajuste os seletores na função.

USO:
    pip install requests beautifulsoup4 --break-system-packages
    python scraper_tempo_home.py

    # pra rodar em loop contínuo, a cada 5 min:
    while true; do python scraper_tempo_home.py; sleep 300; done

    # ou agendar via cron (recomendado p/ rodar em background):
    */5 * * * * cd /caminho/do/script && python scraper_tempo_home.py

SAÍDA:
    tempo_home_estado.json  — estado interno entre execuções (não mexer)
    tempo_home.csv           — uma linha por matéria que saiu da home
"""

import csv
import json
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ── CONFIGURAÇÃO ────────────────────────────────────────────────────

HOMEPAGE_URL = "https://www1.folha.uol.com.br/"

ARQUIVO_ESTADO = "tempo_home_estado.json"
ARQUIVO_CSV = "tempo_home.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; pesquisa-interna-redacao/1.0)"
}

# Mesmos termos usados nas queries SQL, pra manter consistência
TERMOS_CANDIDATOS = {
    "Lula": ["lula"],
    "Flávio": ["flávio"],
    "Tarcísio": ["tarcísio"],
    "Haddad": ["haddad"],
}


def buscar_homepage() -> BeautifulSoup:
    resp = requests.get(HOMEPAGE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def extrair_manchetes(soup: BeautifulSoup):
    """
    Extrai a lista de manchetes atualmente na home, com posição.

    Cobre os três padrões de bloco confirmados na home real da Folha:
    - c-main-headline: a manchete principal (título vem num <h2> dentro
      de um <a>)
    - c-list-links: manchetes secundárias, mesmo padrão de <h2> dentro
      de <a>
    - c-columns-blogs-section: blocos de colunistas, onde o título fica
      direto num <a> (sem <h2> por dentro)

    Se a Folha mudar o layout da home, esses seletores vão parar de
    encontrar itens (extrair_manchetes retorna lista vazia) — nesse
    caso repita o processo de inspecionar o HTML real e ajustar aqui.
    """
    manchetes = []

    elementos = soup.select(
        "h2.c-main-headline__title, "
        "h2.c-list-links__title, "
        ".c-columns-blogs-section__item-description a"
    )

    for posicao, el in enumerate(elementos, start=1):
        if el.name == "h2":
            # título fica dentro de um <a> (ex: c-main-headline, c-list-links)
            link_tag = el.find_parent("a")
            titulo = el.get_text(strip=True)
        else:
            # título é o próprio <a> (ex: colunas/blogs)
            link_tag = el
            titulo = el.get_text(strip=True)

        if not link_tag:
            continue

        url = link_tag.get("href", "")

        manchetes.append({
            "url": url,
            "titulo": titulo,
            "local": posicao,  # posição na home no momento da coleta
        })

    return manchetes


def identificar_candidato(titulo: str):
    """Retorna o nome do candidato se o título mencionar algum, senão None."""
    titulo_lower = titulo.lower()
    candidatos_encontrados = [
        nome for nome, termos in TERMOS_CANDIDATOS.items()
        if any(termo in titulo_lower for termo in termos)
    ]
    if not candidatos_encontrados:
        return None
    return " e ".join(candidatos_encontrados)


def extrair_editoria(url: str) -> str:
    """
    Deriva a editoria a partir do caminho da URL (ex: /poder/,
    /colunas/painel/ etc), já que a home não expõe a editoria
    diretamente. É uma aproximação — cruzar depois com o campo
    'editoria' do warehouse (via URL) pra confirmar.
    """
    match = re.search(r"folha\.uol\.com\.br/([^/]+(?:/[^/]+)?)/", url)
    return match.group(1) if match else ""


def carregar_estado() -> dict:
    if os.path.exists(ARQUIVO_ESTADO):
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def salvar_estado(estado: dict):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def garantir_csv_com_cabecalho():
    if not os.path.exists(ARQUIVO_CSV):
        with open(ARQUIVO_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "categoria_texto", "data_coleta", "editoria_texto",
                "entrada", "local", "saida", "tempo_minutos",
                "total_minutos", "url",
            ])


def registrar_saida(url: str, registro: dict, agora: datetime, estado_totais: dict):
    entrada = datetime.fromisoformat(registro["entrada"])
    tempo_minutos = round((agora - entrada).total_seconds() / 60, 1)

    total_anterior = estado_totais.get(url, 0)
    total_minutos = round(total_anterior + tempo_minutos, 1)
    estado_totais[url] = total_minutos

    with open(ARQUIVO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            registro["categoria_texto"],
            agora.strftime("%Y-%m-%d"),
            registro["editoria_texto"],
            registro["entrada"],
            registro["local"],
            agora.isoformat(timespec="seconds"),
            tempo_minutos,
            total_minutos,
            url,
        ])


def main():
    garantir_csv_com_cabecalho()
    estado = carregar_estado()
    urls_em_estado = estado.get("ativos", {})
    totais = estado.get("totais", {})

    agora = datetime.now()
    soup = buscar_homepage()
    manchetes = extrair_manchetes(soup)

    urls_atuais = {}
    for m in manchetes:
        categoria = identificar_candidato(m["titulo"])
        if not categoria:
            continue  # só nos interessa matéria sobre os candidatos
        urls_atuais[m["url"]] = {
            "categoria_texto": categoria,
            "editoria_texto": extrair_editoria(m["url"]),
            "local": m["local"],
        }

    # Matérias novas na home → registrar entrada
    for url, info in urls_atuais.items():
        if url not in urls_em_estado:
            urls_em_estado[url] = {
                "categoria_texto": info["categoria_texto"],
                "editoria_texto": info["editoria_texto"],
                "entrada": agora.isoformat(timespec="seconds"),
                "local": info["local"],
            }
        else:
            # continua na home — atualiza só a posição
            urls_em_estado[url]["local"] = info["local"]

    # Matérias que saíram da home → registrar saída e gravar no CSV
    urls_que_sairam = [u for u in urls_em_estado if u not in urls_atuais]
    for url in urls_que_sairam:
        registrar_saida(url, urls_em_estado[url], agora, totais)
        del urls_em_estado[url]

    salvar_estado({"ativos": urls_em_estado, "totais": totais})

    print(f"[{agora.isoformat(timespec='seconds')}] "
          f"{len(urls_atuais)} matérias de candidatos na home | "
          f"{len(urls_que_sairam)} saíram nesta rodada")


if __name__ == "__main__":
    main()
