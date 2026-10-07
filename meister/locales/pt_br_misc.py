"""Catálogo de mensagens em português (pt-BR): mensagens fora das cinco áreas traduzidas."""

MESSAGES: dict[str, str] = {
    "misc.timeline.no_conclusion": "sem conclusão",
    "misc.plan_adapter.generated_not_covered": (
        "warning: task {name}: o comando {command} pode gerar {generated}, "
        "não coberto por Files: ou scope.tolerated_files"
    ),
    "misc.models.no_lane_key": "Não há chave de via para estimar o custo",
}
