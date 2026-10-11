"""Catálogo pt-BR do guia do formato de plano (`meister plan guide`)."""

MESSAGES: dict[str, str] = {
    "plan_guide.title": "# Formato de plano do MeisterRouter",
    "plan_guide.intro": "Um plano é um arquivo Markdown com um bloco `### Task N: nome` por tarefa. Leia este guia antes de escrever um.",
    "plan_guide.flow.heading": "## Fluxo",
    "plan_guide.flow.body": "Leia o guia, crie a moldura, preencha as tarefas, converta para o JSON canônico, confira e execute:",
    "plan_guide.split.heading": "## Muitas tarefas pequenas",
    "plan_guide.split.body": (
        "- Divida ao máximo: muitas tarefas pequenas, não poucas grandes.\n"
        "- De 3 a 5 arquivos por tarefa (até cerca de 8 só em troca mecânica, como renomear).\n"
        "- Uma entrega testável por tarefa; a suíte inteira fica verde ao fim de cada tarefa.\n"
        "- Descrição curta, cerca de 1,5 mil tokens: só o que o worker daquela tarefa precisa."
    ),
    "plan_guide.files.heading": "## **Files:** é o escopo",
    "plan_guide.files.body": (
        "- Liste cada arquivo que a tarefa cria, altera ou testa, um por item: `- Create:`, `- Modify:` ou `- Test:` seguido do caminho entre crases.\n"
        "- O gate reprova qualquer mudança fora da lista, então não esqueça os arquivos de teste.\n"
        "- Glob é aceito, por exemplo `docs/*.md`."
    ),
    "plan_guide.depends.heading": "## **Depends on:** só com dependência real",
    "plan_guide.depends.body": (
        "- Escreva `**Depends on:** Task 2, Task 5` só quando a tarefa precisa do resultado de outra.\n"
        "- Com `scope.allow_dependency_files` ligado, a tarefa também pode editar os arquivos das dependências."
    ),
    "plan_guide.parallel.heading": "## Paralelismo",
    "plan_guide.parallel.body": (
        "- Tarefas sem arquivos em comum e sem **Depends on:** rodam em paralelo.\n"
        "- Confira o paralelismo esperado com `meister plan analyze plan.json`."
    ),
    "plan_guide.dont.heading": "## O que NÃO fazer",
    "plan_guide.dont.body": (
        "- Não cite mensagens de erro de provedores de modelo na descrição da tarefa: a saída do worker é varrida atrás delas e a tarefa é descartada.\n"
        "- Não peça a uma tarefa que edite o `meister.config.yaml`: a configuração não faz parte do plano.\n"
        "- Não escreva tarefas gigantes, com muitos arquivos ou várias entregas: divida."
    ),
    "plan_guide.examples.heading": "## Exemplos",
    "plan_guide.examples.markdown": "Markdown (converta com `meister plan import`):",
    "plan_guide.examples.json": "JSON canônico (confira com `meister plan validate`):",
    "plan_guide.example.title": "# Recurso de saudação",
    "plan_guide.example.constraint": "- Manter a API pública compatível.",
    "plan_guide.example.task1.name": "Função de saudação",
    "plan_guide.example.task1.body": "Adicionar greet(name), que devolve uma saudação, com testes.",
    "plan_guide.example.task2.name": "Comando de saudação",
    "plan_guide.example.task2.body": "Adicionar o comando greet, que chama greet(name), com testes.",
}
