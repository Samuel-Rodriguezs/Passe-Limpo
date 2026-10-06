#!/usr/bin/env python3
"""Git da copia sanitizada: identidade, commit verificado, resumo da pausa e push aprovado.

Subcomandos (sempre sobre o DESTINO; a origem so e lida para conferir que esta intacta):

  noreply   --login LOGIN
            descobre o e-mail noreply do GitHub (ID+login@users.noreply.github.com) pela API publica.

  preparar  --destino D --verificacao V --nome N --email E [--mensagem M --commit]
            sem --commit: confere tudo e mostra o que seria commitado.
            com --commit: so commita se todas as checagens passarem.

  remoto    --destino D --url URL
            liga o origin (so se ainda nao existir; nunca troca um origin diferente).

  resumo    --destino D --plano P --verificacao V
            refaz varredura (arquivos + historico), confere origem, identidade e repo,
            e imprime o bloco da PAUSA OBRIGATORIA.

  push      --destino D --plano P --verificacao V --remote-esperado URL --confirmacao APROVADO
            confere origin, repo privado, arvore limpa e tudo do resumo; faz git push origin main;
            confere commit remoto = local. Nao altera arquivo nem cria commit.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import varrer  # noqa: E402
from copiar import conferir_origem  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOREPLY_RE = re.compile(r"^(\d+)\+([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))@users\.noreply\.github\.com$")
GITIGNORE_OBRIGATORIO = [".env", ".env.*", "!.env.example", "*.pem", "*.key", "*.pfx", "*.p12",
                         "*.db", "*.sqlite*", "*.log", "credentials*", "secrets*", "cookies*"]
USO_TITULO = "## Uso"
USO_LINHAS = [
    "Este repositório é disponibilizado como portfólio e demonstração técnica.",
    "Não é concedida permissão para copiar, modificar, distribuir ou reutilizar este código sem autorização do autor.",
]
ENV_IDENTIDADE = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


# ---------------------------------------------------------------- utilitarios

def git(dest, *args, entrada=None, checar=True):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(dest), *args], input=entrada, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if checar and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} falhou: {r.stderr.strip()}")
    return r.stdout


def tem_commit(dest):
    return subprocess.run(["git", "-C", str(dest), "rev-parse", "--verify", "-q", "HEAD"],
                          capture_output=True).returncode == 0


def eh_repo(dest):
    return (Path(dest) / ".git").exists()


def email_valido(email):
    return bool(NOREPLY_RE.match(email or ""))


def termos_da_verificacao(verif):
    return varrer.montar_termos(verif.get("termos_arquivo"), verif.get("termos_extra", []), verif.get("internos_arquivo"),
                                verif.get("nomes_arquivo"), verif.get("permitidos_arquivo"), verif.get("cofre_arquivo"))


def normalizar_url(url):
    u = (url or "").strip()
    m = re.match(r"^git@github\.com:(.+)$", u)
    if m:
        u = "https://github.com/" + m.group(1)
    u = re.sub(r"\.git$", "", u.rstrip("/"))
    if re.match(r"^https?://", u):
        return u.lower()
    return str(Path(u).resolve()).lower() if u else ""


def github_repo(url):
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)$", normalizar_url(url))
    return (m.group(1), m.group(2)) if m else None


def _em_teste():
    """Ganchos de teste so valem dentro do pytest; no uso real sao ignorados."""
    return "PYTEST_CURRENT_TEST" in os.environ


def gh_comando():
    """Comando do GitHub CLI (lista) ou None se nao instalado."""
    if _em_teste() and os.environ.get("PUBLICAR_TESTE_GH"):
        return json.loads(os.environ["PUBLICAR_TESTE_GH"])
    if _em_teste() and os.environ.get("PUBLICAR_TESTE_SEM_GH") == "1":
        return None
    achado = shutil.which("gh")
    if achado:
        return [achado]
    for p in (Path(os.environ.get("ProgramFiles", "")) / "GitHub CLI" / "gh.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GitHub CLI" / "gh.exe"):
        if p.is_file():
            return [str(p)]
    return None


def visibilidade_gh(dono, repo):
    """Pergunta ao GitHub com o login do gh. PRIVADO / PUBLICO / NAO EXISTE / None (gh indisponivel)."""
    cmd = gh_comando()
    if not cmd:
        return None
    try:
        r = subprocess.run([*cmd, "api", f"repos/{dono}/{repo}", "--jq", ".private"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    saida = r.stdout.strip().lower()
    if r.returncode == 0 and saida in ("true", "false"):
        return "PRIVADO" if saida == "true" else "PUBLICO"
    if "not found" in (r.stderr + r.stdout).lower() or "404" in r.stderr:
        return "NAO EXISTE"
    return None  # sem login no gh, rede etc.


def visibilidade(url):
    """PRIVADO / PUBLICO / NAO EXISTE / NAO CONFIRMADO / NAO CONFIGURADO / DESCONHECIDO.

    So devolve PRIVADO com confirmacao autenticada (gh). Sem gh, um 404 da API publica
    pode ser repo privado OU inexistente: vira NAO CONFIRMADO, que bloqueia o push.
    """
    if not url:
        return "NAO CONFIGURADO"
    repo = github_repo(url)
    if repo is None:
        # Remoto local (pasta/bare) so e aceito dentro do pytest.
        if (_em_teste() and os.environ.get("PUBLICAR_TESTE_REMOTO_LOCAL") == "1"
                and not re.match(r"^\w+://", url) and Path(url).exists()):
            return "PRIVADO"
        return "DESCONHECIDO"
    via_gh = visibilidade_gh(*repo)
    if via_gh:
        return via_gh
    try:
        with urllib.request.urlopen(f"https://api.github.com/repos/{repo[0]}/{repo[1]}", timeout=30) as r:
            dados = json.load(r)
        return "PUBLICO" if not dados.get("private") else "PRIVADO"
    except urllib.error.HTTPError as e:
        return "NAO CONFIRMADO" if e.code == 404 else "DESCONHECIDO"
    except Exception:
        return "DESCONHECIDO"


def origin_url(dest):
    return git(dest, "remote", "get-url", "origin", checar=False).strip()


# ---------------------------------------------------------------- checagens

def checar_arquivos(dest, verif):
    """Arquivos no disco (fora .git) = exatamente os liberados na verificacao, com o mesmo hash."""
    problemas = []
    if not verif.get("limpo"):
        problemas.append("a verificacao (varrer.py --verificar) nao esta limpa")
    if normalizar_url(verif.get("origem", "")) != normalizar_url(str(dest)):
        problemas.append("a verificacao e de outra pasta")
    liberados = {it["caminho"]: it.get("sha256") for it in verif.get("arquivos", []) if it.get("liberado")}
    no_disco = {}
    for p in Path(dest).rglob("*"):
        if ".git" in p.relative_to(dest).parts or not p.is_file():
            continue
        no_disco[str(p.relative_to(dest))] = p
    for rel, p in sorted(no_disco.items()):
        if rel not in liberados:
            problemas.append(f"arquivo nao verificado: {rel}")
        elif liberados[rel] != varrer.sha256_arquivo(p):
            problemas.append(f"arquivo mudou depois da verificacao: {rel}")
    for rel in sorted(set(liberados) - set(no_disco)):
        problemas.append(f"arquivo verificado sumiu: {rel}")
    return problemas


def checar_portfolio(dest):
    problemas = []
    d = Path(dest)
    for p in d.iterdir():
        if p.is_file() and p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            problemas.append(f"arquivo de licenca presente ({p.name}): a skill nao adiciona LICENSE")
    readme = d / "README.md"
    if not readme.is_file():
        problemas.append("README.md ausente")
    else:
        txt = readme.read_text(encoding="utf-8", errors="replace")
        if "fictício" not in txt.lower() or "sanitizad" not in txt.lower():
            problemas.append("README sem o aviso de dados ficticios/sanitizados")
        titulos = [l.strip() for l in txt.splitlines() if l.startswith("## ")]
        if not titulos or titulos[-1] != USO_TITULO:
            problemas.append("README nao termina com a secao '## Uso'")
        for linha in USO_LINHAS:
            if linha not in txt:
                problemas.append(f"README sem o texto obrigatorio: {linha[:50]}...")
    gi = d / ".gitignore"
    if not gi.is_file():
        problemas.append(".gitignore ausente")
    else:
        linhas = {l.strip() for l in gi.read_text(encoding="utf-8", errors="replace").splitlines()}
        faltam = [p for p in GITIGNORE_OBRIGATORIO if p not in linhas]
        if faltam:
            problemas.append(f".gitignore sem: {', '.join(faltam)}")
    return problemas


def checar_identidade(dest, nome_esperado=None):
    problemas = []
    for v in ENV_IDENTIDADE:
        if os.environ.get(v):
            problemas.append(f"variavel de ambiente {v} definida: ela passaria por cima da identidade do repo")
    nome = git(dest, "config", "--local", "user.name", checar=False).strip()
    email = git(dest, "config", "--local", "user.email", checar=False).strip()
    if not email_valido(email):
        problemas.append("user.email local nao e um noreply do GitHub")
    if nome_esperado and nome != nome_esperado:
        problemas.append(f"user.name local diferente do esperado ({nome_esperado})")
    return nome, email, problemas


def varrer_historico(dest, termos_re):
    """Todos os commits: e-mails de autor/committer, mensagens e todo blob ja versionado."""
    alertas = []
    if not tem_commit(dest):
        return alertas
    log = git(dest, "log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x01")
    for bloco in log.split("\x01"):
        bloco = bloco.strip("\n")
        if not bloco:
            continue
        h, an, ae, cn, ce, msg = (bloco.split("\x00") + [""] * 6)[:6]
        for papel, email in (("autor", ae), ("committer", ce)):
            if not email_valido(email):
                alertas.append(f"commit {h[:7]}: e-mail de {papel} nao e noreply")
        for ach in varrer.varrer_texto(msg, termos_re):
            alertas.append(f"commit {h[:7]}: mensagem com {ach['categoria']}")
    objetos = git(dest, "rev-list", "--all", "--objects").splitlines()
    caminhos = {}
    for linha in objetos:
        partes = linha.split(" ", 1)
        if len(partes) == 2 and partes[1]:
            caminhos.setdefault(partes[0], set()).add(partes[1])
    if not caminhos:
        return alertas
    tipos = git(dest, "cat-file", "--batch-check=%(objectname) %(objecttype)", entrada="\n".join(caminhos) + "\n")
    blobs = [l.split()[0] for l in tipos.splitlines() if l.endswith(" blob")]
    for sha in blobs:
        for caminho in sorted(caminhos[sha]):
            status, motivo = varrer.classificar_nome(Path(caminho))
            if status == "EXCLUIR":
                alertas.append(f"historico contem arquivo bloqueado: {caminho} ({motivo})")
        r = subprocess.run(["git", "-C", str(dest), "cat-file", "-p", sha], capture_output=True)
        conteudo = r.stdout
        caminho = sorted(caminhos[sha])[0]
        if Path(caminho).suffix.lower() in varrer.EXT_IMAGEM_OCR:
            with tempfile.TemporaryDirectory(prefix="publicar-hist-") as tmp:
                arq = Path(tmp) / ("img" + Path(caminho).suffix.lower())
                arq.write_bytes(conteudo)
                item = varrer.analisar_imagem({"caminho": caminho, "motivos": [], "achados": []}, arq, termos_re)
            if "ocr_caracteres" not in item:
                alertas.append(f"historico: imagem {caminho} nao pode ser lida por OCR")
            for ach in item["achados"]:
                alertas.append(f"historico: imagem {caminho} contem {ach['categoria']} ({ach.get('fonte')})")
            continue
        if b"\x00" in conteudo[:8192]:
            continue
        for ach in varrer.varrer_texto(conteudo.decode("utf-8", errors="replace"), termos_re):
            alertas.append(f"historico: {sorted(caminhos[sha])[0]} contem {ach['categoria']} ({ach['trecho']})")
    return sorted(set(alertas))


def arvore_limpa(dest):
    return git(dest, "status", "--porcelain", "--untracked-files=all").strip() == ""


# ---------------------------------------------------------------- subcomandos

def cmd_noreply(a):
    login = a.login.strip()
    try:
        with urllib.request.urlopen(f"https://api.github.com/users/{login}", timeout=30) as r:
            dados = json.load(r)
    except Exception as e:
        print(f"ERRO: nao consegui consultar o GitHub ({e}). Pergunte o e-mail noreply ao usuario.")
        return 3
    if dados.get("type") != "User" or not dados.get("id") or dados.get("login", "").lower() != login.lower():
        print("ERRO: a conta retornada nao confere. Pergunte o e-mail noreply ao usuario.")
        return 3
    email = f"{dados['id']}+{dados['login']}@users.noreply.github.com"
    print(email)
    return 0


def cmd_gh(a):
    """Confere gh instalado, logado, e na conta esperada."""
    cmd = gh_comando()
    if not cmd:
        print("GH: NAO INSTALADO  (winget install --id GitHub.cli ; depois: gh auth login)")
        return 1
    try:
        r = subprocess.run([*cmd, "api", "user", "--jq", ".login"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"GH: ERRO ({e})")
        return 1
    login = r.stdout.strip()
    if r.returncode != 0 or not login:
        print("GH: SEM LOGIN  (rode: gh auth login)")
        return 1
    if a.login and login.lower() != a.login.lower():
        print(f"GH: CONTA ERRADA (logado como {login}, esperado {a.login})")
        return 1
    print(f"GH: OK ({login})")
    return 0


def cmd_preparar(a):
    dest = Path(a.destino).resolve()
    verif = json.loads(Path(a.verificacao).read_text(encoding="utf-8"))
    termos_re = termos_da_verificacao(verif)
    problemas = checar_arquivos(dest, verif) + checar_portfolio(dest)
    if not email_valido(a.email):
        problemas.append("o e-mail informado nao e um noreply do GitHub (Gmail/corporativo nao valem)")

    if eh_repo(dest) and tem_commit(dest):
        hist = varrer_historico(dest, termos_re)
        if hist:
            print("PARADO: o historico Git da copia ja tem dado sensivel:")
            for x in hist[:30]:
                print("  -", x)
            return 1
    if problemas:
        print("PARADO: checagens antes do commit falharam:")
        for x in problemas:
            print("  -", x)
        return 1

    if not eh_repo(dest):
        git(dest, "init", "-b", "main", "-q")
        print("git init -b main")
    git(dest, "config", "--local", "user.name", a.nome)
    git(dest, "config", "--local", "user.email", a.email)
    nome, email, prob_id = checar_identidade(dest, a.nome)
    if prob_id:
        print("PARADO: identidade Git:")
        for x in prob_id:
            print("  -", x)
        return 1
    print(f"identidade (so neste repo): {nome} <{email}>")

    liberados = {it["caminho"].replace("\\", "/") for it in verif["arquivos"] if it.get("liberado")}
    a_adicionar = [l[len("add '"):-1] for l in git(dest, "add", "--dry-run", "-A").splitlines() if l.startswith("add '")]
    fora = [f for f in a_adicionar if f not in liberados]
    if fora:
        print("PARADO: o git adicionaria arquivos nao verificados:", fora[:20])
        return 1
    ja_rastreados = set(git(dest, "ls-files").splitlines())
    ignorados = sorted(liberados - set(a_adicionar) - ja_rastreados)
    print(f"\nSERA COMMITADO ({len(a_adicionar)}):")
    for f in a_adicionar:
        print("  +", f)
    if ignorados:
        print(f"verificados mas ignorados pelo .gitignore (ficam fora): {ignorados}")
    if not a.commit:
        print("\n(sem --commit: nada foi commitado)")
        return 0
    if not a_adicionar:
        print("nada a commitar")
        return 1
    if not a.mensagem or "co-authored-by" in a.mensagem.lower():
        print("PARADO: informe --mensagem (sem linha Co-Authored-By).")
        return 1
    na_mensagem = varrer.varrer_texto(a.mensagem, termos_re)
    if na_mensagem:
        print("PARADO: a mensagem do commit tem termo sensivel:", sorted({x["categoria"] for x in na_mensagem}))
        return 1

    git(dest, "add", "--pathspec-from-file=-", entrada="\n".join(a_adicionar) + "\n")
    no_indice = set(git(dest, "diff", "--cached", "--name-only").splitlines())
    if not no_indice <= set(a_adicionar):
        git(dest, "reset", "-q")
        print("PARADO: o indice ficou diferente da lista verificada; nada commitado.")
        return 1
    git(dest, "commit", "-q", "-m", a.mensagem)

    hist = varrer_historico(dest, termos_re)
    autor = git(dest, "log", "-1", "--format=%an <%ae>").strip()
    print(f"\nCOMMIT: {git(dest, 'rev-parse', 'HEAD').strip()}")
    print(f"autor: {autor}")
    print(f"working tree: {'CLEAN' if arvore_limpa(dest) else 'SUJO'}")
    print(f"historico: {'limpo' if not hist else 'ALERTA'}")
    for x in hist[:30]:
        print("  -", x)
    return 0 if not hist and arvore_limpa(dest) else 1


def cmd_remoto(a):
    dest = Path(a.destino).resolve()
    atual = origin_url(dest)
    if atual:
        if normalizar_url(atual) != normalizar_url(a.url):
            print(f"PARADO: origin ja aponta para outro endereco ({atual}); nao troco.")
            return 1
        print(f"origin ja configurado: {atual}")
    else:
        git(dest, "remote", "add", "origin", a.url)
        print(f"origin configurado: {a.url}")
    print(f"REPO: {visibilidade(a.url)}")
    return 0


def avaliar(a):
    """Refaz todas as checagens. Devolve (dict, lista de alertas)."""
    dest = Path(a.destino).resolve()
    verif = json.loads(Path(a.verificacao).read_text(encoding="utf-8"))
    plano = json.loads(Path(a.plano).read_text(encoding="utf-8"))
    termos_re = termos_da_verificacao(verif)

    alterados = conferir_origem(Path(plano["origem"]), plano["arquivos"])
    arquivos, pastas = varrer.varrer_pasta(dest, termos_re, varrer.carregar_sinteticos(verif.get("sinteticos")))
    aceitos = set(verif.get("aceitos", []))
    pendentes = [it for it in arquivos if not varrer.liberado(it, aceitos)]
    pastas = [p for p in pastas if p["caminho"] != ".git"]
    hist = varrer_historico(dest, termos_re) if eh_repo(dest) else []
    nome, email, prob_id = checar_identidade(dest) if eh_repo(dest) else ("", "", ["sem repositorio Git"])
    commit = git(dest, "rev-parse", "HEAD").strip() if eh_repo(dest) and tem_commit(dest) else ""
    url = origin_url(dest) if eh_repo(dest) else ""
    vis = visibilidade(url)

    alertas = []
    alertas += [f"origem alterada: {x}" for x in alterados[:10]]
    alertas += [f"arquivo pendente: {it['caminho']} ({it['status']})" for it in pendentes[:20]]
    alertas += [f"pasta pendente: {p['caminho']}" for p in pastas]
    alertas += hist[:20]
    alertas += prob_id
    alertas += checar_portfolio(dest)
    if not commit:
        alertas.append("sem commit")
    elif not arvore_limpa(dest):
        alertas.append("working tree suja")
    if vis == "PUBLICO":
        alertas.append("o repositorio remoto esta PUBLICO")
    elif vis == "NAO CONFIRMADO":
        alertas.append("nao deu para confirmar que o repo e privado: instale e logue o gh (git_publicar.py gh)")
    elif vis == "NAO EXISTE":
        alertas.append("o repositorio remoto nao existe: crie-o como PRIVADO em github.com/new")
    sensiveis = sorted({ach["categoria"] for it in pendentes for ach in it.get("achados", [])})
    info = {
        "origem_intacta": not alterados,
        "limpo": not (pendentes or pastas or hist or prob_id or checar_portfolio(dest)) and bool(commit) and arvore_limpa(dest),
        "sensiveis": sensiveis + (["historico"] if hist else []),
        "nome": nome, "email": email, "commit": commit, "url": url, "vis": vis,
    }
    return info, alertas


def bloco_pausa(info):
    repo = {"PRIVADO": "PRIVADO", "NAO CONFIGURADO": "NAO CONFIGURADO"}.get(info["vis"], f"{info['vis']} (BLOQUEADO)")
    return "\n".join([
        f"ORIGEM INTACTA: {'SIM' if info['origem_intacta'] else 'NAO'}",
        f"VARREDURA FINAL: {'LIMPO' if info['limpo'] else 'ALERTA'}",
        f"DADOS SENSIVEIS: {'NENHUM' if not info['sensiveis'] else ', '.join(info['sensiveis'])}",
        f"GIT IDENTITY: {info['nome']} <{info['email']}>",
        f"COMMIT: {info['commit'] or '-'}",
        f"REPO: {repo}",
        "PUSH: AGUARDANDO APROVACAO",
    ])


def cmd_resumo(a):
    info, alertas = avaliar(a)
    for x in alertas:
        print("ALERTA:", x)
    if alertas:
        print()
    print(bloco_pausa(info))
    return 0 if not alertas else 1


def cmd_push(a):
    if a.confirmacao != "APROVADO":
        print("PARADO: o push so roda com a resposta exata APROVADO.")
        return 1
    dest = Path(a.destino).resolve()
    info, alertas = avaliar(a)
    if normalizar_url(info["url"]) != normalizar_url(a.remote_esperado):
        alertas.append("origin diferente do repositorio esperado")
    if info["vis"] != "PRIVADO":
        alertas.append(f"repositorio nao confirmado como privado ({info['vis']})")
    if git(dest, "branch", "--show-current").strip() != "main":
        alertas.append("branch atual nao e main")
    if alertas or not info["limpo"] or not info["origem_intacta"]:
        print("PARADO antes do push:")
        for x in alertas:
            print("  -", x)
        return 1
    antes = info["commit"]
    r = subprocess.run(["git", "-C", str(dest), "push", "origin", "main"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    ok = r.returncode == 0
    remoto = ""
    for linha in git(dest, "ls-remote", "origin", "refs/heads/main", checar=False).splitlines():
        remoto = linha.split()[0]
    local = git(dest, "rev-parse", "HEAD").strip()
    if local != antes:
        ok = False
    print("\n".join([
        f"PUSH: {'OK' if ok and remoto == local else 'FALHOU'}",
        "BRANCH: main",
        f"COMMIT LOCAL: {local}",
        f"COMMIT REMOTO: {remoto or '-'}",
        f"REPO PRIVADO: {'SIM' if visibilidade(info['url']) == 'PRIVADO' else 'NAO'}",
        f"WORKING TREE: {'CLEAN' if arvore_limpa(dest) else 'SUJO'}",
    ]))
    if not ok:
        print((r.stderr or r.stdout).strip()[-800:])
    return 0 if ok and remoto == local else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("noreply")
    p.add_argument("--login", required=True)
    p.set_defaults(f=cmd_noreply)

    p = sub.add_parser("gh")
    p.add_argument("--login")
    p.set_defaults(f=cmd_gh)

    p = sub.add_parser("preparar")
    p.add_argument("--destino", required=True)
    p.add_argument("--verificacao", required=True)
    p.add_argument("--nome", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--mensagem")
    p.add_argument("--commit", action="store_true")
    p.set_defaults(f=cmd_preparar)

    p = sub.add_parser("remoto")
    p.add_argument("--destino", required=True)
    p.add_argument("--url", required=True)
    p.set_defaults(f=cmd_remoto)

    for nome, func in (("resumo", cmd_resumo), ("push", cmd_push)):
        p = sub.add_parser(nome)
        p.add_argument("--destino", required=True)
        p.add_argument("--plano", required=True, help="plano do inventario da origem")
        p.add_argument("--verificacao", required=True)
        if nome == "push":
            p.add_argument("--remote-esperado", required=True)
            p.add_argument("--confirmacao", required=True)
        p.set_defaults(f=func)

    a = ap.parse_args()
    try:
        return a.f(a)
    except RuntimeError as e:
        print("ERRO:", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
