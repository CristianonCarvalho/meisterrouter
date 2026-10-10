"""Marcas de plataforma compartilhadas pelos testes."""

import sys

import pytest

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="usa sinais/grupos de processos de Unix",
)
