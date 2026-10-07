#!/bin/sh
# bin/herdr-meister.sh
# Entry wrapper for Herdr plugin actions, panes, and startup.
# Locates the meister CLI or guides the user to install it.

bin=""

if [ -n "$MEISTER_BIN" ] && [ -f "$MEISTER_BIN" ] && [ -x "$MEISTER_BIN" ]; then
    bin="$MEISTER_BIN"
fi

if [ -z "$bin" ] && command -v meister >/dev/null 2>&1; then
    _cand="$(command -v meister)"
    if [ -f "$_cand" ] && [ -x "$_cand" ]; then
        bin="$_cand"
    fi
fi

if [ -z "$bin" ] && [ -n "$HOME" ] && [ -f "$HOME/.local/bin/meister" ] && [ -x "$HOME/.local/bin/meister" ]; then
    bin="$HOME/.local/bin/meister"
fi

_opt_bin="${MEISTER_OPT_DIR:-/opt/homebrew}/bin/meister"
if [ -z "$bin" ] && [ -f "$_opt_bin" ] && [ -x "$_opt_bin" ]; then
    bin="$_opt_bin"
fi

_usr_bin="${MEISTER_USR_DIR:-/usr/local}/bin/meister"
if [ -z "$bin" ] && [ -f "$_usr_bin" ] && [ -x "$_usr_bin" ]; then
    bin="$_usr_bin"
fi

if [ -n "$bin" ]; then
    exec "$bin" "$@"
fi

printf "MeisterRouter: the 'meister' CLI was not found. The plugin needs it. Install it with:\n" >&2
printf "curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash\n" >&2
printf "(see https://github.com/CristianonCarvalho/meisterrouter#-install)\n" >&2

if [ "$HERDR_PLUGIN_EVENT" = "startup" ]; then
    exit 0
fi

"${HERDR_BIN_PATH:-herdr}" notification show "MeisterRouter" \
    --body "meister CLI not found. Install: curl -fsSL https://raw.githubusercontent.com/CristianonCarvalho/meisterrouter/main/bin/install.sh | bash" >/dev/null 2>&1 || true

if [ -t 0 ] && [ -t 2 ]; then
    printf "Press Enter to close...\n" >&2
    read -r _dummy || true
fi

exit 127
