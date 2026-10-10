"""Catálogo em português para o comando de demonstração sintética da linha do tempo."""

MESSAGES: dict[str, str] = {
    "timeline_demo.check_arrows": "As setas de dependência são desenhadas em cotovelo entre as vias",
    "timeline_demo.check_axis": "O eixo de tempo está em minutos",
    "timeline_demo.check_ghost": "Sem fase fantasma: a tentativa interrompida não mostra segmento inventado",
    "timeline_demo.check_lane_header": "O cabeçalho de cada via mostra a via (tier) e o harness",
    "timeline_demo.check_sound": "Pressione s para ligar ou desligar o som",
    "timeline_demo.check_tooltip": "Ao passar o mouse numa barra, aparecem as durações de fase e total",
    "timeline_demo.checklist_heading": "O que conferir:",
    "timeline_demo.description": "Sobe a timeline web sobre um log sintético (sem modelo, sem custo).",
    "timeline_demo.header": "Demonstração da timeline: log sintético, modo={mode}, velocidade={speed}x",
    "timeline_demo.host_help": "Host para escutar (padrão: 127.0.0.1).",
    "timeline_demo.mode_help": "static: histórico concluído; live: eventos chegam ao longo do tempo (padrão: live).",
    "timeline_demo.port_help": "Porta para escutar (padrão: 5052).",
    "timeline_demo.speed_invalid": "--speed deve ser um número positivo (recebido {value})",
    "timeline_demo.speed_help": "Multiplicador de velocidade do modo live; offsets e durações são divididos por ele (padrão: 30).",
    "timeline_demo.stop_hint": "Pressione Ctrl+C para encerrar; o log temporário é removido.",
    "timeline_demo.stopped": "Demonstração da timeline encerrada; log temporário removido.",
    "timeline_demo.url": "Abra: {url}",
}
