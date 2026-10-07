#!/usr/bin/env bash
# ==============================================================================
# MeisterRouter — Script de Instalação e Bootstrap Automático
# Instala o pacote Python, cria symlink global e vincula o plugin no Herdr
# ==============================================================================

INSTALL_VERSION="main"
INSTALL_HELP=0

install_usage() {
    cat <<'EOF'
Uso: install.sh [--version <ref> | --version=<ref>] [-h | --help]

Instala a versão main por padrão. <ref> pode ser main, latest ou uma tag vN.N.N.
Também é possível definir MEISTER_VERSION=<ref>.
EOF
}

validate_version_ref() {
    case "$1" in
        main|latest)
            return 0
            ;;
    esac

    if [[ "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+([.-][A-Za-z0-9]+)*)?$ ]]; then
        return 0
    fi

    echo "❌ [MeisterRouter] Erro: versão inválida '$1'. Use main, latest ou uma tag vN.N.N." >&2
    return 2
}

parse_install_args() {
    local argument
    INSTALL_VERSION="${MEISTER_VERSION-main}"
    INSTALL_HELP=0

    while [ "$#" -gt 0 ]; do
        argument="$1"
        case "$argument" in
            --version)
                if [ "$#" -lt 2 ]; then
                    echo "❌ [MeisterRouter] Erro: --version exige uma versão." >&2
                    return 2
                fi
                INSTALL_VERSION="$2"
                shift 2
                ;;
            --version=*)
                INSTALL_VERSION="${argument#--version=}"
                shift
                ;;
            -h|--help)
                INSTALL_HELP=1
                return 0
                ;;
            *)
                echo "❌ [MeisterRouter] Erro: argumento desconhecido '$argument'." >&2
                install_usage >&2
                return 2
                ;;
        esac
    done

    validate_version_ref "${INSTALL_VERSION}" || return 2
    return 0
}

resolve_latest_tag() {
    local remote_url="https://github.com/CristianonCarvalho/meisterrouter.git"
    local tag_line tag
    tag_line="$(git ls-remote --tags --refs --sort=-v:refname "${remote_url}" 'v*' | head -1)"
    tag="${tag_line#*refs/tags/}"
    if [ -z "${tag_line}" ] || ! validate_version_ref "${tag}" || [ "${tag}" = "main" ] || [ "${tag}" = "latest" ]; then
        echo "❌ [MeisterRouter] Erro: não foi possível encontrar uma tag de versão no repositório remoto." >&2
        return 1
    fi
    printf '%s\n' "${tag}"
}

if [ "${MEISTER_INSTALL_SOURCE_ONLY:-}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

if ! parse_install_args "$@"; then
    exit 2
fi
if [ "${INSTALL_HELP}" = "1" ]; then
    install_usage
    exit 0
fi

set -e

# Verificação prévia do Herdr
if ! command -v herdr >/dev/null 2>&1; then
    if [ "${MEISTER_ALLOW_NO_HERDR}" != "1" ]; then
        echo "❌ [MeisterRouter] Erro: Herdr não encontrado no PATH!"
        echo "   O Herdr é pré-requisito para o funcionamento do MeisterRouter."
        echo "   Instale o Herdr com:"
        echo "     curl -fsSL https://herdr.dev/install.sh | sh"
        echo "   (Para prosseguir sem o Herdr, execute com MEISTER_ALLOW_NO_HERDR=1)"
        exit 1
    fi
    echo "⚠️  Herdr não encontrado no PATH, continuando devido a MEISTER_ALLOW_NO_HERDR=1."
fi

# Detecta se está sendo executado a partir de um clone local, de dentro do npx/node_modules ou via curl/pipe
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd -P || echo "")"
LOCAL_SHARE="${HOME}/.local/share/meisterrouter"
LOCAL_BIN="${HOME}/.local/bin"

if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/../setup.py" ] && [[ "${SCRIPT_DIR}" != *"node_modules"* ]] && [[ "${SCRIPT_DIR}" != *"_npx"* ]]; then
    REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
    if [ "${INSTALL_VERSION}" != "main" ]; then
        echo "⚠️  a versão fixa só vale para a instalação por curl; usando o clone local em ${REPO_DIR}"
    fi
