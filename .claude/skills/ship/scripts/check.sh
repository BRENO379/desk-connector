#!/usr/bin/env bash
# =============================================================================
# check.sh — Roda o linter e os testes do projeto antes de um commit.
#
# Uso:  bash .claude/skills/ship/scripts/check.sh [--all]
#   (sem argumento) verifica só as linhas alteradas em relação ao HEAD
#   --all           verifica os arquivos inteiros (mostra problemas antigos)
#
# Código de saída: 0 = tudo ok | 1 = linter ou testes falharam
# =============================================================================
set -uo pipefail

MODE="${1:-changed}"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERRO: não estou dentro de um repositório git." >&2
  exit 1
}
cd "$ROOT" || exit 1

FAILED=0

# -----------------------------------------------------------------------------
# 1) Descobre quais arquivos Markdown mudaram (staged + não staged + novos)
# -----------------------------------------------------------------------------
if [ "$MODE" = "--all" ]; then
  mapfile -t MD_FILES < <(git ls-files '*.md')
else
  mapfile -t MD_FILES < <(
    { git diff --name-only --diff-filter=ACMR HEAD -- '*.md' 2>/dev/null
      git ls-files --others --exclude-standard -- '*.md'; } | sort -u
  )
fi

# Descarta arquivos que não existem mais no disco (ex.: renomeados/apagados)
EXISTING=()
for f in "${MD_FILES[@]}"; do
  [ -f "$f" ] && EXISTING+=("$f")
done
MD_FILES=("${EXISTING[@]}")

# -----------------------------------------------------------------------------
# 2) LINT — markdownlint-cli2, filtrado para as linhas que você alterou
# -----------------------------------------------------------------------------
echo "== LINT (markdownlint) =="
if [ ${#MD_FILES[@]} -eq 0 ]; then
  echo "Nenhum arquivo .md alterado — lint pulado."
elif ! command -v npx >/dev/null 2>&1; then
  echo "AVISO: npx (Node.js) não encontrado — lint pulado."
else
  LINT_OUT="$(npx -y markdownlint-cli2 "${MD_FILES[@]}" 2>&1 | grep -E '^[^ ]+\.md:[0-9]+' || true)"

  if [ "$MODE" != "--all" ]; then
    # Monta a lista "arquivo:linha" das linhas alteradas (diff sem contexto)
    CHANGED_LINES="$(
      for f in "${MD_FILES[@]}"; do
        if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
          git diff -U0 HEAD -- "$f" | awk -v f="$f" '
            /^@@/ {
              split($3, a, ",");            # +inicio,quantidade
              start = substr(a[1], 2); n = (a[2] == "" ? 1 : a[2]);
              for (i = 0; i < n; i++) print f ":" start + i
            }'
        else
          # Arquivo novo: todas as linhas contam como alteradas
          awk -v f="$f" '{ print f ":" NR }' "$f"
        fi
      done
    )"
    # Mantém só os erros que caem em linhas alteradas
    LINT_OUT="$(printf '%s\n' "$LINT_OUT" | awk -v list="$CHANGED_LINES" '
      BEGIN { n = split(list, L, "\n"); for (i = 1; i <= n; i++) keep[L[i]] = 1 }
      NF { split($1, p, ":"); if ((p[1] ":" p[2]) in keep) print }')"
  fi

  if [ -n "$LINT_OUT" ]; then
    echo "$LINT_OUT"
    echo "LINT: FALHOU"
    FAILED=1
  else
    echo "LINT: OK (${#MD_FILES[@]} arquivo(s))"
  fi
fi

# -----------------------------------------------------------------------------
# 3) TESTES — verificações estruturais da documentação
#    Blocos de código ``` precisam abrir e fechar (número par de cercas);
#    um bloco aberto quebra toda a renderização do README no GitHub.
# -----------------------------------------------------------------------------
echo
echo "== TESTES =="
TEST_FAILED=0
TEST_FILES=("${MD_FILES[@]}")
[ "$MODE" != "--all" ] && [ ${#TEST_FILES[@]} -eq 0 ] && mapfile -t TEST_FILES < <(git ls-files '*.md')
for f in "${TEST_FILES[@]}"; do
  FENCES="$(grep -cE '^[[:space:]]*```' "$f" || true)"
  if [ $((FENCES % 2)) -ne 0 ]; then
    echo "FALHOU: $f tem um bloco de código \`\`\` sem fechamento ($FENCES cercas)."
    TEST_FAILED=1
  fi
done

# Suítes de teste de verdade, se o projeto passar a ter alguma
RAN_SUITE=0
if [ -f package.json ] && grep -q '"test"[[:space:]]*:' package.json; then
  echo "-> npm test"; npm test || TEST_FAILED=1; RAN_SUITE=1
fi
if [ -f pyproject.toml ] || [ -f pytest.ini ] || [ -d tests ]; then
  if command -v pytest >/dev/null 2>&1; then
    echo "-> pytest"; pytest -q || TEST_FAILED=1; RAN_SUITE=1
  fi
fi
[ $RAN_SUITE -eq 0 ] && echo "(nenhuma suíte de testes configurada — só as verificações de docs rodaram)"
if [ $TEST_FAILED -eq 0 ]; then echo "TESTES: OK"; else echo "TESTES: FALHOU"; FAILED=1; fi

echo
if [ $FAILED -eq 0 ]; then echo "RESULTADO: tudo verde ✅"; else echo "RESULTADO: há falhas ❌"; fi
exit $FAILED
