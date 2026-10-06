# Instalar a skill /publicar-projeto

## 1. Pré-requisitos

| programa | para quê | instalar |
|---|---|---|
| Claude Code | rodar a skill | — |
| Python 3.11+ | scripts e app do cofre | `winget install --id Python.Python.3.13` |
| Git | commits | `winget install --id Git.Git` |
| GitHub CLI | confirmar repositório privado e push | `winget install --id GitHub.cli` e depois `gh auth login` |
| Tesseract OCR (com português) | ler imagens | `winget install --id UB-Mannheim.TesseractOCR` (marque *Portuguese*) |
| PyMuPDF (opcional) | OCR melhor em texto pequeno | `python -m pip install pymupdf` |
| pytest (opcional) | rodar os testes da skill | `python -m pip install pytest` |

Sem o GitHub CLI logado, a skill funciona até a pausa, mas **bloqueia o push**, porque não consegue confirmar que o repositório é privado. Sem o Tesseract, as imagens **não podem ser aprovadas**.

## 2. Instalar

1. Baixe ou clone este repositório.
2. No PowerShell, dentro da pasta, rode:
   ```
   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
   ```
   Ele copia a skill para `%USERPROFILE%\.claude\skills\publicar-projeto` (com backup de uma instalação anterior, se houver), confere os pré-requisitos e roda os testes.
3. Preencha o `config.json` (`git_user_name`, `github_login`, `destino_base`).
4. Preencha as listas de proteção com os dados da **sua** organização e leve-as para o cofre:
   - escreva os termos em `termos-sensiveis.txt` e `termos-internos.txt` (um por linha);
   - rode `python scripts\cofre.py migrar`: os itens vão para o `cofre.db` cifrado e os `.txt` ficam só com comentários;
   - depois disso, edite pelo app: duplo clique em `Abrir cofre.cmd`.
5. (Opcional) monte a lista de nomes protegidos com `scripts\nomes_protegidos.py montar ... --cofre cofre.db` (veja o SKILL.md).
6. Abra uma **sessão nova** do Claude Code.

## 3. Levar o cofre para outra máquina

O `cofre.db` só abre no usuário do Windows que o criou. No app, aba **Backup → Exportar backup…** gera um arquivo `.cofre` cifrado com senha (mínimo 12 caracteres); na outra máquina, **Importar backup…**. Faça um backup novo sempre que mudar as listas: se o perfil do Windows for recriado, o backup é a única forma de recuperar o cofre.

## 4. Usar

```
/publicar-projeto "C:\caminho\do\projeto"
```

A skill faz a varredura, a cópia sanitizada, o README, a verificação, a revisão independente e o commit, e para num resumo de 7 linhas. O push só acontece com a resposta exata **APROVADO**.

> O `cofre.db`, os backups `.cofre` e o `nomes-protegidos.json` são de cada usuário: não os publique (já estão no `.gitignore`).
