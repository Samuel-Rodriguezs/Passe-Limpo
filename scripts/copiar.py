#!/usr/bin/env python3
"""Copia para o destino somente o que o plano de varrer.py liberou. A origem e so lida.

Uso:
  python copiar.py --plano "<trab>\\plano.json" --destino "C:\\caminho\\portfolio\\nome" [--aprovar REL ...] [--pular REL ...]
  python copiar.py --plano "<trab>\\plano.json" --conferir      (so confere se a origem continua intacta)

Regras:
  - recusa se o destino ja existir (nunca sobrescreve);
  - recusa se destino e origem se sobrepuserem;
  - EXCLUIR nunca e copiado; DUVIDA so com --aprovar explicito;
  - abre a origem apenas em modo leitura e o destino em modo 'xb' (falha se o arquivo existir);
  - no fim confere tamanho e mtime de todos os arquivos da origem contra o plano.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def conferir_origem(origem, arquivos):
    alterados = []
    for it in arquivos:
        if "mtime_ns" not in it:
            continue
        p = origem / it["caminho"]
        try:
            st = p.lstat()
        except OSError:
            alterados.append(it["caminho"] + " (sumiu)")
            continue
        if st.st_size != it["tamanho"] or st.st_mtime_ns != it["mtime_ns"]:
            alterados.append(it["caminho"])
    return alterados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plano", required=True)
    ap.add_argument("--destino")
    ap.add_argument("--aprovar", action="append", default=[], help="arquivo DUVIDA aprovado pelo usuario (repetivel)")
    ap.add_argument("--pular", action="append", default=[], help="arquivo a deixar de fora mesmo se liberado (repetivel)")
    ap.add_argument("--manifesto", help="onde gravar o manifesto (padrao: ao lado do plano)")
    ap.add_argument("--conferir", action="store_true")
    a = ap.parse_args()

    plano_path = Path(a.plano).resolve()
    plano = json.loads(plano_path.read_text(encoding="utf-8"))
    if plano.get("modo") != "inventario":
        print("ERRO: use o plano do modo inventario, nao o de verificacao.")
        return 2
    origem = Path(plano["origem"]).resolve()
    arquivos = plano["arquivos"]

    if a.conferir:
        alterados = conferir_origem(origem, arquivos)
        print("ORIGEM INTACTA: sim" if not alterados else f"ORIGEM INTACTA: NAO ({len(alterados)})")
        for c in alterados[:50]:
            print("  -", c)
        return 0 if not alterados else 1

    if not a.destino:
        print("ERRO: informe --destino.")
        return 2
    destino = Path(a.destino.strip('"')).resolve()
    if destino.exists():
        print(f"ERRO: destino ja existe, nao sobrescrevo: {destino}")
        return 2
    if destino == origem or origem in destino.parents or destino in origem.parents:
        print("ERRO: destino e origem se sobrepoem.")
        return 2

    aprovar = {str(Path(x)) for x in a.aprovar}
    pular = {str(Path(x)) for x in a.pular}
    por_caminho = {it["caminho"]: it for it in arquivos}
    invalidos = [x for x in aprovar if x not in por_caminho or por_caminho[x]["status"] != "DUVIDA"]
    if invalidos:
        print("ERRO: --aprovar so vale para arquivos DUVIDA do plano:", invalidos)
        return 2

    copiados, a_sanitizar, fora, mudaram = [], [], [], []
    destino.mkdir(parents=True, exist_ok=False)
    for it in arquivos:
        rel = it["caminho"]
        st = it["status"]
        liberado = st in ("COPIAR", "SANITIZAR") or (st == "DUVIDA" and rel in aprovar)
        if not liberado or rel in pular:
            fora.append({"caminho": rel, "status": st, "motivos": it.get("motivos", []) + (["pulado"] if rel in pular else [])})
            continue
        src = origem / rel
        s = src.lstat()
        if s.st_size != it["tamanho"] or s.st_mtime_ns != it["mtime_ns"]:
            mudaram.append(rel)
            fora.append({"caminho": rel, "status": st, "motivos": ["mudou desde a varredura: nao copiado"]})
            continue
        alvo = destino / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as f, open(alvo, "xb") as g:
            shutil.copyfileobj(f, g)
        copiados.append(rel)
        if st == "SANITIZAR" or (st == "DUVIDA" and it.get("total_achados")):
            a_sanitizar.append({"caminho": rel, "total_achados": it.get("total_achados", 0), "motivos": it.get("motivos", [])})

    alterados = conferir_origem(origem, arquivos)
    manifesto = {
        "origem": str(origem), "destino": str(destino),
        "copiados": copiados, "a_sanitizar": a_sanitizar, "fora": fora,
        "dirs_ignorados": plano.get("dirs_ignorados", []),
        "origem_intacta": not alterados, "origem_alterados": alterados, "mudaram_durante": mudaram,
    }
    man_path = Path(a.manifesto).resolve() if a.manifesto else plano_path.with_name("manifesto.json")
    if man_path == destino or destino in man_path.parents:
        man_path = plano_path.with_name("manifesto.json")
    man_path.write_text(json.dumps(manifesto, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"DESTINO: {destino}")
    print(f"copiados: {len(copiados)} | a sanitizar: {len(a_sanitizar)} | fora: {len(fora)} | pastas ignoradas: {len(manifesto['dirs_ignorados'])}")
    print(f"manifesto: {man_path}")
    print("ORIGEM INTACTA: sim" if not alterados else f"ORIGEM INTACTA: NAO -> {alterados[:20]}")
    if mudaram:
        print("ATENCAO: arquivos mudaram na origem durante o processo (nao copiados):", mudaram[:20])
    for x in a_sanitizar:
        print(f"  sanitizar: {x['caminho']} ({x['total_achados']} achados)")
    return 0 if not alterados else 1


if __name__ == "__main__":
    sys.exit(main())
