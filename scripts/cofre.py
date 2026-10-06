#!/usr/bin/env python3
"""Cofre cifrado das listas de proteção (termos sensíveis, vocabulário interno, exceções e chaves).

Armazenamento: SQLite (cofre.db). Cada item é cifrado com AES-256-GCM (Windows CNG) usando uma
chave de dados aleatória; essa chave fica protegida pelo DPAPI do Windows (só o mesmo usuário, na
mesma máquina, consegue abrir). Sem dependências: tudo via ctypes + biblioteca padrão.

Backup portátil: exportar/importar com senha (scrypt + AES-256-GCM). A senha é digitada pelo
usuário (no app ou no terminal) e nunca é gravada.

Comandos (nenhum imprime valores):
  python cofre.py status
  python cofre.py migrar --pasta <pasta da skill>      move listas .txt e a chave dos nomes para o cofre
  python cofre.py exportar --saida backup.cofre        pede a senha no terminal
  python cofre.py importar --entrada backup.cofre      pede a senha no terminal
Para ver e editar: python app_cofre.py
"""
import argparse
import base64
import ctypes
import getpass
import hashlib
import json
import os
import sqlite3
import sys
import time
import unicodedata
from ctypes import wintypes
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LISTAS = {"sensiveis": "termos-sensiveis.txt", "internos": "termos-internos.txt", "permitidos": "nomes-permitidos.txt"}
ENTROPIA = b"publicar-projeto/cofre/v1"
MAGICO = b"PUBCOFRE1"
SCRYPT = dict(n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
SENHA_MINIMA = 12


# ---------------------------------------------------------------- DPAPI

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(dados):
    buf = ctypes.create_string_buffer(dados, len(dados))
    return _BLOB(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(dados, proteger):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    entrada, b1 = _blob(dados)
    entropia, b2 = _blob(ENTROPIA)
    saida = _BLOB()
    fn = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    args = (ctypes.byref(entrada), "publicar-projeto" if proteger else None, ctypes.byref(entropia),
            None, None, 0x1, ctypes.byref(saida))  # CRYPTPROTECT_UI_FORBIDDEN
    if not fn(*args):
        raise PermissionError("DPAPI recusou (outro usuario do Windows ou outra maquina?)")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def dpapi_proteger(dados):
    return _dpapi(dados, True)


def dpapi_abrir(dados):
    return _dpapi(dados, False)


# ---------------------------------------------------------------- AES-256-GCM (Windows CNG)

class _GCMINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("dwInfoVersion", wintypes.ULONG),
                ("pbNonce", ctypes.c_void_p), ("cbNonce", wintypes.ULONG),
                ("pbAuthData", ctypes.c_void_p), ("cbAuthData", wintypes.ULONG),
                ("pbTag", ctypes.c_void_p), ("cbTag", wintypes.ULONG),
                ("pbMacContext", ctypes.c_void_p), ("cbMacContext", wintypes.ULONG),
                ("cbAAD", wintypes.ULONG), ("cbData", ctypes.c_ulonglong), ("dwFlags", wintypes.ULONG)]


class ErroIntegridade(Exception):
    """Senha errada, chave errada ou dado adulterado."""


def _gcm(chave, nonce, dados, aad, tag=None):
    if len(chave) != 32 or len(nonce) != 12:
        raise ValueError("chave de 32 bytes e nonce de 12 bytes")
    bc = ctypes.windll.bcrypt
    alg, chv = ctypes.c_void_p(), ctypes.c_void_p()
    _ok(bc.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0))
    try:
        modo = ctypes.create_unicode_buffer("ChainingModeGCM")
        _ok(bc.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), modo, ctypes.sizeof(modo), 0))
        bchave = ctypes.create_string_buffer(chave, 32)
        _ok(bc.BCryptGenerateSymmetricKey(alg, ctypes.byref(chv), None, 0, bchave, 32, 0))
        try:
            bnonce = ctypes.create_string_buffer(nonce, 12)
            baad = ctypes.create_string_buffer(aad, len(aad)) if aad else None
            btag = ctypes.create_string_buffer(tag if tag else b"\x00" * 16, 16)
            info = _GCMINFO()
            info.cbSize, info.dwInfoVersion = ctypes.sizeof(_GCMINFO), 1
            info.pbNonce, info.cbNonce = ctypes.cast(bnonce, ctypes.c_void_p), 12
            if baad is not None:
                info.pbAuthData, info.cbAuthData = ctypes.cast(baad, ctypes.c_void_p), len(aad)
            info.pbTag, info.cbTag = ctypes.cast(btag, ctypes.c_void_p), 16
            bin_ = ctypes.create_string_buffer(dados, max(len(dados), 1))
            bout = ctypes.create_string_buffer(max(len(dados), 1))
            n = wintypes.ULONG()
            fn = bc.BCryptDecrypt if tag else bc.BCryptEncrypt
            st = fn(chv, bin_, len(dados), ctypes.byref(info), None, 0, bout, len(dados), ctypes.byref(n), 0)
            if tag and (st & 0xFFFFFFFF) == 0xC000A002:
                raise ErroIntegridade("autenticacao falhou")
            _ok(st)
            saida = bout.raw[:n.value]
            return saida if tag else (saida, btag.raw)
        finally:
            bc.BCryptDestroyKey(chv)
    finally:
        bc.BCryptCloseAlgorithmProvider(alg, 0)


