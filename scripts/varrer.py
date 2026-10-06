#!/usr/bin/env python3
"""Inventario e varredura de dados sensiveis de um projeto (somente leitura).

Uso:
  python varrer.py --origem "C:\\caminho\\projeto" --saida "<trab>\\plano.json" [--termos termos-sensiveis.txt] [--termo NOME ...]
  python varrer.py --origem "C:\\caminho\\portfolio\\projeto" --saida "<trab>\\verificacao.json" --verificar [...]

Nunca escreve dentro de --origem. Na saida, os valores encontrados aparecem sempre mascarados.
Status de cada arquivo:
  COPIAR     texto sem achados
  SANITIZAR  texto com achados: copia e substitui por placeholders NO DESTINO
  DUVIDA     fica fora por padrao; so entra com aprovacao explicita do usuario
  EXCLUIR    nunca entra na versao publica
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import socket
import sys
import time
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIRS_IGNORAR = {
    ".git": "historico git (pode conter segredos antigos)",
    ".hg": "controle de versao", ".svn": "controle de versao",
    "node_modules": "dependencias", ".venv": "ambiente virtual", "venv": "ambiente virtual",
    "env": "ambiente virtual", "site-packages": "dependencias",
    "__pycache__": "cache", ".pytest_cache": "cache", ".mypy_cache": "cache", ".ruff_cache": "cache",
    ".tox": "cache", ".cache": "cache", ".parcel-cache": "cache", ".ipynb_checkpoints": "cache",
    ".gradle": "cache", ".terraform": "estado de infraestrutura",
    "dist": "build", "build": "build", "target": "build", "bin": "build", "obj": "build",
    ".next": "build", ".nuxt": "build", "coverage": "relatorio de testes",
    ".idea": "config local da IDE", ".vscode": "config local da IDE", ".claude": "config/memoria local do Claude",
    "logs": "logs", "log": "logs", "tmp": "temporarios", "temp": "temporarios",
}

DIRS_DADOS = {
    "data", "dados", "input", "inputs", "output", "outputs", "saida", "saidas", "entrada", "entradas",
    "uploads", "upload", "downloads", "export", "exports", "backup", "backups", "planilhas", "pdfs",
    "raw", "private", "privado", "secrets", "credentials", "credenciais", "sessions", "sessoes",
    "cookies", "relatorios", "documentos", "processos", "clientes",
}

EXT_EXCLUIR = {}
for _exts, _motivo in [
    (".pem .key .pfx .p12 .crt .cer .der .csr .jks .keystore .p7b .p8 .ppk .asc .gpg .kdbx", "certificado/chave"),
    (".db .sqlite .sqlite3 .db3 .db-journal .db-wal .db-shm .mdb .accdb .dump .bak", "banco de dados/backup"),
    (".log .har", "log/captura de rede"),
    (".pdf .doc .docx .odt .rtf .msg .eml .pst .ost", "documento (pode ser real)"),
    (".xlsx .xls .xlsm .xlsb .ods .csv .tsv .parquet .feather .pkl .pickle .npy .h5", "planilha/dados"),
    (".zip .7z .rar .tar .gz .tgz .bz2 .xz", "arquivo compactado"),
    (".tmp .swp .swo", "temporario"),
    (".cookie .cookies .session .sess", "cookie/sessao"),
    (".exe .dll .so .dylib .pyc .pyo .class .jar .msi", "binario gerado"),
]:
    for _e in _exts.split():
        EXT_EXCLUIR[_e] = _motivo

EXT_DUVIDA = {}
for _exts, _motivo in [
    (".png .jpg .jpeg .gif .bmp .tif .tiff .webp .ico .heic", "imagem (pode ser print com dados)"),
    (".mp4 .mp3 .wav .mov .avi .m4a .ogg", "midia"),
    (".ttf .otf .woff .woff2", "fonte (binario)"),
    (".sql", "SQL pode ser dump com dados"),
    (".jsonl .ndjson", "provavel dado"),
]:
    for _e in _exts.split():
        EXT_DUVIDA[_e] = _motivo

NOMES_EXCLUIR = {
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".htpasswd", ".pgpass", "known_hosts",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_rsa.pub", "id_ed25519.pub",
    "credentials", "credentials.json", "token.json", "token.pickle", "cookies.txt", "cookies.json",
    "storage_state.json", "auth.json", "thumbs.db", "desktop.ini", ".ds_store",
}
ENV_MODELO = {".env.example", ".env.sample", ".env.template", ".env.dist", "env.example"}
RE_NOME_EXCLUIR = re.compile(r"(?i)^(client_secret.*\.json|service[-_]?account.*\.json|.*\.secrets?(\..*)?|secrets?\..*)$")
RE_NOME_DUVIDA = re.compile(
    r"(?i)(secret|segredo|credential|credencia|token|cookie|session|sess[aã]o|senha|passw|apikey|api[_-]key"
    r"|private|privad|confidencia|cliente|backup|dump)"
)

EXT_CODIGO = set(
    ".py .js .ts .tsx .jsx .mjs .cjs .java .cs .go .rb .php .ps1 .psm1 .sh .bash .bat .cmd .vue .svelte "
    ".r .kt .swift .c .cpp .h .hpp .rs .lua .gs .css .scss .less .dart .scala".split()
)
EXT_DADO_TEXTO = set(".txt .json .xml .yaml .yml .html .htm".split())

EXT_IMAGEM_OCR = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
MAX_TEXTO = 2 * 1024 * 1024
MAX_DADO_TEXTO = 150 * 1024

CATS_PESSOAIS = {"cpf", "cnpj", "processo_cnj", "processo_cnj_sem_mascara", "email", "telefone", "cep",
                 "endereco", "parte_processual", "oab"}

PADROES = [
    ("chave_privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("airtable_token", re.compile(r"\bpat[A-Za-z0-9]{14}\.[a-f0-9]{64}\b")),
    ("airtable_api_key_antiga", re.compile(r"\bkey[A-Za-z0-9]{14}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("google_oauth", re.compile(r"\b(?:\d+-[a-z0-9]+\.apps\.googleusercontent\.com|ya29\.[0-9A-Za-z_\-]{20,}|GOCSPX-[0-9A-Za-z_\-]{20,})")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("segredo_atribuido", re.compile(
        r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_\-]*(?:api[_-]?key|apikey|secret|token|password|passwd|pwd|senha"
        r"|private[_-]?key|credential)[A-Za-z0-9_\-]*[\"']?\s*[:=]\s*[\"']([^\"'\s]{6,})[\"']")),
    ("url_com_credencial", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@\"']+:[^/\s@\"']+@[^\s\"']+")),
    ("token_em_url", re.compile(r"(?i)[?&](?:key|api_?key|token|access_token|auth|sig|signature|secret)=[^&\s\"']{8,}")),
    ("airtable_id", re.compile(r"\b(?:app|tbl|fld|viw|rec|shr|wsp|usr|sel)[A-Za-z0-9]{14}\b")),
    ("google_doc_url", re.compile(r"(?i)https?://(?:docs|drive|script)\.google\.com/[^\s\"'<>)]+")),
    ("url_privada", re.compile(
        r"(?i)https?://[^\s\"'<>)]*(?:n8n|webhook|ngrok|trycloudflare|loca\.lt|\.local\b|\.internal\b|\.lan\b|\.corp\b"
        r"|sharepoint\.com|onedrive|1drv\.ms|dropbox\.com|hooks\.slack\.com|discord(?:app)?\.com/api/webhooks"
        r"|airtable\.com/(?:app|shr)|pje\.|\.jus\.br)[^\s\"'<>)]*")),
    ("ip_privado", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
    ("caminho_interno", re.compile(r"(?<![A-Za-z])[A-Za-z]:(?:\\\\|\\|/)[^\s\"'<>|*?]+")),
    ("caminho_interno", re.compile(r"(?<![\w.])/(?:Users|home)/[A-Za-z0-9._\-]+")),
    ("caminho_interno", re.compile(r"\\\\[A-Za-z0-9._\-]{2,}\\[A-Za-z0-9$._\-]+")),
    ("cpf", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")),
    ("processo_cnj", re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")),
    ("processo_cnj_sem_mascara", re.compile(r"(?<!\d)\d{20}(?!\d)")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("telefone", re.compile(r"(?:\+55\s?)?\(\d{2}\)\s?9?\d{4}-?\d{4}\b|\+55\s?\d{2}\s?9?\d{4}-?\d{4}\b")),
    ("cep", re.compile(r"\b\d{5}-\d{3}\b")),
    ("endereco", re.compile(
        r"\b(?:Rua|R\.|Av\.|Avenida|Alameda|Al\.|Travessa|Rodovia|Estrada|Pra[çc]a)\s+[A-ZÀ-Ú][\wÀ-ú]+[^\n]{0,40}?,\s*\d+")),
    ("parte_processual", re.compile(
        r"(?i:" r"\b(?:reclamante|reclamad[oa]|exequente|executad[oa]|autor[a]?|r[ée]u|cliente|advogad[oa]|perit[oa]"
        r"|testemunha|preposto)\b)\s*[:=\-]\s*[\"']?[A-ZÀ-Ú][a-zà-ú]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][a-zà-ú]+)+")),
    ("oab", re.compile(r"(?i)\bOAB\s*/?\s*[A-Z]{2}\s*(?:n[ºo°.]*\s*)?\d[\d.]{2,}")),
]

DOMINIOS_EXEMPLO = ("example.com", "example.org", "example.net", "exemplo.com", "exemplo.com.br", "email.com",
                    "users.noreply.github.com", "localhost", "test.com", "teste.com")
RE_PLACEHOLDER = re.compile(
    r"(?i)(your_|seu_|sua_|exemplo|example|placeholder|changeme|change_me|xxx|\*\*\*|<[^>]*>|\$\{|\{\{|os\.environ"
    r"|getenv|process\.env|env\(|dummy|fake|ficticio|fictício|redacted)")


ROTULO_OCULTO = {
    "termo_sensivel": "[termo sensivel]",
    "vocabulario_interno": "[termo interno]",
    "parte_processual": "[nome de parte]",
    "endereco": "[endereco]",
    "email": "[e-mail]",
    "nome_protegido": "[nome protegido]",
}


def mascarar(cat, valor):
    """Nunca mostra pedaco de nome, termo, e-mail ou endereco; segredos so com inicio e fim."""
    v = valor.strip()
    if cat in ROTULO_OCULTO:
        return ROTULO_OCULTO[cat]
    if cat == "caminho_interno":
        return f"[caminho local, {len(v)} caracteres]"
    if len(v) <= 8:
        return v[:1] + "*" * (len(v) - 1)
    return v[:4] + "*" * min(len(v) - 6, 20) + v[-2:]


def eh_placeholder(cat, valor):
    if RE_PLACEHOLDER.search(valor):
        return True
    digitos = re.sub(r"\D", "", valor)
    if cat in ("cpf", "cnpj", "processo_cnj", "processo_cnj_sem_mascara", "telefone", "cep"):
        if digitos and set(digitos) <= {"0"}:
            return True
    if cat == "email":
        dominio = valor.rsplit("@", 1)[-1].lower()
        if dominio.endswith(DOMINIOS_EXEMPLO):
            return True
        if re.search(r"(?i)\.(png|jpe?g|gif|svg|webp)$", valor):  # ex.: icone@2x.png
            return True
    if cat == "airtable_id":
        resto = valor[3:]
        if not (re.search(r"\d", resto) and re.search(r"[A-Z]", resto) and re.search(r"[a-z]", resto)):
            return True
    if cat == "airtable_api_key_antiga":
        resto = valor[3:]
        if not (re.search(r"\d", resto) and re.search(r"[A-Z]", resto)):
            return True
    if cat == "openai_key" and not re.search(r"\d", valor):
        return True
    if cat == "caminho_interno":
        if re.fullmatch(r"(?i)[a-z]:[\\/]+(caminho|path|exemplo|example)([\\/].*)?", valor):
            return True
    return False


def carregar_termos(arquivo, extras):
    termos = set()
    if arquivo and Path(arquivo).is_file():
        for linha in Path(arquivo).read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                termos.add(linha)
    for t in extras or []:
        if t.strip():
            termos.add(t.strip())
    for auto in (getpass.getuser(), socket.gethostname(), os.environ.get("USERNAME", ""),
                 os.environ.get("USERDOMAIN", ""), os.environ.get("COMPUTERNAME", "")):
        if auto and len(auto) >= 3:
            termos.add(auto)
    return sorted(termos, key=len, reverse=True)


def classificar_nome(rel):
    nome = rel.name.lower()
    ext = rel.suffix.lower()
    partes = [p.lower() for p in rel.parts[:-1]]
    if nome in ENV_MODELO:
        return None, None
    if nome == ".env" or nome.startswith(".env.") or nome.endswith(".env"):
        return "EXCLUIR", "arquivo .env"
    if nome in NOMES_EXCLUIR or RE_NOME_EXCLUIR.match(nome):
        return "EXCLUIR", "credencial/segredo pelo nome"
    if nome.startswith("~$") or nome.endswith("~"):
        return "EXCLUIR", "temporario"
    if ext in EXT_EXCLUIR:
        return "EXCLUIR", EXT_EXCLUIR[ext]
    if ext in EXT_DUVIDA:
        return "DUVIDA", EXT_DUVIDA[ext]
    pasta_dados = next((p for p in partes if p in DIRS_DADOS), None)
    if pasta_dados:
        return "DUVIDA", f"dentro de pasta de dados ({pasta_dados})"
    if RE_NOME_DUVIDA.search(rel.stem):
        return "DUVIDA", "nome sugere segredo/sessao/cliente"
    return None, None


def varrer_texto(texto, termos_re):
    achados = []
    for n, linha in enumerate(texto.splitlines(), 1):
        if len(linha) > 5000:
            linha = linha[:5000]
        for cat, rx in PADROES:
            for m in rx.finditer(linha):
                valor = m.group(1) if (cat == "segredo_atribuido" and m.groups()) else m.group(0)
                if eh_placeholder(cat, m.group(0)):
                    continue
                achados.append({"linha": n, "categoria": cat, "trecho": mascarar(cat, valor)})
        linha_sem_acento = sem_acento(linha)
        for item in termos_re:
            if hasattr(item, "procurar"):  # nomes protegidos (hash)
                for n_palavras in item.procurar(linha):
                    achados.append({"linha": n, "categoria": "nome_protegido",
                                    "trecho": f"[{n_palavras} palavra(s) da lista protegida]"})
                continue
            termo, rx, cat = item if len(item) == 3 else (*item, "termo_sensivel")
            if rx.search(linha_sem_acento):
                # vocabulario interno nao e dado pessoal: mostra o termo para facilitar a troca
                trecho = mascarar(cat, termo)
                achados.append({"linha": n, "categoria": cat, "trecho": trecho})
    return achados


def sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def carregar_lista(arquivo):
    """Termos de um arquivo (um por linha, '#' comenta), sem os automaticos."""
    if not arquivo or not Path(arquivo).is_file():
        return []
    return [l.strip() for l in Path(arquivo).read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


PASTA_SKILL = Path(__file__).resolve().parent.parent


def dados_dir():
    """Pasta das listas e do cofre. Nos testes (pytest) pode apontar para dados ficticios."""
    if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("PUBLICAR_SKILL_DADOS"):
        return Path(os.environ["PUBLICAR_SKILL_DADOS"])
    return PASTA_SKILL


def padrao(nome):
    return dados_dir() / nome


NOMES_PADRAO = PASTA_SKILL / "nomes-protegidos.json"
PERMITIDOS_PADRAO = PASTA_SKILL / "nomes-permitidos.txt"


def montar_termos(arquivo_sensiveis, extras, arquivo_internos, arquivo_nomes=None, arquivo_permitidos=None,
                  arquivo_cofre=None):
    """Lista unica: (termo, regex, categoria) dos sensiveis + extras + automaticos e do vocabulario
    interno (arquivos .txt + cofre cifrado), mais o detector de nomes protegidos (hash)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cofre import abrir_se_existir
    cofre = abrir_se_existir(arquivo_cofre)
    do_cofre = (lambda l: cofre.listar(l)) if cofre else (lambda l: [])
    sens = sorted(set(carregar_termos(arquivo_sensiveis, extras)) | set(do_cofre("sensiveis")), key=len, reverse=True)
    inter = sorted(set(carregar_lista(arquivo_internos)) | set(do_cofre("internos")), key=len, reverse=True)
    lista = termos_regex(sens) + termos_regex(inter, "vocabulario_interno")
    if arquivo_nomes and Path(arquivo_nomes).is_file():
        from nomes_protegidos import NomesProtegidos
        lista.append(NomesProtegidos(arquivo_nomes, arquivo_permitidos, cofre))
    if cofre:
        cofre.fechar()
    return lista


