#!/usr/bin/env python3
"""App local do cofre: ver, adicionar e remover itens das listas cifradas, testar nomes e fazer backup.

  python app_cofre.py            (ou duplo clique em "Abrir cofre.cmd" na pasta da skill)

Tudo aparece mascarado; um item só é revelado por alguns segundos, quando você pede.
A janela fecha sozinha depois de 5 minutos sem uso. Nada sai da máquina.
"""
import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cofre as cofre_mod  # noqa: E402

PASTA = Path(__file__).resolve().parent.parent
ABAS = [("sensiveis", "Termos sensíveis"), ("internos", "Vocabulário interno"), ("permitidos", "Exceções de nomes")]
REVELAR_MS = 10_000
INATIVO_MS = 5 * 60_000


def mascara(valor):
    return "●" * min(len(valor), 24) + f"   ({len(valor)} caracteres)"


class App:
    def __init__(self, raiz, caminho_cofre=None, caminho_nomes=None):
        self.raiz = raiz
        self.cofre = cofre_mod.Cofre(caminho_cofre or PASTA / "cofre.db")
        self.caminho_nomes = Path(caminho_nomes or PASTA / "nomes-protegidos.json")
        self.valores, self.listas, self._revelado, self._timer = {}, {}, None, None
        raiz.title("Cofre")
        raiz.geometry("620x440")
        raiz.protocol("WM_DELETE_WINDOW", self.fechar)
        nb = ttk.Notebook(raiz)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        for chave, titulo in ABAS:
            nb.add(self._aba_lista(nb, chave), text=titulo)
        nb.add(self._aba_nomes(nb), text="Nomes protegidos")
        nb.add(self._aba_backup(nb), text="Backup")
        self.status = ttk.Label(raiz, text="Itens mascarados. Selecione um e clique em Revelar.")
        self.status.pack(fill="x", padx=8, pady=(0, 6))
        for ev in ("<Key>", "<Button>", "<Motion>"):
            raiz.bind_all(ev, self._atividade, add="+")
        self._atividade()
        self.atualizar()

    # ------------------------------------------------------------ abas
    def _aba_lista(self, pai, chave):
        f = ttk.Frame(pai)
        lb = tk.Listbox(f, font=("Consolas", 11), activestyle="none")
        lb.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        self.listas[chave] = lb
        bt = ttk.Frame(f)
        bt.pack(side="right", fill="y", padx=6, pady=4)
        for texto, acao in (("Revelar 10 s", lambda: self.revelar(chave)), ("Adicionar…", lambda: self.adicionar(chave)),
                            ("Remover", lambda: self.remover(chave)), ("Procurar…", lambda: self.procurar(chave))):
            ttk.Button(bt, text=texto, command=acao, width=16).pack(pady=3)
        return f

    def _aba_nomes(self, pai):
        f = ttk.Frame(pai)
        self.info_nomes = ttk.Label(f, justify="left")
        self.info_nomes.pack(anchor="w", padx=8, pady=8)
        ttk.Button(f, text="Testar se um nome está na lista…", command=self.testar_nome).pack(anchor="w", padx=8)
        ttk.Label(f, text="A lista guarda só impressões digitais (hash): os nomes não podem ser listados.\n"
                          "Para atualizar com a base de cadastro, peça ao Claude.", justify="left").pack(anchor="w", padx=8, pady=12)
        return f

    def _aba_backup(self, pai):
        f = ttk.Frame(pai)
        ttk.Label(f, text="Backup cifrado com senha (para outra máquina ou para guardar fora do computador).\n"
                          f"Senha com no mínimo {cofre_mod.SENHA_MINIMA} caracteres. Sem a senha o backup não abre.",
                  justify="left").pack(anchor="w", padx=8, pady=8)
        ttk.Button(f, text="Exportar backup…", command=self.exportar).pack(anchor="w", padx=8, pady=3)
        ttk.Button(f, text="Importar backup…", command=self.importar).pack(anchor="w", padx=8, pady=3)
        return f

    # ------------------------------------------------------------ dados
    def atualizar(self):
        for chave, _ in ABAS:
            self.valores[chave] = self.cofre.listar(chave)
            lb = self.listas[chave]
            lb.delete(0, "end")
            for v in self.valores[chave]:
                lb.insert("end", mascara(v))
        texto = "Lista de nomes protegidos: não encontrada."
        if self.caminho_nomes.is_file():
            d = json.loads(self.caminho_nomes.read_text(encoding="utf-8"))
            c = d.get("contagem", {})
            texto = (f"Pessoas: {c.get('pessoas', {}).get('nomes', '?')}   ·   Empresas: {c.get('empresas', {}).get('nomes', '?')}\n"
                     f"Atualizada em: {d.get('gerado_em', '?')}\n"
                     f"Chave: {'no cofre (cifrada)' if d.get('chave_no_cofre') else 'NO ARQUIVO (migre para o cofre)'}")
        self.info_nomes.config(text=texto)

    def _selecionado(self, chave):
        sel = self.listas[chave].curselection()
        return sel[0] if sel else None

    def revelar(self, chave):
        i = self._selecionado(chave)
        if i is None:
            return
        self._mascarar_revelado()
        self.listas[chave].delete(i)
        self.listas[chave].insert(i, self.valores[chave][i])
        self._revelado = (chave, i)
        self.raiz.after(REVELAR_MS, self._mascarar_revelado)

    def _mascarar_revelado(self):
        if self._revelado:
            chave, i = self._revelado
            self._revelado = None
            if i < len(self.valores[chave]):
                self.listas[chave].delete(i)
                self.listas[chave].insert(i, mascara(self.valores[chave][i]))

    def adicionar(self, chave):
        v = simpledialog.askstring("Adicionar", "Novo item:", parent=self.raiz)
        if v and v.strip():
            ok = self.cofre.adicionar(chave, v)
            self.status.config(text="Adicionado." if ok else "Já existia (nada mudou).")
            self.atualizar()

    def remover(self, chave):
        i = self._selecionado(chave)
        if i is None or not messagebox.askyesno("Remover", "Remover o item selecionado?", parent=self.raiz):
            return
        self.cofre.remover(chave, self.valores[chave][i])
        self.status.config(text="Removido.")
        self.atualizar()

    def procurar(self, chave):
        v = simpledialog.askstring("Procurar", "Texto a procurar (não diferencia maiúsculas nem acentos):", parent=self.raiz)
        if v:
            self.status.config(text="Está na lista." if self.cofre.contem(chave, v) else "Não está na lista.")

    def testar_nome(self):
        v = simpledialog.askstring("Testar nome", "Nome:", parent=self.raiz)
        if not v:
            return
        try:
            from nomes_protegidos import NomesProtegidos
            achou = NomesProtegidos(self.caminho_nomes, None, self.cofre).procurar(v)
            self.status.config(text="Está na lista protegida." if achou else "Não está na lista protegida.")
        except Exception as e:  # lista ausente ou chave indisponivel
            self.status.config(text=f"Não foi possível testar: {e.__class__.__name__}")

    def _senha(self, titulo, confirmar):
        s1 = simpledialog.askstring(titulo, "Senha:", show="•", parent=self.raiz)
        if not s1:
            return None
        if confirmar and s1 != simpledialog.askstring(titulo, "Repita a senha:", show="•", parent=self.raiz):
            messagebox.showerror(titulo, "As senhas não conferem.", parent=self.raiz)
            return None
        return s1

    def exportar(self):
        destino = filedialog.asksaveasfilename(defaultextension=".cofre", filetypes=[("Backup do cofre", "*.cofre")])
        senha = destino and self._senha("Exportar backup", True)
        if not senha:
            return
        try:
            self.cofre.exportar(destino, senha)
            messagebox.showinfo("Exportar backup", "Backup cifrado gravado.", parent=self.raiz)
        except ValueError as e:
            messagebox.showerror("Exportar backup", str(e), parent=self.raiz)

    def importar(self):
        origem = filedialog.askopenfilename(filetypes=[("Backup do cofre", "*.cofre")])
        senha = origem and self._senha("Importar backup", False)
        if not senha:
            return
        try:
            novos = self.cofre.importar(origem, senha)
            messagebox.showinfo("Importar backup", f"Itens novos: {sum(novos.values())}", parent=self.raiz)
            self.atualizar()
        except cofre_mod.ErroIntegridade:
            messagebox.showerror("Importar backup", "Senha errada ou arquivo adulterado.", parent=self.raiz)
        except ValueError as e:
            messagebox.showerror("Importar backup", str(e), parent=self.raiz)

    # ------------------------------------------------------------ bloqueio
    def _atividade(self, _ev=None):
        if self._timer:
            self.raiz.after_cancel(self._timer)
        self._timer = self.raiz.after(INATIVO_MS, self.fechar)

    def fechar(self):
        try:
            self.cofre.fechar()
        finally:
            self.raiz.destroy()


def main():
    raiz = tk.Tk()
    try:
        App(raiz)
    except PermissionError as e:
        raiz.withdraw()
        messagebox.showerror("Cofre", f"Não foi possível abrir o cofre: {e}")
        raiz.destroy()
        return 1
    raiz.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
