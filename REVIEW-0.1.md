# Konstructor 0.1 — Revisão técnica

**Escopo:** inspeção estática do ZIP fornecido, execução dos testes incluídos e smoke tests da CLI/instalador.  
**Limitação:** esta revisão não inspecionou o repositório privado Kodoo; avalia somente o código incluído no pacote Konstructor.  
**Resultado geral:** bom protótipo de fluxo e segurança básica, ainda não é um gerador de aplicações por IA funcional. Há bloqueadores de segurança e integridade a resolver antes de usar contra um workspace real.

## Resumo executivo

O pacote apresenta uma boa separação inicial entre CLI, adaptador do repositório, planejamento, staging, diff, validação e aplicação. O estado é mantido fora do workspace-alvo, os caminhos passam por uma política de raiz, o diff é legível e existe detecção de alterações concorrentes por hash. A interface de provedor de IA é independente de SDKs.

Porém, o gerador atual é predominantemente determinístico: o provedor não está conectado ao planejamento nem à geração; a geração produz sempre um esqueleto genérico `Requirement`; e nenhuma view é gerada. Além disso, `apply` não exige uma validação aprovada e o Web Terminal permite operações de escrita sem autenticação quando exposto à rede.

## Verificações executadas

- `python -m unittest discover -s tests -v`: **11 testes passaram**.
- `python -m konstructor --help`: **passou**; CLI reconhece o nome novo.
- `python -m konstructor config show`: **passou**; estado padrão em `~/.konstructor`.
- `install.sh --dry-run --prefix ...`: **passou**; launcher e diretório de instalação usam `konstructor`.
- Não executei build/testes do workspace Kodoo real. O `cargo` não foi necessário para os testes unitários do Konstructor.

## Pontos positivos

1. **Separação de responsabilidades inicial:** `cli`, `repository`, `planner`, `workflow`, `safety`, `provider` e as interfaces de terminal estão separados.
2. **Staging fora do workspace-alvo:** `generate` cria arquivos na área de estado do Konstructor e não altera diretamente o projeto.
3. **Revisão humana explícita:** o fluxo tem `diff` e a aplicação exige confirmação explícita.
4. **Detecção de conflitos de base:** hashes dos arquivos-alvo são conferidos antes da aplicação.
5. **Sem dependências Python externas no núcleo:** facilita instalar e reproduzir o protótipo.
6. **Testes úteis para o fluxo básico:** incluem path traversal, conflito, diff/apply, TUI e Web Terminal.
7. **Renomeação aplicada:** pacote Python, metadata, comando, textos, instalador e diretório de estado adotam `konstructor`. A variável `KODOO_GEN_HOME` permanece apenas como fallback de migração; `KONSTRUCTOR_HOME` é a opção canônica.

## Achados prioritários

### P1 — Web Terminal pode aplicar alterações remotamente sem autenticação

**Evidência:** `konstructor/web_terminal.py`, rotas `POST /action` e `POST /input`; `WebTerminalApp.action()` encaminha `approve` fornecido pelo cliente. O comando `web` aceita `--host 0.0.0.0`.  
**Impacto:** qualquer cliente com acesso de rede ao servidor pode tentar executar ações e enviar `approve: true` para aplicar um changeset. A advertência no README não é uma barreira técnica.  
**Recomendação:** por padrão, recusar bind não-loopback; antes de permitir acesso remoto, exigir autenticação forte/token, proteção CSRF/origin, limite de requisição e confirmação no terminal local. Não tratar o campo `approve` enviado pelo browser como autorização suficiente.

### P1 — `apply` não exige validação bem-sucedida

**Evidência:** `konstructor/workflow.py`, função `apply()`. Ela verifica `yes` e os hashes da base, mas não exige que `validate()` tenha sido executado nem que todos os checks estejam `passed`.  
**Impacto:** uma proposta não validada, com validação falha ou bloqueada, pode ser aplicada após confirmação.  
**Recomendação:** aplicar somente se existir um resultado de validação para o changeset, se todos os checks obrigatórios estiverem aprovados e se o staging ainda corresponder aos hashes aprovados na revisão.

### P1 — O staging pode mudar depois da revisão

**Evidência:** `generate()` grava conteúdo em staging; `diff()` lê os arquivos staged; `apply()` volta a copiar esses arquivos, mas não compara seus hashes com os hashes vistos no diff/validação.  
**Impacto:** o conteúdo aplicado pode diferir do conteúdo revisado.  
**Recomendação:** registrar hash de cada arquivo staged, invalidar aprovação se algum mudar e revalidar antes de aplicar. Tornar o changeset imutável ou criar um snapshot selado.

### P1 — Arquivos existentes podem ser sobrescritos apesar de a proposta dizer `add`

**Evidência:** os arquivos da proposta são marcados `action: add`, mas `apply()` não rejeita um destino que já existia quando o changeset foi criado; o hash de base apenas impede alterações posteriores.  
**Impacto:** um arquivo pré-existente e inalterado pode ser substituído por um esqueleto genérico, contrariando a semântica de `add`.  
**Recomendação:** `add` deve falhar se o destino existir. `modify` deve ser um tipo de ação separado, com diff explícito e hash-base. Nunca inferir que sobrescrever é aceitável.

### P1 — Ainda não há geração por IA nem geração de views

**Evidência:** `provider.py` declara `LLMProvider`, mas `planner.py` não o chama; `workflow.py::_contents()` gera quatro arquivos fixos com `Requirement`; não há geração de views nem integração end-to-end com uma implementação de modelo.  
**Impacto:** o produto não atende ainda ao requisito central: combinar reutilização e geração de código por IA para criar aplicações de diferentes tipos, inclusive suas views.  
**Recomendação:** tratar 0.1 como prova de fluxo seguro. Próximo incremento: injetar um provedor real via interface, exigir saída estruturada validada por schema, planejar alterações específicas por arquivo e gerar um exemplo vertical real que inclua view e testes. Manter o provedor determinístico apenas como mock/teste.

