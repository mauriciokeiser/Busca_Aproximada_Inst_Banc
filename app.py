import os
import sys
import json
import csv
import random
from datetime import datetime
import requests
from thefuzz import fuzz, process
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt

# Configurações globais (Configuração via arquivo / Variáveis de ambiente)
API_URL = os.environ.get("BANKS_API_URL", "https://brasilapi.com.br/api/banks/v1")
DB_NAME = "bancos.db"
LOG_FILE = "app_bancos.log"

console = Console()

# ==========================================
# SISTEMA DE LOGGING
# ==========================================
def log_event(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{level.upper()}] {message}\n")
    except Exception:
        pass

# ==========================================
# BANCO DE DADOS (SQLite)
# ==========================================
def init_db():
    import sqlite3
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bancos (
            ispb TEXT PRIMARY KEY,
            name TEXT,
            code INTEGER,
            fullName TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadados (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_db_connection():
    import sqlite3
    return sqlite3.connect(DB_NAME)

# ==========================================
# FUNÇÕES CORE DO MENU
# ==========================================

def sincronizar_bancos():
    console.print("[yellow]Consultando a API do BrasilAPI...[/yellow]")
    log_event("INFO", f"Iniciando sincronização com a API: {API_URL}")
    
    try:
        response = requests.get(API_URL, timeout=15)
        response.raise_for_status()
        bancos = response.json()
    except Exception as e:
        console.print(f"[red]Erro ao consultar a API: {e}[/red]")
        log_event("ERROR", f"Erro na consulta HTTP: {e}")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM bancos")  # Limpa para sincronizar atualizado
        
        inseridos = 0
        for b in bancos:
            # Garante que campos nulos não quebrem o banco
            ispb = b.get("ispb") or f"SEM_ISPB_{random.randint(1000,9999)}"
            name = b.get("name") or "SEM NOME"
            code = b.get("code")
            fullname = b.get("fullName") or "SEM NOME COMPLETO"
            
            cursor.execute(
                "INSERT OR REPLACE INTO bancos (ispb, name, code, fullName) VALUES (?, ?, ?, ?)",
                (str(ispb), str(name), code, str(fullname))
            )
            inseridos += 1
            
        # Atualiza data da última consulta nos metadados
        cursor.execute("INSERT OR REPLACE INTO metadados (chave, valor) VALUES (?, ?)", 
                       ("ultima_sincronizacao", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        console.print(Panel(f"[green]Sincronização concluída com sucesso![/green]\nTotal de bancos armazenados: [bold]{inseridos}[/bold]", title="Resumo"))
        log_event("INFO", f"Sincronização concluída. {inseridos} registros inseridos.")
    except Exception as e:
        conn.rollback()
        console.print(f"[red]Erro ao salvar no banco de dados: {e}[/red]")
        log_event("ERROR", f"Erro no banco durante sincronização: {e}")
    finally:
        conn.close()

def busca_aproximada_nome():
    termo = Prompt.ask("[cyan]Digite o nome ou fragmento do banco[/cyan]").strip()
    if not termo:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, name, fullName FROM bancos")
    records = cursor.fetchall()
    conn.close()

    if not records:
        console.print("[red]Nenhum banco cadastrado. Sincronize primeiro![/red]")
        return

    # Mapeamento para busca por aproximação usando o fullName como principal alvo
    opcoes = {r[2]: (r[0], r[1]) for r in records} # fullName -> (code, name)
    
    # Extrai os 5 melhores resultados
    resultados = process.extract(termo, opcoes.keys(), limit=5, scorer=fuzz.WRatio)

    table = Table(title=f"Resultados para: '{termo}'", show_header=True, header_style="bold magenta")
    table.add_column("Código", style="dim", width=10)
    table.add_column("Nome Curto")
    table.add_column("Nome Completo")
    table.add_column("Score", justify="right")

    for nome_completo, score in resultados:
        code, name = opcoes[nome_completo]
        score_str = f"[bold green]{score}[/bold green]" if score == 100 else str(score)
        if score == 100:
            score_str += " [Exact]"
        
        table.add_row(str(code or "N/A"), name, nome_completo, score_str)

    console.print(table)
    log_event("INFO", f"Busca aproximada por nome realizada para o termo: '{termo}'")

def busca_codigo_tolerancia():
    codigo_alvo = Prompt.ask("[cyan]Digite o código numérico (ex: 341)[/cyan]").strip()
    if not codigo_alvo:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT code, name, fullName FROM bancos WHERE code IS NOT NULL")
    records = cursor.fetchall()
    conn.close()

    # Tenta achar exato primeiro
    exato = [r for r in records if str(r[0]) == codigo_alvo]
    if exato:
        console.print("[green]Correspondência exata encontrada diretamente pelo código![/green]")
        table = Table(show_header=True, header_style="bold green")
        table.add_column("Código")
        table.add_column("Nome")
        table.add_column("Nome Completo")
        for r in exato:
            table.add_row(str(r[0]), r[1], r[2])
        console.print(table)
        return

    # Se não achar exato, roda thefuzz nos códigos convertidos para String
    console.print("[yellow]Código exato não encontrado. Buscando por aproximação numérica (Typos)...[/yellow]")
    opcoes_codigo = {str(r[0]): (r[1], r[2]) for r in records}
    
    resultados = process.extract(codigo_alvo, opcoes_codigo.keys(), limit=5, scorer=fuzz.Ratio)

    table = Table(title=f"Códigos similares encontrados para '{codigo_alvo}'", show_header=True, header_style="bold yellow")
    table.add_column("Código Sugerido")
    table.add_column("Nome")
    table.add_column("Score")

    for cod, score in resultados:
        name, _ = opcoes_codigo[cod]
        table.add_row(cod, name, f"{score}%")
        
    console.print(table)
    log_event("INFO", f"Busca por código com tolerância para: '{codigo_alvo}'")

def agrupar_por_similaridade():
    console.print("[yellow]Processando agrupamentos por similaridade nominal (pode demorar alguns segundos)...[/yellow]")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, fullName FROM bancos")
    records = cursor.fetchall()
    conn.close()

    if len(records) < 2:
        console.print("[red]Registros insuficientes para agrupamento.[/red]")
        return

    nomes = list(set([r[0] for r in records if r[0]]))
    visitados = set()
    grupos = {}

    # Algoritmo de agrupamento guloso baseado em threshold
    for nome in nomes:
        if nome in visitados:
            continue
        
        visitados.add(nome)
        grupo_atual = [nome]
        
        for outro in nomes:
            if outro not in visitados:
                # Token Set Ratio resolve variações de ordem como "BANCO DO BRASIL" e "BRASIL BANCO"
                score = fuzz.token_set_ratio(nome, outro)
                if score >= 85:
                    grupo_atual.append(outro)
                    visitados.add(outro)
                    
        if len(grupo_atual) > 1:
            grupos[nome] = grupo_atual

    if not grupos:
        console.print("[green]Nenhum grupo de alta similaridade duplicada foi detectado.[/green]")
        return

    table = Table(title="Grupos de Bancos com Nomes Similares Encontrados", show_header=True, header_style="bold cyan")
    table.add_column("Nome Representativo", style="bold yellow")
    table.add_column("Variações Encontradas no Banco")

    for rep, integrantes in list(grupos.items())[:10]: # Limita a 10 exibições para não estourar a tela
        table.add_row(rep, ", ".join(integrantes))

    console.print(table)
    if len(grupos) > 10:
        console.print(f"[dim]* Exibindo 10 de {len(grupos)} grupos detectados.[/dim]")

def corrigir_nome_digitado():
    nome_digitado = Prompt.ask("[cyan]Digite o nome do banco com possíveis erros ortográficos[/cyan]").strip()
    if not nome_digitado:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT fullName FROM bancos")
    nomes_completos = [r[0] for r in cursor.fetchall()]
    conn.close()

    if not nomes_completos:
        console.print("[red]Base de dados vazia.[/red]")
        return

    sugestao, score = process.extractOne(nome_digitado, nomes_completos, scorer=fuzz.WRatio)

    console.print("\n[bold]Resultado da Verificação:[/bold]")
    console.print(f"Informado: [red]{nome_digitado}[/red]")
    console.print(f"Sugestão Correta: [green]{sugestao}[/green]")
    console.print(f"Nível de Confiança: [bold]{score}%[/bold]\n")

# ==========================================
# TAREFAS BÔNUS DO TRABALHO 8
# ==========================================

def bonus_autocorrecao_lote():
    console.print("[purple]=== BÔNUS: Autocorreção Simulada em Lote ===[/purple]")
    console.print("Insira nomes de bancos separados por vírgula (Ex: 'Baco do brasi, bradesco sa, itaú unibaco'):")
    entrada = Prompt.ask("[cyan]Lista de entrada[/cyan]")
    if not entrada:
        return

    itens = [i.strip() for i in entrada.split(",") if i.strip()]
    
    conn = get_db_connection()
    cursor = conn.cursor()
