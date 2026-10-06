#!/usr/bin/env python3
"""Imagens: OCR do texto visível e leitura/remoção de metadados (EXIF, XMP, IPTC, texto de PNG).

Usado pelo varrer.py para imagens (sempre DUVIDA): o texto lido por OCR e o texto dos metadados
passam pela mesma varredura; metadado presente vira achado "metadados_imagem".

Limpar metadados (só no destino, nunca na origem):
  python midia.py limpar --destino "C:\\caminho\\portfolio\\nome" --plano "<trab>\\plano.json"

Sem dependências novas: Tesseract (CLI) para OCR, PyMuPDF (opcional) para ampliar a imagem antes,
e leitura/escrita de PNG e JPEG feita aqui mesmo.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

IMAGENS_OCR = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
PNG_ASSINATURA = b"\x89PNG\r\n\x1a\n"
PNG_CHUNKS_META = {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"}
JPEG_SEG_META = set(range(0xE1, 0xF0)) - {0xEE} | {0xFE}  # APP1..APP15 exceto APP14 (Adobe/cor), e COM


# ---------------------------------------------------------------- OCR

def tesseract():
    achado = shutil.which("tesseract")
    if achado:
        return achado
    for p in (Path(os.environ.get("ProgramFiles", "")) / "Tesseract-OCR" / "tesseract.exe",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe"):
        if p.is_file():
            return str(p)
    return None


def _ampliada(caminho, pasta):
    """Copia ampliada 2x (texto pequeno de print lê melhor). Sem PyMuPDF, usa a original."""
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return caminho
    try:
        doc = fitz.open(str(caminho))
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2))
        saida = Path(pasta) / "ampliada.png"
        pix.save(str(saida))
        return saida
    except Exception:
        return caminho


def ocr(caminho):
    """Texto da imagem, ou None se o OCR não estiver disponível ou falhar. Nunca escreve ao lado do arquivo."""
    exe = tesseract()
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="publicar-ocr-") as tmp:
        alvo = _ampliada(caminho, tmp)
        try:
            r = subprocess.run([exe, str(alvo), "stdout", "-l", "por+eng", "--psm", "3"], capture_output=True,
                               timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- metadados

def _png_chunks(dados):
    i = len(PNG_ASSINATURA)
    while i + 8 <= len(dados):
        tam = struct.unpack(">I", dados[i:i + 4])[0]
        tipo = dados[i + 4:i + 8]
        corpo = dados[i + 8:i + 8 + tam]
        yield tipo, corpo, dados[i:i + 12 + tam]
        i += 12 + tam
        if tipo == b"IEND":
            break


def _png_texto(tipo, corpo):
    try:
        if tipo == b"tEXt":
            return corpo.replace(b"\x00", b": ").decode("latin-1")
        if tipo == b"zTXt":
            k, _, resto = corpo.partition(b"\x00")
            return k.decode("latin-1") + ": " + zlib.decompress(resto[1:]).decode("latin-1", errors="replace")
        if tipo == b"iTXt":
            k, _, resto = corpo.partition(b"\x00")
            comprimido = resto[0:1] == b"\x01"
            resto = resto[2:]
            _, _, resto = resto.partition(b"\x00")
            _, _, texto = resto.partition(b"\x00")
            texto = zlib.decompress(texto) if comprimido else texto
            return k.decode("latin-1") + ": " + texto.decode("utf-8", errors="replace")
        if tipo == b"eXIf":
            return _strings_ascii(corpo)
    except Exception:
        return ""
    return ""


def _strings_ascii(dados, minimo=4):
    return "\n".join(m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e\xc0-\xff]{%d,}" % minimo, dados))


def _jpeg_segmentos(dados):
    """(marcador, corpo, bytes_do_segmento) ate o inicio dos dados da imagem (SOS)."""
    i = 2
    while i + 4 <= len(dados) and dados[i] == 0xFF:
        marcador = dados[i + 1]
        if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
            i += 2
            continue
        tam = struct.unpack(">H", dados[i + 2:i + 4])[0]
        yield marcador, dados[i + 4:i + 2 + tam], dados[i:i + 2 + tam]
        if marcador == 0xDA:  # SOS: dali em diante e imagem
            return
        i += 2 + tam


def metadados(caminho):
    """(lista de tipos encontrados, texto legível dos metadados)."""
    dados = Path(caminho).read_bytes()
    tipos, textos = [], []
    if dados.startswith(PNG_ASSINATURA):
        for tipo, corpo, _ in _png_chunks(dados):
            if tipo in PNG_CHUNKS_META:
                tipos.append("png-" + tipo.decode())
                textos.append(_png_texto(tipo, corpo))
    elif dados.startswith(b"\xff\xd8"):
        for marcador, corpo, _ in _jpeg_segmentos(dados):
            if marcador in JPEG_SEG_META:
                nome = {0xE1: "exif/xmp", 0xED: "iptc", 0xFE: "comentario"}.get(marcador, f"app{marcador - 0xE0}")
                tipos.append("jpeg-" + nome)
                textos.append(_strings_ascii(corpo))
    else:
        tipos.append("formato-nao-verificavel")
    return sorted(set(tipos)), "\n".join(t for t in textos if t)


def limpar_bytes(dados):
    """Mesmos pixels, sem metadados. Devolve None se o formato não for PNG nem JPEG."""
    if dados.startswith(PNG_ASSINATURA):
        return PNG_ASSINATURA + b"".join(seg for tipo, _, seg in _png_chunks(dados) if tipo not in PNG_CHUNKS_META)
    if dados.startswith(b"\xff\xd8"):
        saida, i = bytearray(b"\xff\xd8"), 2
        for marcador, _, seg in _jpeg_segmentos(dados):
            i = dados.index(seg, i) + len(seg)
            if marcador not in JPEG_SEG_META:
                saida += seg
            if marcador == 0xDA:
                break
        return bytes(saida + dados[i:])
    return None


# ---------------------------------------------------------------- comando

def cmd_limpar(a):
    destino = Path(a.destino).resolve()
    plano = json.loads(Path(a.plano).read_text(encoding="utf-8"))
    origem = Path(plano["origem"]).resolve()
    if destino == origem or origem in destino.parents or destino in origem.parents:
        print("ERRO: destino e origem se sobrepoem; so limpo dentro da copia.")
        return 2
    limpos, intocados = [], []
    for p in sorted(destino.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(destino).parts or p.suffix.lower() not in IMAGENS_OCR:
            continue
        tipos, _ = metadados(p)
        if not tipos:
            continue
        novo = limpar_bytes(p.read_bytes())
        rel = str(p.relative_to(destino))
        if novo is None:
            intocados.append(rel)
            continue
        p.write_bytes(novo)
        limpos.append((rel, tipos))
    for rel, tipos in limpos:
        print(f"  limpo: {rel} ({', '.join(tipos)})")
    for rel in intocados:
        print(f"  NAO LIMPO (formato sem suporte, converta para PNG): {rel}")
    print(f"imagens limpas: {len(limpos)} | sem suporte: {len(intocados)}")
    return 1 if intocados else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("limpar")
    p.add_argument("--destino", required=True)
    p.add_argument("--plano", required=True)
    p.set_defaults(f=cmd_limpar)
    a = ap.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