### P2 — Validação não valida realmente o código proposto

**Evidência:** `validate()` verifica caminhos e existência dos arquivos staged; `cargo fmt --all -- --check` executa sobre o workspace existente, não sobre uma cópia do workspace com o conteúdo staged aplicado. Não há `cargo check` ou testes do módulo gerado.  
**Impacto:** o relatório pode parecer favorável sem provar que o código proposto compila. Também não há timeout para o subprocesso.  
**Recomendação:** montar um workspace temporário/cópia de validação, aplicar o staging nele e executar comandos allowlisted e configuráveis (`cargo fmt --check`, `cargo check`, testes pertinentes), com timeout, captura de saída e estados `passed`/`failed`/`blocked`.

### P2 — Scanner Rust baseado em regex é incompleto

**Evidência:** `repository.py` usa regex para extrair `members` do `Cargo.toml` e símbolos de arquivos `.rs`.  
**Impacto:** pode ignorar formas válidas de configuração e símbolos (`pub(crate)`, macros, declarações condicionais, módulos fora do padrão); também pode classificar como símbolos métodos e funções em contextos que exigem interpretação sintática. O catálogo atual não é uma fonte arquitetural completa.  
**Recomendação:** usar `tomllib` para ler TOML, suportar as formas relevantes de workspace e documentar limitações. Evoluir para parser AST ou ferramenta de análise Rust quando a geração depender de precisão estrutural. Preservar sempre caminho e linha como evidência.

### P2 — O planejador não cria um plano semântico confiável

**Evidência:** `planner.py` busca correspondências por palavras e cria sempre o mesmo conjunto de arquivos `Cargo.toml`, `lib.rs`, `domain.rs`, `application.rs`.  
**Impacto:** não decide de modo confiável quais superfícies são necessárias, não inspeciona exemplos de implementação equivalentes e pode propor módulos redundantes.  
**Recomendação:** plano por etapas com arquivos propostos, ação por arquivo, evidências reutilizadas, dependências, superfícies aplicáveis, testes e perguntas em aberto. Bloquear geração quando o destino ou a arquitetura não estiverem claros.

### P2 — O catálogo não é suficientemente rico para reutilização

**Evidência:** `index()` indexa apenas membros enumerados pelo regex e declarações lexicais em `.rs`; não modela usos/call sites, imports, rotas, componentes de view, testes associados, relações entre módulos ou contratos.  
**Impacto:** o gerador não consegue decidir com confiança se deve reutilizar ou criar algo novo.  
**Recomendação:** acrescentar grafo de dependências, referências de uso, exportações públicas, rotas/views e associação entre implementação e testes. Marcar a cobertura e confiança do catálogo, sem tratar uma busca vazia como prova de inexistência.

### P3 — Servidor HTTP precisa de limites operacionais

**Evidência:** `_Handler._body()` lê `Content-Length` sem limite explícito e o endpoint SSE mantém uma conexão aberta por cliente.  
**Impacto:** um endpoint exposto pode consumir memória ou threads com requisições excessivas.  
**Recomendação:** impor tamanho máximo de body, limite de conexões e duração, validação de JSON e limites de concorrência.

## Trade-offs atuais

- **Python sem dependências:** bom para prototipagem e portabilidade; regex não basta para análise profunda de Rust.
- **Provedor abstrato:** boa fronteira arquitetural; por enquanto, é só um contrato e não participa do fluxo de geração.
- **CLI + TUI + Web Terminal em 0.1:** oferece ergonomia, mas amplia superfície de segurança e manutenção antes do núcleo de geração estar validado.
- **Template fixo:** é determinístico e testável, mas não atende a aplicações de diferentes tipos nem à geração de views.

## Plano recomendado

### P0 — Fechar segurança e integridade
1. Exigir validação aprovada antes de `apply`.
2. Selar/hash dos artefatos staged e conferir antes de aplicar.
3. Impedir sobrescrita de destinos marcados como `add`.
4. Bloquear Web Terminal remoto sem autenticação; preferencialmente desabilitar bind não-loopback no MVP.

### P1 — Provar geração útil
5. Conectar um provedor real através de `LLMProvider`, com configuração explícita e sem acoplar SDK ao núcleo.
6. Criar schemas de plano e changeset e rejeitar saídas inválidas.
7. Fazer a geração variar por requisito e contexto, em vez de sempre criar `Requirement`.
8. Gerar uma funcionalidade vertical mínima para Kodoo, com view apropriada e testes.

### P2 — Melhorar conhecimento arquitetural
9. Ler TOML com parser adequado e ampliar o catálogo com referências, exports, rotas, views e testes.
10. Validar em workspace temporário e executar checks relevantes com timeout.
11. Adicionar testes para: staging adulterado, validação falha, destino `add` preexistente, acesso remoto, JSON/body inválido e falha de comando.

## Critério para chamar a próxima versão de MVP

Uma versão seguinte só deve ser considerada MVP quando:
- nenhum arquivo do workspace muda antes de aprovação;
- o conteúdo aplicado é exatamente o que foi revisado;
- não se aplica changeset não validado ou com checks obrigatórios falhos;
- arquivos marcados `add` não sobrescrevem arquivos existentes;
- Web Terminal não permite alterações remotas anônimas;
- o provedor configurado participa do planejamento/geração;
- ao menos um exemplo gera e valida código específico do requisito, incluindo view quando aplicável;
- o resultado é reproduzível e acompanhado de evidências, diffs e testes.

