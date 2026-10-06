#!/usr/bin/env python3
"""Registra os exemplos SINTETICOS que a skill acabou de gerar no destino.

Uso:
  python registrar_sinteticos.py --destino "C:\\caminho\\portfolio\\nome" --pasta exemplos \
      --plano "<trab>\\plano.json" [--manifesto-copia "<trab>\\manifesto.json"] --saida "<trab>\\sinteticos.json"

Grava {caminho_relativo: sha256}. Na verificacao (varrer.py --sinteticos) esses arquivos
deixam de cair em DUVIDA so por estarem em pasta de exemplos/dados, mas o conteudo
continua sendo varrido. Se o arquivo mudar depois do registro, o hash nao bate e ele
volta a ser DUVIDA.

Travas:
  - so registra dentro de --pasta (nunca o destino inteiro);
  - recusa arquivo que veio da origem (caminho copiado ou mesmo hash de um arquivo da origem);
  - o manifesto fica fora do destino.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from varrer import sha256_arquivo  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", required=True)
    ap.add_argument("--pasta", default="exemplos")
    ap.add_argument("--plano", required=True, help="plano do inventario da origem (varrer.py)")
    ap.add_argument("--manifesto-copia", help="manifesto.json do copiar.py, se houve copia")
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()

    destino = Path(a.destino).resolve()
    pasta = (destino / a.pasta).resolve()
    saida = Path(a.saida).resolve()
    if destino not in pasta.parents:
        print("ERRO: --pasta tem que ser uma subpasta do destino.")
        return 2
    if not pasta.is_dir():
        print(f"ERRO: pasta de exemplos nao existe: {pasta}")
        return 2
    if saida == destino or destino in saida.parents:
        print("ERRO: o manifesto nao pode ficar dentro do destino.")
        return 2

    plano = json.loads(Path(a.plano).read_text(encoding="utf-8"))
    hashes_origem = {it["sha256"] for it in plano.get("arquivos", []) if it.get("sha256")}
    copiados = set()
    if a.manifesto_copia:
        copiados = {str(Path(c)) for c in json.loads(Path(a.manifesto_copia).read_text(encoding="utf-8")).get("copiados", [])}

    registrados, recusados = {}, []
    for p in sorted(pasta.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = str(p.relative_to(destino))
        h = sha256_arquivo(p)
        if rel in copiados:
            recusados.append((rel, "veio da origem (copiado)"))
        elif h in hashes_origem:
            recusados.append((rel, "mesmo conteudo de um arquivo da origem"))
        else:
            registrados[rel] = h

    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps({"destino": str(destino), "pasta": a.pasta,
                                 "gerado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "arquivos": registrados}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"sinteticos registrados: {len(registrados)} -> {saida}")
    for rel, motivo in recusados:
        print(f"  RECUSADO {rel}: {motivo}")
    return 1 if recusados else 0


if __name__ == "__main__":
    sys.exit(main())
