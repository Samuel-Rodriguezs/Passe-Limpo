#!/usr/bin/env python3
"""Lista de nomes protegidos (clientes, partes, empresas, profissionais) guardada só como hash.

Montar (nunca imprime nomes; só contagens):
  python nomes_protegidos.py montar --pessoas ARQ:CAMPO [...] --empresas ARQ:CAMPO [...] \
      --saida nomes-protegidos.json --fonte "descricao da origem"

  ARQ = JSON do Airtable ({records:[{cellValuesByFieldId:{...}}]}), qualquer extensão, com CAMPO = id do
        campo com o nome; ou lista com um nome por linha, com CAMPO = "-" (ARQ:-).

O arquivo final tem uma chave aleatória e os HMAC-SHA256 (truncados) das variantes de cada nome:
  pessoa  -> nome completo, primeiro+último, primeiro+segundo (sem 'da/de/do/dos/das/e')
  empresa -> nome completo, nome sem sufixo societário e duas primeiras palavras
  (toda variante tem 2+ palavras de 2+ letras e não é feita só de palavras genéricas)
Quem só tem o arquivo não lê a lista; a varredura compara os hashes das sequências de palavras do texto.

Falso positivo (palavra comum que coincide com uma variante): ponha a sequência em nomes-permitidos.txt.
"""
import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARTICULAS = {"DA", "DE", "DO", "DAS", "DOS", "E", "D"}
SUFIXOS = {"LTDA", "SA", "S", "A", "EIRELI", "ME", "EPP", "MEI", "CIA", "EM", "RECUPERACAO", "JUDICIAL", "FALIDA", "MASSA"}
GENERICAS = set("""
BANCO COMPANHIA EMPRESA EMPRESAS SERVICOS SERVICO INDUSTRIA INDUSTRIAS COMERCIO GRUPO BRASIL BRASILEIRA BRASILEIRO
SAO PAULO RIO JANEIRO MINAS GERAIS NACIONAL ASSOCIACAO SINDICATO CONSTRUTORA CONSTRUCOES TRANSPORTES TRANSPORTE
LOGISTICA SOLUCOES ADMINISTRACAO ADMINISTRADORA HOSPITAL INSTITUTO CENTRO MUNICIPIO PREFEITURA ESTADO FUNDACAO
COOPERATIVA SEGURANCA VIGILANCIA LIMPEZA CONSERVACAO ATACADISTA DISTRIBUIDORA COMERCIAL TECNOLOGIA SISTEMAS
PARTICIPACOES ENGENHARIA SAUDE EDUCACIONAL EDUCACAO ENSINO MINISTERIO UNIAO FEDERAL CAIXA ECONOMICA SUPERMERCADO
SUPERMERCADOS MERCADO LOJAS LOJA RESTAURANTE ALIMENTOS ALIMENTACAO AUTO POSTO POSTOS TERCEIRIZACAO RECURSOS HUMANOS
GESTAO CONSULTORIA ASSESSORIA TELECOMUNICACOES TELEFONIA ENERGIA ELETRICA ELETRICIDADE DISTRIBUICAO GERAL GERAIS
INTERNACIONAL GLOBAL NOVA NOVO PRIMEIRA SUL NORTE LESTE OESTE CLINICA FARMACIA DROGARIA BAR CASA SOCIEDADE
""".split())
JANELA_MAX = 8


def normalizar(texto):
    sem = "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))
    return re.sub(r"[^A-Z0-9]+", " ", sem.upper()).split()


def variante_valida(v):
    """Pelo menos 2 palavras, todas com 2+ letras, e nao so palavras genericas/particulas."""
    w = v.split()
    return len(w) >= 2 and all(len(x) >= 2 for x in w) and not all(x in GENERICAS or x in PARTICULAS for x in w)


def variantes_pessoa(nome):
    p = [w for w in normalizar(nome) if not w.isdigit()]
    if len(p) < 2:
        return set()
    out = {" ".join(p)}
    sem_part = [w for w in p if w not in PARTICULAS]
    if len(sem_part) >= 2:
        out.add(f"{sem_part[0]} {sem_part[-1]}")
        out.add(f"{sem_part[0]} {sem_part[1]}")
    return {v for v in out if variante_valida(v)}


