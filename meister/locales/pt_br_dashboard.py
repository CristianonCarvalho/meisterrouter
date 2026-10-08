"""Catálogo em português para a configuração do dashboard."""

MESSAGES: dict[str, str] = {
    "dashboard.expected_object": "deve ser um objeto",
    "dashboard.heading": "Dashboard:",
    "dashboard.idle_exit_minutes_help": "Encerra o dashboard após esta quantidade de minutos sem atividade.",
    "dashboard.idle_exit_minutes_label": "Minutos até encerrar sem atividade",
    "dashboard.no_open_help": "Não inicia nem abre o dashboard nesta execução da orquestração.",
    "dashboard.open_invalid": "deve ser um destes valores: auto, app, tab, never (recebido {value})",
    "dashboard.open_label": "Abertura",
    "dashboard.positive_integer": "{field} deve ser um inteiro positivo (recebido {value})",
    "dashboard.timeline": "Timeline: {url}",
    "dashboard.window_height_label": "Altura da janela",
    "dashboard.window_width_label": "Largura da janela",
}
