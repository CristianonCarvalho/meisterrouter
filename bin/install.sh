#!/usr/bin/env bash
# ==============================================================================
# MeisterRouter — Script de Instalação e Bootstrap Automático
# Instala o pacote Python, cria symlink global e vincula o plugin no Herdr
# ==============================================================================

set -e

# Detecta se está sendo executado a partir de um clone local ou via curl/pipe
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P || echo "")"
if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/../setup.py" ]; then
    REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
else
    REPO_DIR="${HOME}/.local/share/meisterrouter"
    echo "📥 Baixando repositório para ${REPO_DIR}..."
    mkdir -p "${REPO_DIR}"
    if [ -d "${REPO_DIR}/.git" ]; then
        git -C "${REPO_DIR}" pull --quiet
    else
        git clone --depth=1 https://github.com/CristianonCarvalho/meisterrouter.git "${REPO_DIR}"
    fi
fi

LOCAL_BIN="${HOME}/.local/bin"

echo "🔮 Instalando MeisterRouter a partir de: ${REPO_DIR}"

# 1. Cria ou atualiza o ambiente virtual
if [ ! -d "${REPO_DIR}/.venv" ]; then
    echo "📦 Criando ambiente virtual Python..."
    python3 -m venv "${REPO_DIR}/.venv"
fi

echo "📦 Instalando dependências e pacote em modo editável..."
"${REPO_DIR}/.venv/bin/pip" install -q -e "${REPO_DIR}"

# 2. Garante que ~/.local/bin existe e cria symlink global
mkdir -p "${LOCAL_BIN}"
ln -sf "${REPO_DIR}/.venv/bin/meister" "${LOCAL_BIN}/meister"
echo "✅ Executável global configurado em: ${LOCAL_BIN}/meister"

# 3. Vincula ao Herdr se instalado
if command -v herdr &> /dev/null; then
    echo "🔌 Vinculando MeisterRouter como plugin no Herdr..."
    herdr plugin link "${REPO_DIR}"
    echo "✅ Plugin vinculado ao Herdr com sucesso!"
else
    echo "ℹ️  Herdr não encontrado no PATH. Instale o Herdr (curl -fsSL https://herdr.dev/install.sh | sh) e rode: herdr plugin link ${REPO_DIR}"
fi

# 4. Verificação final
echo ""
echo "🎉 Instalação concluída com sucesso!"
echo "• CLI: $("${LOCAL_BIN}/meister" --help | grep -m1 "MeisterRouter" | sed 's/^[ \t]*//')"
echo "• Para usar no Herdr: basta abrir o Herdr no seu projeto rodando 'herdr'"
echo "• Atalhos Herdr: 'prefix+m' (orquestração autônoma) e 'prefix+M' (dashboard TUI)"