def variantes_empresa(nome):
    p = [w for w in normalizar(nome) if not w.isdigit()]
    if not p:
        return set()
    out = {" ".join(p)}
    nucleo = list(p)
    while nucleo and nucleo[-1] in SUFIXOS:
        nucleo.pop()
    if len(nucleo) >= 2:
        out.add(" ".join(nucleo))
        if not (nucleo[0] in GENERICAS and nucleo[1] in GENERICAS) and nucleo[1] not in PARTICULAS:
            out.add(f"{nucleo[0]} {nucleo[1]}")
    # Palavra unica (ex.: so a primeira palavra do nome) nao entra: coincide demais com palavras
    # comuns ("origem", "teste", "sistema"). Empresa de uma palavra so vai em termos-sensiveis.txt.
    return {v for v in out if variante_valida(v)}


def assinar(chave, sequencia):
    return hmac.new(bytes.fromhex(chave), sequencia.encode(), hashlib.sha256).hexdigest()[:20]


def ler_nomes(spec):
    arq, _, campo = spec.rpartition(":")
    if not arq:
        arq, campo = spec, "-"
    p = Path(arq)
    if campo == "-":
        return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    dados = json.loads(p.read_text(encoding="utf-8"))
    nomes = []
    for r in dados.get("records", []):
        campos = r.get("cellValuesByFieldId") or r.get("fields") or {}
        v = campos.get(campo)
        if isinstance(v, str) and v.strip():
            nomes.append(v.strip())
    return nomes


class NomesProtegidos:
    """Usado pelo varrer.py: procura variantes protegidas nas sequências de palavras de uma linha."""

    def __init__(self, arquivo, permitidos=None):
        dados = json.loads(Path(arquivo).read_text(encoding="utf-8"))
        self.chave = dados["chave"]
        self.hashes = set(dados["hashes"])
        self.permitidos = set()
        if permitidos and Path(permitidos).is_file():
            for l in Path(permitidos).read_text(encoding="utf-8").splitlines():
                if l.strip() and not l.strip().startswith("#"):
                    self.permitidos.add(" ".join(normalizar(l)))

    def procurar(self, linha):
        palavras = normalizar(linha)
        achados = []
        i = 0
        while i < len(palavras):
            casou = 0
            for n in range(min(JANELA_MAX, len(palavras) - i), 0, -1):
                seq = " ".join(palavras[i:i + n])
                if seq in self.permitidos:
                    continue
                if assinar(self.chave, seq) in self.hashes:
                    casou = n
                    break
            if casou:
                achados.append(casou)
                i += casou
            else:
                i += 1
        return achados


def cmd_montar(a):
    saida = Path(a.saida)
    chave = secrets.token_hex(16)
    hashes, contagem = set(), {}
    for tipo, specs, gerar in (("pessoas", a.pessoas, variantes_pessoa), ("empresas", a.empresas, variantes_empresa)):
        nomes = set()
        for spec in specs or []:
            nomes.update(" ".join(normalizar(n)) for n in ler_nomes(spec))
        nomes.discard("")
        variantes = set()
        for n in nomes:
            variantes |= gerar(n)
        hashes |= {assinar(chave, v) for v in variantes}
        contagem[tipo] = {"nomes": len(nomes), "variantes": len(variantes)}
    saida.write_text(json.dumps({
        "versao": 1, "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"), "fonte": a.fonte,
        "contagem": contagem, "chave": chave, "hashes": sorted(hashes),
    }, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"nomes protegidos -> {saida}")
    for tipo, c in contagem.items():
        print(f"  {tipo}: {c['nomes']} nomes, {c['variantes']} variantes")
    print(f"  hashes: {len(hashes)}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("montar")
    p.add_argument("--pessoas", action="append", default=[])
    p.add_argument("--empresas", action="append", default=[])
    p.add_argument("--saida", required=True)
    p.add_argument("--fonte", default="")
    p.set_defaults(f=cmd_montar)
    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
