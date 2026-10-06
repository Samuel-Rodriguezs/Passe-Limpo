"""GitHub CLI falso para os testes. Resposta controlada por FAKE_GH_RESP.

  true / false -> repo privado / publico
  404          -> repo nao existe
  noauth       -> gh sem login
  login:<nome> -> 'gh api user' devolve <nome>
"""
import os
import sys

resp = os.environ.get("FAKE_GH_RESP", "true")
args = sys.argv[1:]
if resp == "noauth":
    print("To get started with GitHub CLI, please run:  gh auth login", file=sys.stderr)
    sys.exit(4)
if args[:2] == ["api", "user"]:
    print(resp.split(":", 1)[1] if resp.startswith("login:") else "exemplo-usuario")
    sys.exit(0)
if resp == "404":
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
print(resp)