else
    REPO_DIR="${LOCAL_SHARE}"
    echo "📥 Instalando MeisterRouter em ${REPO_DIR}..."
    mkdir -p "${REPO_DIR}"

    if [ -n "${SCRIPT_DIR}" ] && [ -f "${SCRIPT_DIR}/../setup.py" ]; then
        # Chamado a partir de pacote npx / npm: copia os arquivos para local permanente
        SRC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
        echo "📋 Copiando arquivos do pacote para armazenamento permanente..."
        cp -R "${SRC_DIR}/"* "${REPO_DIR}/" 2>/dev/null || true
        if [ "${INSTALL_VERSION}" != "main" ]; then
            echo "⚠️  a versão fixa só vale para a instalação por curl; usando o clone local em ${REPO_DIR}"
        fi
    else
        REMOTE_URL="https://github.com/CristianonCarvalho/meisterrouter.git"
        if [ "${INSTALL_VERSION}" = "latest" ]; then
            INSTALL_VERSION="$(resolve_latest_tag)" || exit 1
        fi

        if [ -d "${REPO_DIR}/.git" ]; then
            if [ "${INSTALL_VERSION}" = "main" ]; then
                if ! git -C "${REPO_DIR}" checkout --quiet main; then
                    echo "❌ [MeisterRouter] Erro: não foi possível selecionar main em ${REPO_DIR}." >&2
                    exit 1
                fi
                git -C "${REPO_DIR}" pull --quiet 2>/dev/null || true
            else
                if ! git -C "${REPO_DIR}" fetch --depth=1 origin "refs/tags/${INSTALL_VERSION}:refs/tags/${INSTALL_VERSION}"; then
                    echo "❌ [MeisterRouter] Erro: não foi possível buscar a tag ${INSTALL_VERSION}." >&2
                    exit 1
                fi
                if ! git -C "${REPO_DIR}" checkout --quiet "${INSTALL_VERSION}"; then
                    echo "❌ [MeisterRouter] Erro: não foi possível selecionar a tag ${INSTALL_VERSION}; verifique alterações locais ou se a tag existe." >&2
                    exit 1
                fi
            fi
        else
            CLONE_ARGS=(clone --depth=1)
            if [ "${INSTALL_VERSION}" != "main" ]; then
                CLONE_ARGS+=(--branch "${INSTALL_VERSION}")
            fi

            # Se gh estiver instalado e logado, usa para clonar repositório privado
            if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
                echo "🔑 Usando GitHub CLI para acessar repositório..."
                GH_ARGS=(repo clone CristianonCarvalho/meisterrouter "${REPO_DIR}" -- --depth=1)
                if [ "${INSTALL_VERSION}" != "main" ]; then
                    GH_ARGS+=(--branch "${INSTALL_VERSION}")
                fi
                gh "${GH_ARGS[@]}"
            elif ! git "${CLONE_ARGS[@]}" "${REMOTE_URL}" "${REPO_DIR}" 2>/dev/null; then
                echo "⚠️  Não foi possível clonar via HTTPS público. Tentando via SSH..."
                git "${CLONE_ARGS[@]}" git@github.com:CristianonCarvalho/meisterrouter.git "${REPO_DIR}" || {
                    echo "❌ Erro ao baixar o repositório. O repositório é privado."
                    echo "💡 Solução: instale e autentique a GitHub CLI ('gh auth login') ou adicione sua chave SSH ao GitHub."
                    exit 1
                }
            fi
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

# 3. Configuração automática e diagnóstico com meister setup
if [ "${MEISTER_SKIP_SETUP}" != "1" ]; then
    echo "⚙️  Executando configuração automática (meister setup)..."
    "${LOCAL_BIN}/meister" setup
else
    echo "ℹ️  Configuração ignorada devido a MEISTER_SKIP_SETUP=1."
fi

# 4. Verificação final
echo ""
echo "🎉 Instalação concluída com sucesso!"
echo "• CLI: $("${LOCAL_BIN}/meister" --help | grep -m1 "MeisterRouter" | sed 's/^[ \t]*//')"
VERSION_OUTPUT="$("${LOCAL_BIN}/meister" --version 2>/dev/null || true)"
echo "• Versão: ${VERSION_OUTPUT}"
echo "• Para usar no Herdr: basta abrir o Herdr no seu projeto rodando 'herdr'"
echo "• Consulte o relatório do 'meister setup' acima para status e próximos passos"
