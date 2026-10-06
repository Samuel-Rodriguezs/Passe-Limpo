"""Testes da skill publicar-projeto com projetos 100% ficticios (pytest)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import git_publicar  # noqa: E402

NOREPLY = "12345+exemplo-usuario@users.noreply.github.com"
NOME = "Pessoa Exemplo"
README = """# Projeto exemplo

> Os dados deste repositório são fictícios ou foram sanitizados.

## Como executar

python app.py

## Uso

Este repositório é disponibilizado como portfólio e demonstração técnica.

Não é concedida permissão para copiar, modificar, distribuir ou reutilizar este código sem autorização do autor.
"""
GITIGNORE = "\n".join(git_publicar.GITIGNORE_OBRIGATORIO + ["__pycache__/"]) + "\n"
TOKEN_FALSO = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
# Dados falsos montados em tempo de execucao: o codigo-fonte dos testes nao contem o padrao inteiro
# (assim o proprio repositorio passa na varredura e na protecao de segredos do GitHub).
ASPAS = '"'
CPF_FALSO = "123.456." + "789-09"
GMAIL_FALSO = "pessoa" + "@gmail.com"
CHAVE_FALSA = "-----BEGIN " + "PRIVATE KEY-----"


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch):
    for v in git_publicar.ENV_IDENTIDADE:
        monkeypatch.delenv(v, raising=False)


def rodar(script, *args, env=None):
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, **(env or {})})
    return r.returncode, r.stdout + r.stderr


def escrever(base, arquivos):
    for rel, conteudo in arquivos.items():
        p = Path(base) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(conteudo, encoding="utf-8")


@pytest.fixture
def cenario(tmp_path):
    """Origem ficticia + inventario + destino 'ja sanitizado' com exemplos sinteticos."""
    origem, destino, trab = tmp_path / "origem", tmp_path / "destino", tmp_path / "trab"
    escrever(origem, {
        "app.py": "TOKEN = " + ASPAS + TOKEN_FALSO + ASPAS + "\nprint('ola')\n",
        ".env": "SENHA=nao-pode-sair\n",
        "chave.pem": CHAVE_FALSA + "\n",
        "dados.csv": "a,b\n",
        "util.py": "def soma(a, b):\n    return a + b\n",
    })
    assert rodar("varrer.py", "--origem", origem, "--saida", trab / "plano.json")[0] == 0
    escrever(destino, {
        "app.py": 'import os\nTOKEN = os.getenv("TOKEN")\nprint("ola")\n',
        "util.py": "def soma(a, b):\n    return a + b\n",
        "README.md": README,
        ".gitignore": GITIGNORE,
        ".env.example": "TOKEN=YOUR_TOKEN\n",
        "exemplos/documentos/caso1.txt": "Processo 0000000-00.0000.0.00.0000\nCliente: EXEMPLO_CLIENTE\n",
    })
    code, out = rodar("registrar_sinteticos.py", "--destino", destino, "--pasta", "exemplos",
                      "--plano", trab / "plano.json", "--saida", trab / "sinteticos.json")
    assert code == 0, out
    return {"origem": origem, "destino": destino, "trab": trab}


def verificar(c, *extra):
    return rodar("varrer.py", "--origem", c["destino"], "--saida", c["trab"] / "verif.json", "--verificar",
                 "--sinteticos", c["trab"] / "sinteticos.json", *extra)


def preparar(c, *extra):
    return rodar("git_publicar.py", "preparar", "--destino", c["destino"], "--verificacao", c["trab"] / "verif.json",
                 "--nome", NOME, "--email", NOREPLY, *extra)


def resumo(c):
    return rodar("git_publicar.py", "resumo", "--destino", c["destino"], "--plano", c["trab"] / "plano.json",
                 "--verificacao", c["trab"] / "verif.json")


# ------------------------------------------------------------ inventario e copia (travas antigas)

def test_inventario_classifica_e_nunca_copia_excluir(cenario):
    plano = json.loads((cenario["trab"] / "plano.json").read_text(encoding="utf-8"))
    st = {it["caminho"]: it["status"] for it in plano["arquivos"]}
    assert st[".env"] == st["chave.pem"] == st["dados.csv"] == "EXCLUIR"
    assert st["app.py"] == "SANITIZAR" and st["util.py"] == "COPIAR"
    code, out = rodar("copiar.py", "--plano", cenario["trab"] / "plano.json",
                      "--destino", cenario["trab"] / "copia", "--aprovar", ".env")
    assert code == 2 and not (cenario["trab"] / "copia").exists()


def test_copiar_recusa_destino_existente(cenario):
    code, out = rodar("copiar.py", "--plano", cenario["trab"] / "plano.json", "--destino", cenario["destino"])
    assert code == 2 and "ja existe" in out


# ------------------------------------------------------------ exemplos sinteticos

def test_sintetico_nao_vira_duvida_mas_e_varrido(cenario):
    code, out = verificar(cenario)
    assert code == 0, out
    escrever(cenario["destino"], {"exemplos/documentos/caso2.txt": "CPF " + CPF_FALSO + "\n"})
    rodar("registrar_sinteticos.py", "--destino", cenario["destino"], "--pasta", "exemplos",
          "--plano", cenario["trab"] / "plano.json", "--saida", cenario["trab"] / "sinteticos.json")
    code, out = verificar(cenario)
    assert code == 1 and "cpf" in out


def test_sintetico_alterado_depois_do_registro_volta_a_duvida(cenario):
    (cenario["destino"] / "exemplos/documentos/caso1.txt").write_text("outro conteudo\n", encoding="utf-8")
    code, out = verificar(cenario)
    assert code == 1 and "conteudo mudou" in out


def test_registrar_recusa_arquivo_que_veio_da_origem(cenario):
    escrever(cenario["destino"], {"exemplos/util_copiado.py": "def soma(a, b):\n    return a + b\n"})
    code, out = rodar("registrar_sinteticos.py", "--destino", cenario["destino"], "--pasta", "exemplos",
                      "--plano", cenario["trab"] / "plano.json", "--saida", cenario["trab"] / "s2.json")
    assert code == 1 and "RECUSADO" in out


def test_sinteticos_nao_valem_no_inventario_da_origem(cenario):
    code, out = rodar("varrer.py", "--origem", cenario["origem"], "--saida", cenario["trab"] / "x.json",
                      "--sinteticos", cenario["trab"] / "sinteticos.json")
    assert code == 2


# ------------------------------------------------------------ identidade

@pytest.mark.parametrize("email,ok", [
    (NOREPLY, True),
    (GMAIL_FALSO, False),
    ("fulano" + "@escritorio-exemplo.com.br", False),
    ("exemplo-usuario@users.noreply.github.com", False),  # sem ID: nao aceito
    ("12345+x" + "@users.noreply.github.com.evil.com", False),
])
def test_so_aceita_noreply_com_id(email, ok):
    assert git_publicar.email_valido(email) is ok


def test_preparar_recusa_gmail(cenario):
    verificar(cenario)
    code, out = rodar("git_publicar.py", "preparar", "--destino", cenario["destino"],
                      "--verificacao", cenario["trab"] / "verif.json", "--nome", NOME,
                      "--email", GMAIL_FALSO, "--mensagem", "x", "--commit")
    assert code == 1 and "noreply" in out
    assert not (cenario["destino"] / ".git").exists()


def test_variavel_de_ambiente_de_identidade_bloqueia(cenario):
    verificar(cenario)
    code, out = rodar("git_publicar.py", "preparar", "--destino", cenario["destino"],
                      "--verificacao", cenario["trab"] / "verif.json", "--nome", NOME, "--email", NOREPLY,
                      "--mensagem", "x", "--commit", env={"GIT_AUTHOR_EMAIL": GMAIL_FALSO})
    assert code == 1 and "GIT_AUTHOR_EMAIL" in out


# ------------------------------------------------------------ checagens antes do commit

def test_arquivo_criado_depois_da_verificacao_bloqueia(cenario):
    verificar(cenario)
    escrever(cenario["destino"], {"novo.py": "x = 1\n"})
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "nao verificado: novo.py" in out


def test_arquivo_alterado_depois_da_verificacao_bloqueia(cenario):
    verificar(cenario)
    (cenario["destino"] / "util.py").write_text("y = 2\n", encoding="utf-8")
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "mudou depois" in out


@pytest.mark.parametrize("arquivo,conteudo,erro", [
    ("README.md", "# Projeto\n\nDados fictícios e sanitizados.\n", "## Uso"),
    ("README.md", README.replace("fictícios ou foram sanitizados", "reais"), "aviso"),
    (".gitignore", "__pycache__/\n", ".gitignore sem"),
    ("LICENSE", "MIT\n", "licenca"),
])
def test_checagens_de_portfolio(cenario, arquivo, conteudo, erro):
    escrever(cenario["destino"], {arquivo: conteudo})
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and erro in out


def test_mensagem_com_co_authored_by_e_recusada(cenario):
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x\n\nCo-Authored-By: Alguem <a@b.c>", "--commit")
    assert code == 1


# ------------------------------------------------------------ historico git

def _commit_manual(dest, email, arquivos):
    escrever(dest, arquivos)
    subprocess.run(["git", "-C", str(dest), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(dest), "-c", f"user.name={NOME}", "-c", f"user.email={email}",
                    "commit", "-q", "-m", "antigo"], check=True)


def test_historico_com_segredo_antigo_para(cenario):
    _commit_manual(cenario["destino"], NOREPLY, {"segredo.py": "K = " + ASPAS + TOKEN_FALSO + ASPAS + "\n"})
    (cenario["destino"] / "segredo.py").unlink()  # some do disco, mas continua no historico
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "historico" in out.lower() and "github_token" in out


def test_historico_com_email_pessoal_para(cenario):
    _commit_manual(cenario["destino"], GMAIL_FALSO, {})
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "nao e noreply" in out


# ------------------------------------------------------------ fluxo completo + pausa + push

def test_fluxo_completo_com_pausa_e_push(cenario, tmp_path):
    assert verificar(cenario)[0] == 0
    code, out = preparar(cenario)  # so mostra
    assert code == 0 and "SERA COMMITADO" in out and "nada foi commitado" in out
    code, out = preparar(cenario, "--mensagem", "Versao inicial do projeto exemplo", "--commit")
    assert code == 0, out
    assert "working tree: CLEAN" in out and "historico: limpo" in out

    code, out = resumo(cenario)
    assert code == 0, out
    linhas = out.strip().splitlines()[-7:]
    assert linhas[0] == "ORIGEM INTACTA: SIM"
    assert linhas[1] == "VARREDURA FINAL: LIMPO"
    assert linhas[2] == "DADOS SENSIVEIS: NENHUM"
    assert linhas[3] == f"GIT IDENTITY: {NOME} <{NOREPLY}>"
    assert linhas[4].startswith("COMMIT: ") and len(linhas[4]) == len("COMMIT: ") + 40
    assert linhas[5] == "REPO: NAO CONFIGURADO"
    assert linhas[6] == "PUSH: AGUARDANDO APROVACAO"

    remoto = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remoto)], check=True)
    teste = {"PUBLICAR_TESTE_REMOTO_LOCAL": "1"}
    assert rodar("git_publicar.py", "remoto", "--destino", cenario["destino"], "--url", remoto, env=teste)[0] == 0
    base = ["push", "--destino", cenario["destino"], "--plano", cenario["trab"] / "plano.json",
            "--verificacao", cenario["trab"] / "verif.json"]

    code, out = rodar("git_publicar.py", *base, "--remote-esperado", remoto, "--confirmacao", "aprovado", env=teste)
    assert code == 1 and "APROVADO" in out
    code, out = rodar("git_publicar.py", *base, "--remote-esperado", tmp_path / "outro.git",
                      "--confirmacao", "APROVADO", env=teste)
    assert code == 1 and "origin diferente" in out
    code, out = rodar("git_publicar.py", *base, "--remote-esperado", remoto, "--confirmacao", "APROVADO")
    assert code == 1 and "privado" in out  # sem a flag de teste, remoto local nao e confirmado privado

    code, out = rodar("git_publicar.py", *base, "--remote-esperado", remoto, "--confirmacao", "APROVADO", env=teste)
    assert code == 0, out
    assert "PUSH: OK" in out and "WORKING TREE: CLEAN" in out and "REPO PRIVADO: SIM" in out
    local = [l for l in out.splitlines() if l.startswith("COMMIT LOCAL")][0].split(": ")[1]
    remoto_h = [l for l in out.splitlines() if l.startswith("COMMIT REMOTO")][0].split(": ")[1]
    assert local == remoto_h


def test_origem_alterada_aparece_no_resumo(cenario):
    verificar(cenario)
    assert preparar(cenario, "--mensagem", "x", "--commit")[0] == 0
    (cenario["origem"] / "util.py").write_text("mudou\n", encoding="utf-8")
    code, out = resumo(cenario)
    assert code == 1 and "ORIGEM INTACTA: NAO" in out


def test_remoto_nao_troca_origin_existente(cenario, tmp_path):
    verificar(cenario)
    preparar(cenario, "--mensagem", "x", "--commit")
    subprocess.run(["git", "-C", str(cenario["destino"]), "remote", "add", "origin", "https://github.com/a/b.git"], check=True)
    code, out = rodar("git_publicar.py", "remoto", "--destino", cenario["destino"], "--url", "https://github.com/c/d.git")
    assert code == 1 and "nao troco" in out


# ------------------------------------------------------------ visibilidade com gh (limitacao 1)

FAKE_GH = json.dumps([sys.executable, str(Path(__file__).resolve().parent / "fake_gh.py")])
URL_GH = "https://github.com/exemplo-usuario/projeto-exemplo.git"


@pytest.mark.parametrize("resp,esperado", [("true", "PRIVADO"), ("false", "PUBLICO"), ("404", "NAO EXISTE")])
def test_visibilidade_confirmada_pelo_gh(monkeypatch, resp, esperado):
    monkeypatch.setenv("PUBLICAR_TESTE_GH", FAKE_GH)
    monkeypatch.setenv("FAKE_GH_RESP", resp)
    assert git_publicar.visibilidade(URL_GH) == esperado


def _api_publica_404(*a, **k):
    raise git_publicar.urllib.error.HTTPError(URL_GH, 404, "Not Found", {}, None)


def test_sem_gh_404_nao_e_mais_privado(monkeypatch):
    monkeypatch.setenv("PUBLICAR_TESTE_SEM_GH", "1")
    monkeypatch.setattr(git_publicar.urllib.request, "urlopen", _api_publica_404)
    assert git_publicar.visibilidade(URL_GH) == "NAO CONFIRMADO"


def test_gh_sem_login_cai_na_api_publica(monkeypatch):
    monkeypatch.setenv("PUBLICAR_TESTE_GH", FAKE_GH)
    monkeypatch.setenv("FAKE_GH_RESP", "noauth")
    monkeypatch.setattr(git_publicar.urllib.request, "urlopen", _api_publica_404)
    assert git_publicar.visibilidade(URL_GH) == "NAO CONFIRMADO"


def test_ganchos_de_teste_ignorados_fora_do_pytest():
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(PUBLICAR_TESTE_GH=FAKE_GH, PUBLICAR_TESTE_REMOTO_LOCAL="1")
    codigo = ("import sys; sys.path.insert(0, r'%s'); import git_publicar as g; "
              "print(g._em_teste(), g.gh_comando() == __import__('json').loads(r'''%s'''))") % (SCRIPTS, FAKE_GH)
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, env=env)
    assert r.stdout.split() == ["False", "False"], r.stdout + r.stderr


@pytest.mark.parametrize("resp,login,code,texto", [
    ("login:exemplo-usuario", "exemplo-usuario", 0, "GH: OK"),
    ("login:outra-conta", "exemplo-usuario", 1, "CONTA ERRADA"),
    ("noauth", "exemplo-usuario", 1, "SEM LOGIN"),
])
def test_comando_gh(resp, login, code, texto):
    c, out = rodar("git_publicar.py", "gh", "--login", login, env={"PUBLICAR_TESTE_GH": FAKE_GH, "FAKE_GH_RESP": resp})
    assert c == code and texto in out


def test_push_bloqueado_se_repo_publico_ou_nao_confirmado(cenario, monkeypatch):
    verificar(cenario)
    preparar(cenario, "--mensagem", "x", "--commit")
    subprocess.run(["git", "-C", str(cenario["destino"]), "remote", "add", "origin", URL_GH], check=True)
    base = ["push", "--destino", cenario["destino"], "--plano", cenario["trab"] / "plano.json",
            "--verificacao", cenario["trab"] / "verif.json", "--remote-esperado", URL_GH, "--confirmacao", "APROVADO"]
    code, out = rodar("git_publicar.py", *base, env={"PUBLICAR_TESTE_GH": FAKE_GH, "FAKE_GH_RESP": "false"})
    assert code == 1 and "PUBLICO" in out
    code, out = rodar("git_publicar.py", *base, env={"PUBLICAR_TESTE_GH": FAKE_GH, "FAKE_GH_RESP": "404"})
    assert code == 1 and "nao existe" in out
    code, out = rodar(
        "git_publicar.py", "resumo", "--destino", cenario["destino"], "--plano", cenario["trab"] / "plano.json",
        "--verificacao", cenario["trab"] / "verif.json", env={"PUBLICAR_TESTE_GH": FAKE_GH, "FAKE_GH_RESP": "true"})
    assert code == 0 and "REPO: PRIVADO" in out


# ------------------------------------------------------------ vocabulario interno (limitacao 3a)

import varrer  # noqa: E402

TERMOS = varrer.montar_termos(str(SCRIPTS.parent / "termos-sensiveis.txt"), [], str(SCRIPTS.parent / "termos-internos.txt"))


def _cats(texto):
    return [a["categoria"] for a in varrer.varrer_texto(texto, TERMOS)]


def test_vocabulario_interno_no_codigo_bloqueia_a_verificacao(cenario):
    escrever(cenario["destino"], {"util.py": "# preenche a PLANILHA MESTRA\nx = 1\n"})
    code, out = verificar(cenario)
    assert code == 1 and "vocabulario_interno" in out


def test_vocabulario_interno_aparece_sem_mascara():
    achado = varrer.varrer_texto("campo PLANILHA MESTRA", TERMOS)
    assert achado and achado[0]["categoria"] == "vocabulario_interno" and achado[0]["trecho"] == "PLANILHA MESTRA"


@pytest.mark.parametrize("texto", ["construtora omega exemplo", "CONSTRUTORA ÔMEGA EXEMPLO", "campo status interno"])
def test_termos_sem_acento_e_sem_maiuscula(texto):
    assert _cats(texto), texto


@pytest.mark.parametrize("texto", ["fase processual", "acordo homologado", "valor homologado", "calculo_reclamante"])
def test_palavras_comuns_nao_sao_vocabulario_interno(texto):
    assert "vocabulario_interno" not in _cats(texto)


def test_lista_de_internos_customizada(cenario, tmp_path):
    lista = tmp_path / "internos.txt"
    lista.write_text("# comentario\nPlanilhaMestra\n", encoding="utf-8")
    escrever(cenario["destino"], {"util.py": "carrega a PlanilhaMestra\n"})
    code, out = verificar(cenario, "--internos", lista)
    assert code == 1 and "PlanilhaMestra" in out


def test_mensagem_de_commit_com_vocabulario_interno_e_recusada(cenario):
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "Ajusta PLANILHA MESTRA", "--commit")
    assert code == 1 and "mensagem do commit" in out
    assert not git_publicar.tem_commit(cenario["destino"])


def test_vocabulario_interno_no_historico_e_detectado(cenario):
    _commit_manual(cenario["destino"], NOREPLY, {"antigo.py": "# le a PLANILHA MESTRA\n"})
    (cenario["destino"] / "antigo.py").unlink()
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "vocabulario_interno" in out


# ------------------------------------------------------------ nomes protegidos por hash (limitacao 4)

import nomes_protegidos  # noqa: E402


@pytest.fixture
def lista_nomes(tmp_path):
    fonte = tmp_path / "nomes.txt"
    fonte.write_text("Fulana Exemplar da Silvaficticia\nBeltrano Inventado Souzaficticio\n", encoding="utf-8")
    empresas = tmp_path / "empresas.json"
    empresas.write_text(json.dumps({"records": [{"cellValuesByFieldId": {"fldX": "Companhia Ficticia Zetaexemplo Ltda"}},
                                                {"cellValuesByFieldId": {"fldX": "Origem Teste Sistemas Ltda"}}]}), encoding="utf-8")
    saida = tmp_path / "nomes-protegidos.json"
    code, out = rodar("nomes_protegidos.py", "montar", "--pessoas", f"{fonte}:-", "--empresas", f"{empresas}:fldX",
                      "--saida", saida)
    assert code == 0, out
    return saida


def test_lista_guarda_so_hash(lista_nomes):
    txt = lista_nomes.read_text(encoding="utf-8").upper()
    for palavra in ("FULANA", "SILVAFICTICIA", "ZETAEXEMPLO", "BELTRANO"):
        assert palavra not in txt


@pytest.mark.parametrize("texto", [
    "cliente Fulana Exemplar da Silvaficticia",
    "fulana silvaficticia",                     # primeiro + ultimo, minusculas
    "FULANA EXEMPLAR",                          # primeiro + segundo
    "réu: Companhia Ficticia Zetaexemplo LTDA.",
    "Companhia Ficticia Zetaexemplo",           # sem sufixo
])
def test_detecta_variantes(lista_nomes, texto):
    assert nomes_protegidos.NomesProtegidos(lista_nomes).procurar(texto)


@pytest.mark.parametrize("texto", ["Fulana", "a origem do teste", "sistema de teste", "Silvaficticia sozinho"])
def test_nao_detecta_palavra_solta(lista_nomes, texto):
    assert not nomes_protegidos.NomesProtegidos(lista_nomes).procurar(texto)


def test_permitidos_tiram_falso_positivo(lista_nomes, tmp_path):
    perm = tmp_path / "permitidos.txt"
    perm.write_text("Fulana Exemplar\n", encoding="utf-8")
    np_ = nomes_protegidos.NomesProtegidos(lista_nomes, perm)
    assert not np_.procurar("FULANA EXEMPLAR")
    assert np_.procurar("Fulana Silvaficticia")


def test_nome_protegido_bloqueia_verificacao_e_historico(cenario, lista_nomes):
    escrever(cenario["destino"], {"util.py": "# feito para Beltrano Souzaficticio\nx = 1\n"})
    code, out = verificar(cenario, "--nomes", lista_nomes)
    assert code == 1 and "nome_protegido" in out and "Beltrano" not in out
    (cenario["destino"] / "util.py").write_text("x = 1\n", encoding="utf-8")
    _commit_manual(cenario["destino"], NOREPLY, {"velho.py": "# Beltrano Souzaficticio\n"})
    (cenario["destino"] / "velho.py").unlink()
    verificar(cenario, "--nomes", lista_nomes)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "nome_protegido" in out


def test_sem_lista_de_nomes_avisa(cenario, tmp_path):
    code, out = verificar(cenario, "--nomes", tmp_path / "nao-existe.json")
    assert "AVISO: lista de nomes protegidos ausente" in out


# ------------------------------------------------------------ imagens: OCR e metadados (limitacao 5)

import struct  # noqa: E402
import zlib  # noqa: E402

import midia  # noqa: E402

fitz = pytest.importorskip("pymupdf")
precisa_ocr = pytest.mark.skipif(not midia.tesseract(), reason="Tesseract nao instalado")


def png_com_texto(caminho, linhas, meta=None):
    doc = fitz.open()
    pg = doc.new_page(width=520, height=40 + 28 * len(linhas))
    for i, l in enumerate(linhas):
        pg.insert_text((20, 40 + 28 * i), l, fontsize=16)
    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    pg.get_pixmap().save(str(caminho))
    if meta:
        d = Path(caminho).read_bytes()
        corpo = meta.encode("latin-1")
        chunk = struct.pack(">I", len(corpo)) + b"tEXt" + corpo + struct.pack(">I", zlib.crc32(b"tEXt" + corpo))
        Path(caminho).write_bytes(d[:33] + chunk + d[33:])


@precisa_ocr
def test_imagem_com_cpf_nao_pode_ser_aprovada(cenario):
    png_com_texto(cenario["destino"] / "docs/print.png", ["Cliente", "CPF " + CPF_FALSO])
    code, out = verificar(cenario, "--aceitar", r"docs\print.png")
    assert code == 1 and "cpf" in out


@precisa_ocr
def test_imagem_limpa_so_entra_aprovada(cenario):
    png_com_texto(cenario["destino"] / "docs/diagrama.png", ["Diagrama do fluxo", "fila -> extracao -> relatorio"])
    assert verificar(cenario)[0] == 1
    code, out = verificar(cenario, "--aceitar", r"docs\diagrama.png")
    assert code == 0, out


@precisa_ocr
def test_metadado_bloqueia_e_limpar_resolve(cenario):
    png_com_texto(cenario["destino"] / "docs/diagrama.png", ["Diagrama do fluxo"], meta="Author\x00" + "C:" + r"\Users\fulano\Desktop")
    code, out = verificar(cenario, "--aceitar", r"docs\diagrama.png")
    assert code == 1 and "metadados_imagem" in out
    code, out = rodar("midia.py", "limpar", "--destino", cenario["destino"], "--plano", cenario["trab"] / "plano.json")
    assert code == 0 and "imagens limpas: 1" in out
    assert midia.metadados(cenario["destino"] / "docs/diagrama.png")[0] == []
    assert verificar(cenario, "--aceitar", r"docs\diagrama.png")[0] == 0


def test_jpeg_exif_e_removido_sem_estragar_a_imagem(tmp_path):
    png_com_texto(tmp_path / "a.png", ["x"])
    jpg = fitz.Pixmap(str(tmp_path / "a.png")).tobytes("jpg")
    app1 = b"Exif\x00\x00GPS -23.5 Camera Exemplo"
    (tmp_path / "a.jpg").write_bytes(jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpg[2:])
    assert "jpeg-exif/xmp" in midia.metadados(tmp_path / "a.jpg")[0]
    (tmp_path / "b.jpg").write_bytes(midia.limpar_bytes((tmp_path / "a.jpg").read_bytes()))
    assert midia.metadados(tmp_path / "b.jpg")[0] == []
    assert fitz.Pixmap(str(tmp_path / "b.jpg")).width == fitz.Pixmap(str(tmp_path / "a.jpg")).width


def test_sem_ocr_imagem_nao_e_liberada(cenario):
    png_com_texto(cenario["destino"] / "docs/diagrama.png", ["Diagrama"])
    code, out = verificar(cenario, "--aceitar", r"docs\diagrama.png", "--sem-ocr")
    assert code == 1


def test_limpar_recusa_a_origem(cenario):
    code, out = rodar("midia.py", "limpar", "--destino", cenario["origem"], "--plano", cenario["trab"] / "plano.json")
    assert code == 2 and "sobrepoem" in out


@precisa_ocr
def test_imagem_com_dado_no_historico_para(cenario):
    png_com_texto(cenario["destino"] / "velho.png", ["CPF " + CPF_FALSO])
    _commit_manual(cenario["destino"], NOREPLY, {})
    (cenario["destino"] / "velho.png").unlink()
    verificar(cenario)
    code, out = preparar(cenario, "--mensagem", "x", "--commit")
    assert code == 1 and "imagem velho.png contem cpf" in out


def test_pdf_continua_excluido_sempre(cenario):
    doc = fitz.open()
    doc.new_page().insert_text((50, 50), "documento")
    doc.save(str(cenario["destino"] / "manual.pdf"))
    code, out = verificar(cenario, "--aceitar", "manual.pdf")
    assert code == 1 and "EXCLUIR" in out
