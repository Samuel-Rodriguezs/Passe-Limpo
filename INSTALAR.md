# Instalar a skill /publicar-projeto

## 1. Pré-requisitos

| programa | para quê | instalar |
|---|---|---|
| Claude Code | rodar a skill | — |
| Python 3.11+ | scripts | `winget install --id Python.Python.3.13` |
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
3. Preencha o `config.json`:
   - `git_user_name`: nome que aparece nos commits;
   - `github_login`: sua conta do GitHub (o e-mail noreply é descoberto a partir dele);
   - `destino_base`: pasta onde as cópias sanitizadas são criadas.
4. Preencha as listas de proteção com os dados da **sua** organização:
   - `termos-sensiveis.txt`: pessoas, clientes, empresas, domínios;
   - `termos-internos.txt`: nomes de campos, planilhas e jargão interno;
   - `nomes-protegidos.json` (opcional): lista grande de nomes guardada só como hash, montada com `scripts/nomes_protegidos.py montar` (veja o SKILL.md).
5. Abra uma **sessão nova** do Claude Code.

## 3. Usar

```
/publicar-projeto "C:\caminho\do\projeto"
```

A skill faz a varredura, a cópia sanitizada, o README, a verificação, a revisão independente e o commit, e para num resumo de 7 linhas. O push só acontece com a resposta exata **APROVADO**.

> As listas de proteção preenchidas são confidenciais: não as publique junto com a skill.