def _ok(status):
    if status != 0:
        raise OSError(f"CNG falhou: 0x{status & 0xFFFFFFFF:08X}")


def cifrar(chave, dados, aad=b""):
    nonce = os.urandom(12)
    ct, tag = _gcm(chave, nonce, dados, aad)
    return nonce + tag + ct


def decifrar(chave, pacote, aad=b""):
    if len(pacote) < 28:
        raise ErroIntegridade("pacote curto")
    return _gcm(chave, pacote[:12], pacote[28:], aad, tag=pacote[12:28])


# ---------------------------------------------------------------- cofre

def _norm(texto):
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sem.casefold().split())


class Cofre:
    def __init__(self, caminho, criar=True):
        self.caminho = Path(caminho)
        if not criar and not self.caminho.is_file():
            raise FileNotFoundError(self.caminho)
        self.db = sqlite3.connect(str(self.caminho))
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor BLOB NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS itens (id INTEGER PRIMARY KEY, lista TEXT NOT NULL,"
                        " valor BLOB NOT NULL, criado_em TEXT NOT NULL)")
        linha = self.db.execute("SELECT valor FROM meta WHERE chave='chave_dados'").fetchone()
        if linha:
            self._chave = dpapi_abrir(linha[0])
        else:
            self._chave = os.urandom(32)
            self.db.execute("INSERT INTO meta VALUES ('chave_dados', ?)", (dpapi_proteger(self._chave),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('versao', ?)", (b"1",))
            self.db.commit()

    def fechar(self):
        self.db.close()
        self._chave = None

    # listas
    def listar(self, lista):
        out = []
        for _id, valor in self.db.execute("SELECT id, valor FROM itens WHERE lista=? ORDER BY id", (lista,)):
            out.append(decifrar(self._chave, valor, f"item:{lista}".encode()).decode("utf-8"))
        return out

    def _ids(self, lista):
        return [(i, decifrar(self._chave, v, f"item:{lista}".encode()).decode("utf-8"))
                for i, v in self.db.execute("SELECT id, valor FROM itens WHERE lista=?", (lista,))]

    def contem(self, lista, valor):
        alvo = _norm(valor)
        return any(_norm(v) == alvo for _, v in self._ids(lista))

    def adicionar(self, lista, valor):
        valor = valor.strip()
        if lista not in LISTAS or not valor or self.contem(lista, valor):
            return False
        pacote = cifrar(self._chave, valor.encode("utf-8"), f"item:{lista}".encode())
        self.db.execute("INSERT INTO itens (lista, valor, criado_em) VALUES (?, ?, ?)",
                        (lista, pacote, time.strftime("%Y-%m-%d %H:%M:%S")))
        self.db.commit()
        return True

    def remover(self, lista, valor):
        alvo = _norm(valor)
        ids = [i for i, v in self._ids(lista) if _norm(v) == alvo]
        self.db.executemany("DELETE FROM itens WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        return len(ids)

    def contagem(self):
        return {l: self.db.execute("SELECT COUNT(*) FROM itens WHERE lista=?", (l,)).fetchone()[0] for l in LISTAS}

    # segredos (ex.: chave HMAC dos nomes protegidos)
    def segredo(self, nome):
        linha = self.db.execute("SELECT valor FROM meta WHERE chave=?", (f"seg:{nome}",)).fetchone()
        return decifrar(self._chave, linha[0], f"seg:{nome}".encode()) if linha else None

    def definir_segredo(self, nome, valor):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                        (f"seg:{nome}", cifrar(self._chave, valor, f"seg:{nome}".encode())))
        self.db.commit()

    # backup portatil
    def exportar(self, saida, senha):
        if len(senha) < SENHA_MINIMA:
            raise ValueError(f"senha com menos de {SENHA_MINIMA} caracteres")
        segs = {}
        for chave, valor in self.db.execute("SELECT chave, valor FROM meta WHERE chave LIKE 'seg:%'"):
            nome = chave[4:]
            segs[nome] = base64.b64encode(decifrar(self._chave, valor, chave.encode())).decode()
        carga = json.dumps({"listas": {l: self.listar(l) for l in LISTAS}, "segredos": segs}).encode()
        sal = os.urandom(16)
        chave = hashlib.scrypt(senha.encode("utf-8"), salt=sal, **SCRYPT)
        Path(saida).write_bytes(MAGICO + sal + cifrar(chave, carga, MAGICO))

    def importar(self, entrada, senha):
        bruto = Path(entrada).read_bytes()
        if not bruto.startswith(MAGICO):
            raise ValueError("arquivo nao e um backup do cofre")
        sal = bruto[len(MAGICO):len(MAGICO) + 16]
        chave = hashlib.scrypt(senha.encode("utf-8"), salt=sal, **SCRYPT)
        carga = json.loads(decifrar(chave, bruto[len(MAGICO) + 16:], MAGICO))
        novos = {l: sum(self.adicionar(l, v) for v in vals) for l, vals in carga["listas"].items() if l in LISTAS}
        for nome, b64 in carga.get("segredos", {}).items():
            atual = self.segredo(nome)
            novo = base64.b64decode(b64)
            if atual is not None and atual != novo:
                raise ValueError(f"o segredo '{nome}' do backup e diferente do atual; nada foi trocado")
            self.definir_segredo(nome, novo)
        return novos


def abrir_se_existir(caminho):
    """Cofre para leitura pela varredura, ou None se nao houver cofre."""
    if caminho and Path(caminho).is_file():
        return Cofre(caminho, criar=False)
    return None


# ---------------------------------------------------------------- comandos

def _ler_txt(p):
    if not p.is_file():
        return [], []
    linhas = p.read_text(encoding="utf-8").splitlines()
    return ([l for l in linhas if l.strip().startswith("#") or not l.strip()],
            [l.strip() for l in linhas if l.strip() and not l.strip().startswith("#")])


def cmd_status(a):
    c = abrir_se_existir(a.cofre)
    if not c:
        print("COFRE: NAO EXISTE")
        return 1
    seg = [n for (n,) in c.db.execute("SELECT substr(chave,5) FROM meta WHERE chave LIKE 'seg:%'")]
    print(f"COFRE: OK ({c.caminho})")
    for l, n in c.contagem().items():
        print(f"  {l}: {n} itens")
    print(f"  segredos: {', '.join(seg) or 'nenhum'}")
    return 0


def cmd_migrar(a):
    pasta = Path(a.pasta)
    c = Cofre(Path(a.cofre) if a.cofre else pasta / "cofre.db")
    total = {}
    for lista, arquivo in LISTAS.items():
        p = pasta / arquivo
        comentarios, valores = _ler_txt(p)
        for v in valores:
            c.adicionar(lista, v)
        faltando = [v for v in valores if not c.contem(lista, v)]
        if faltando:
            print(f"ERRO: {len(faltando)} itens de {arquivo} nao entraram no cofre; nada apagado.")
            return 1
        if valores:
            cab = [l for l in comentarios if l.strip()] + [
                "# CONTEUDO MOVIDO PARA O COFRE CIFRADO (cofre.db). Veja e edite com: python scripts/app_cofre.py",
                "# Linhas novas aqui continuam valendo, mas ficam em texto puro: prefira o app."]
            p.write_text("\n".join(cab) + "\n", encoding="utf-8")
        total[lista] = len(valores)
    nomes = pasta / "nomes-protegidos.json"
    if nomes.is_file():
        dados = json.loads(nomes.read_text(encoding="utf-8"))
        if "chave" in dados:
            chave = bytes.fromhex(dados["chave"])
            atual = c.segredo("chave_nomes")
            if atual is not None and atual != chave:
                print("ERRO: o cofre ja tem outra chave de nomes; nada alterado.")
                return 1
            c.definir_segredo("chave_nomes", chave)
            if c.segredo("chave_nomes") != chave:
                print("ERRO: a chave dos nomes nao conferiu no cofre; nada apagado.")
                return 1
            del dados["chave"]
            dados["chave_no_cofre"] = True
            nomes.write_text(json.dumps(dados, ensure_ascii=False, indent=0), encoding="utf-8")
            total["chave_nomes"] = "movida"
    print("MIGRADO:", json.dumps(total, ensure_ascii=False))
    return 0


def cmd_exportar(a):
    c = Cofre(a.cofre, criar=False)
    s1 = getpass.getpass("Senha do backup (min. %d caracteres): " % SENHA_MINIMA)
    if s1 != getpass.getpass("Repita a senha: "):
        print("As senhas nao conferem.")
        return 1
    c.exportar(a.saida, s1)
    print(f"Backup cifrado gravado em {a.saida}")
    return 0


def cmd_importar(a):
    c = Cofre(a.cofre)
    try:
        novos = c.importar(a.entrada, getpass.getpass("Senha do backup: "))
    except ErroIntegridade:
        print("Senha errada ou arquivo adulterado.")
        return 1
    print("IMPORTADO (itens novos):", json.dumps(novos))
    return 0


def caminho_padrao():
    return Path(__file__).resolve().parent.parent / "cofre.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cofre", default=str(caminho_padrao()))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(f=cmd_status)
    p = sub.add_parser("migrar")
    p.add_argument("--pasta", default=str(Path(__file__).resolve().parent.parent))
    p.set_defaults(f=cmd_migrar)
    p = sub.add_parser("exportar")
    p.add_argument("--saida", required=True)
    p.set_defaults(f=cmd_exportar)
    p = sub.add_parser("importar")
    p.add_argument("--entrada", required=True)
    p.set_defaults(f=cmd_importar)
    a = ap.parse_args()
    if a.cmd == "migrar" and a.cofre == str(caminho_padrao()):
        a.cofre = None
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