def termos_regex(termos, categoria="termo_sensivel"):
    """Casa sem diferenciar maiusculas nem acentos ('Joao' pega 'João' e vice-versa)."""
    return [(t, re.compile(r"(?<![A-Za-z0-9])" + re.escape(sem_acento(t)) + r"(?![A-Za-z0-9])", re.IGNORECASE),
             categoria) for t in termos]


def sha256_arquivo(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def carregar_sinteticos(caminho):
    """Manifesto {caminho_relativo: sha256} gravado por registrar_sinteticos.py."""
    if not caminho:
        return {}
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    return {str(Path(k)): v for k, v in dados.get("arquivos", {}).items()}


def classificar_conteudo(item, rel, bruto, status, motivo, termos_re, tamanho):
    """Aplica as regras de conteudo a um arquivo ja lido. Altera e devolve item."""
    if b"\x00" in bruto[:8192]:
        item["status"] = status or "DUVIDA"
        if not motivo:
            item["motivos"].append("binario nao verificavel")
        return item
    if len(bruto) > MAX_TEXTO:
        item["status"] = "DUVIDA"
        item["motivos"].append("texto grande (>2 MB): provavel dado")
        return item
    texto = bruto.decode("utf-8", errors="replace")
    achados = varrer_texto(texto, termos_re)
    item["total_achados"] = len(achados)
    item["achados"] = achados[:40]
    ext = rel.suffix.lower()
    if status == "DUVIDA":
        item["status"] = "DUVIDA"
    elif ext in EXT_DADO_TEXTO and tamanho > MAX_DADO_TEXTO:
        item["status"] = "DUVIDA"
        item["motivos"].append("arquivo de dados em texto grande (>150 KB)")
    elif ext in EXT_DADO_TEXTO and sum(1 for x in achados if x["categoria"] in CATS_PESSOAIS) >= 5:
        item["status"] = "DUVIDA"
        item["motivos"].append("muitos dados pessoais: provavel dado real")
    elif achados or ext == ".ipynb":
        item["status"] = "SANITIZAR"
        if ext == ".ipynb":
            item["motivos"].append("notebook: limpar saidas e metadados")
    else:
        item["status"] = "COPIAR"
    return item


def analisar_imagem(item, p, termos_re):
    """OCR do texto visivel + metadados (EXIF/XMP/texto de PNG). A imagem continua DUVIDA."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from midia import metadados, ocr
    achados = []
    tipos, texto_meta = metadados(p)
    if tipos and not (tipos == ["formato-nao-verificavel"] and p.suffix.lower() == ".bmp"):
        achados.append({"linha": 0, "categoria": "metadados_imagem", "trecho": ", ".join(tipos), "fonte": "metadados"})
        for ach in varrer_texto(texto_meta, termos_re):
            achados.append({**ach, "fonte": "metadados"})
    texto = ocr(p)
    if texto is None:
        item["motivos"].append("OCR indisponivel: imagem nao lida (nao pode ser aprovada)")
    else:
        item["ocr_caracteres"] = len(texto.strip())
        for ach in varrer_texto(texto, termos_re):
            achados.append({**ach, "fonte": "ocr"})
        item["motivos"].append(f"OCR: {item['ocr_caracteres']} caracteres lidos")
    item["achados"] = (item.get("achados") or []) + achados[:40]
    item["total_achados"] = (item.get("total_achados") or 0) + len(achados)
    return item


def varrer_pasta(origem, termos_re, sinteticos=None, ocr=True):
    """Percorre a pasta (somente leitura) e classifica cada arquivo."""
    sinteticos = sinteticos or {}
    arquivos, dirs_ignorados = [], []
    for raiz, dirs, nomes in os.walk(origem, followlinks=False):
        raiz_p = Path(raiz)
        manter = []
        for d in dirs:
            motivo = DIRS_IGNORAR.get(d.lower())
            if motivo:
                dirs_ignorados.append({"caminho": str((raiz_p / d).relative_to(origem)), "motivo": motivo})
            else:
                manter.append(d)
        dirs[:] = manter
        for nome in nomes:
            p = raiz_p / nome
            rel = p.relative_to(origem)
            try:
                st = p.lstat()
            except OSError as e:
                arquivos.append({"caminho": str(rel), "status": "DUVIDA", "motivos": [f"nao foi possivel ler: {e}"]})
                continue
            item = {"caminho": str(rel), "tamanho": st.st_size, "mtime_ns": st.st_mtime_ns, "motivos": [], "achados": []}
            if p.is_symlink():
                item.update(status="EXCLUIR", motivos=["link simbolico"])
                arquivos.append(item)
                continue
            status, motivo = classificar_nome(rel)
            if status == "EXCLUIR":
                item["status"] = status
                item["motivos"].append(motivo)
                arquivos.append(item)
                continue
            try:
                item["sha256"] = sha256_arquivo(p)
                with open(p, "rb") as f:
                    bruto = f.read(MAX_TEXTO + 1)
            except OSError as e:
                item.update(status="DUVIDA", motivos=[f"nao foi possivel ler: {e}"])
                arquivos.append(item)
                continue
            # Exemplo sintetico gerado pela skill: dispensa so as regras de pasta/nome.
            # Extensao e conteudo continuam valendo; hash diferente = nao e o arquivo gerado.
            if sinteticos.get(str(rel)) == item["sha256"]:
                item["sintetico"] = True
                if status == "DUVIDA" and rel.suffix.lower() not in EXT_DUVIDA:
                    status, motivo = None, None
            elif str(rel) in sinteticos:
                item["motivos"].append("listado como sintetico, mas o conteudo mudou depois do registro")
                status = "DUVIDA"
            if motivo:
                item["motivos"].append(motivo)
            item = classificar_conteudo(item, rel, bruto, status, motivo, termos_re, st.st_size)
            if ocr and item["status"] == "DUVIDA" and rel.suffix.lower() in EXT_IMAGEM_OCR:
                item = analisar_imagem(item, p, termos_re)
            arquivos.append(item)
    return sorted(arquivos, key=lambda x: x["caminho"].lower()), dirs_ignorados


def liberado(item, aceitos=()):
    """Arquivo que pode ficar na versao publica (modo verificacao)."""
    st = item.get("status")
    if st == "COPIAR":
        return True
    if st == "SANITIZAR":
        return not item.get("total_achados")  # notebook sem achados
    if st == "DUVIDA":
        if Path(item["caminho"]).suffix.lower() in EXT_IMAGEM_OCR and "ocr_caracteres" not in item:
            return False  # imagem sem leitura de OCR: nao da para saber o que ela mostra
        return item["caminho"] in aceitos and not item.get("total_achados") and "sha256" in item
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origem", required=True)
    ap.add_argument("--saida", required=True)
    ap.add_argument("--termos", default=str(padrao("termos-sensiveis.txt")))
    ap.add_argument("--cofre", default=str(padrao("cofre.db")), help="cofre cifrado com as listas (cofre.py)")
    ap.add_argument("--termo", action="append", default=[], help="termo extra desta sessao (repetivel)")
    ap.add_argument("--nomes", default=str(padrao("nomes-protegidos.json")), help="lista de nomes protegidos (hash)")
    ap.add_argument("--permitidos", default=str(padrao("nomes-permitidos.txt")), help="falsos positivos da lista de nomes")
    ap.add_argument("--internos", default=str(padrao("termos-internos.txt")),
                    help="vocabulario interno da empresa (bloqueado como termo sensivel)")
    ap.add_argument("--verificar", action="store_true", help="modo verificacao final do destino")
    ap.add_argument("--aceitar", action="append", default=[],
                    help="no modo --verificar: arquivo DUVIDA ja aprovado pelo usuario (caminho relativo, repetivel)")
    ap.add_argument("--sem-ocr", action="store_true", help="nao ler imagens (elas ficam sem poder ser aprovadas)")
    ap.add_argument("--sinteticos", help="manifesto de exemplos gerados pela skill (registrar_sinteticos.py)")
    a = ap.parse_args()

    origem = Path(a.origem.strip('"')).resolve()
    saida = Path(a.saida).resolve()
    if not origem.is_dir():
        print(f"ERRO: origem nao existe ou nao e pasta: {origem}")
        return 2
    if saida == origem or origem in saida.parents:
        print("ERRO: --saida nao pode ficar dentro da origem (origem e somente leitura).")
        return 2
    if a.sinteticos and not a.verificar:
        print("ERRO: --sinteticos so vale no modo --verificar (no inventario da origem nada e sintetico).")
        return 2

    termos = montar_termos(a.termos, a.termo, a.internos, a.nomes, a.permitidos, a.cofre)
    automaticos = set(carregar_termos(None, []))
    if not any(isinstance(t, tuple) and t[2] == "termo_sensivel" and t[0] not in automaticos for t in termos):
        print("AVISO: nenhum termo sensivel carregado (lista vazia e sem cofre); so os padroes automaticos valem.")
    if not Path(a.nomes).is_file():
        print(f"AVISO: lista de nomes protegidos ausente ({a.nomes}); nomes de clientes nao serao checados.")
    if not a.sem_ocr:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from midia import tesseract
        if not tesseract():
            print("AVISO: Tesseract nao encontrado; imagens nao serao lidas e nao poderao ser aprovadas.")
    arquivos, dirs_ignorados = varrer_pasta(origem, termos, carregar_sinteticos(a.sinteticos), ocr=not a.sem_ocr)

    aceitos = {str(Path(x)) for x in a.aceitar}
    pastas = [d for d in dirs_ignorados if d["caminho"] != ".git"]  # .git do repo novo e esperado
    if a.verificar:
        for it in arquivos:
            it["liberado"] = liberado(it, aceitos)
    problemas = [it for it in arquivos if a.verificar and not it["liberado"]]
    plano = {
        "origem": str(origem),
        "modo": "verificacao" if a.verificar else "inventario",
        "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "termos_verificados": len(termos),
        "termos_extra": a.termo,
        "termos_arquivo": str(Path(a.termos).resolve()),
        "internos_arquivo": str(Path(a.internos).resolve()),
        "nomes_arquivo": str(Path(a.nomes).resolve()) if Path(a.nomes).is_file() else None,
        "permitidos_arquivo": str(Path(a.permitidos).resolve()),
        "cofre_arquivo": str(Path(a.cofre).resolve()) if Path(a.cofre).is_file() else None,
        "aceitos": sorted(aceitos),
        "sinteticos": str(Path(a.sinteticos).resolve()) if a.sinteticos else None,
        "arquivos": arquivos,
        "dirs_ignorados": dirs_ignorados,
    }
    if a.verificar:
        plano["limpo"] = not problemas and not pastas
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(plano, ensure_ascii=False, indent=1), encoding="utf-8")

    cont, cats = {}, {}
    for it in arquivos:
        cont[it["status"]] = cont.get(it["status"], 0) + 1
        for ach in it.get("achados", []):
            cats[ach["categoria"]] = cats.get(ach["categoria"], 0) + 1
    sint = sum(1 for it in arquivos if it.get("sintetico"))
    print(f"{plano['modo'].upper()} de {origem}")
    print(f"plano: {saida}")
    print("arquivos por status:", json.dumps(cont, ensure_ascii=False) + (f" | sinteticos reconhecidos: {sint}" if sint else ""))
    print("pastas ignoradas:", len(dirs_ignorados), [d["caminho"] for d in dirs_ignorados][:30])
    print("achados por categoria:", json.dumps(dict(sorted(cats.items(), key=lambda x: -x[1])), ensure_ascii=False))
    for st in ("EXCLUIR", "DUVIDA", "SANITIZAR"):
        lista = [it for it in arquivos if it["status"] == st and not (a.verificar and it.get("liberado"))]
        if not lista:
            continue
        print(f"\n[{st}] {len(lista)}")
        for it in lista[:80]:
            extra = ""
            if it.get("total_achados"):
                c = {}
                for ach in it["achados"]:
                    c[ach["categoria"]] = c.get(ach["categoria"], 0) + 1
                extra = f" ({it['total_achados']} achados: {', '.join(f'{k} {v}' for k, v in c.items())})"
                linhas = sorted({ach["linha"] for ach in it["achados"]})[:15]
                if linhas:
                    extra += f" [linhas: {', '.join(map(str, linhas))}]"
            print(f"  - {it['caminho']}{extra}" + (f" :: {'; '.join(it['motivos'])}" if it["motivos"] else ""))
        if len(lista) > 80:
            print(f"  ... +{len(lista) - 80} (ver plano)")

    if a.verificar:
        if problemas or pastas:
            print(f"\nVERIFICACAO: {len(problemas)} arquivo(s) e {len(pastas)} pasta(s) pendentes.")
            return 1
        print("\nVERIFICACAO: limpo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
