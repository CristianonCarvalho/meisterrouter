#!/usr/bin/env bash
# ==============================================================================
# MeisterRouter — Script de Instalação e Bootstrap Automático
# Instala o pacote Python, cria symlink global e vincula o plugin no Herdr
# ==============================================================================

set -e

# Detecta se está sendo executado a partir de um clone local, de dentro do npx/node_modules ou via curl/pipe
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P || echo "")"
LOCAL_SHARE="${HOME}/.local/share/meisterrouter"
LOCAL_BIN="${HOME}/.local/bin"

if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/../setup.py" ] && [[ "${SCRIPT_DIR}" != *"node_modules"* ]] && [[ "${SCRIPT_DIR}" != *"_npx"* ]]; then
    REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
else
    REPO_DIR="${LOCAL_SHARE}"
    echo "📥 Instalando MeisterRouter em ${REPO_DIR}..."
    mkdir -p "${REPO_DIR}"

    if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/../setup.py" ]; then
        # Chamado a partir de pacote npx / npm: copia os arquivos para local permanente
        SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
        echo "📋 Copiando arquivos do pacote para armazenamento permanente..."
        cp -R "${SRC_DIR}/"* "${REPO_DIR}/" 2>/dev/null || true
    elif [ -d "${REPO_DIR}/.git" ]; then
        git -C "${REPO_DIR}" pull --quiet 2>/dev/null || true
    else
        # Se gh estiver instalado e logado, usa para clonar repositório privado
        if command -v gh &> /dev/null && gh auth status &> /dev/null; then
            echo "🔑 Usando GitHub CLI para acessar repositório..."
            gh repo clone CristianonCarvalho/meisterrouter "${REPO_DIR}" -- --depth=1
        elif ! git clone --depth=1 https://github.com/CristianonCarvalho/meisterrouter.git "${REPO_DIR}" 2>/dev/null; then
            echo "⚠️  Não foi possível clonar via HTTPS público. Tentando via SSH..."
            git clone --depth=1 git@github.com:CristianonCarvalho/meisterrouter.git "${REPO_DIR}" || {
                echo "❌ Erro ao baixar o repositório. O repositório é privado."
                echo "💡 Solução: instale e autentique a GitHub CLI ('gh auth login') ou adicione sua chave SSH ao GitHub."
                exit 1
            }
        fi
    fi
fi

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
echo "• Atalhos Herdr (popup do dashboard e linha do tempo): cadastre no config.toml, veja o README"
