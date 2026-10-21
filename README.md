# Konstructor

Gerador CLI independente para transformar requisitos em propostas revisáveis de alteração para o workspace Kodoo.

## Princípio de segurança

O gerador nunca escreve no workspace-alvo durante `inspect`, `index`, `plan`, `generate`, `validate` ou `diff`. As propostas ficam em staging fora do repositório. `apply` exige o ID exato do changeset e uma aprovação explícita (`--yes` em automação ou confirmação interativa). Alterações locais não são descartadas e conflitos de hash interrompem a aplicação.

## Uso rápido

```bash
python -m konstructor app wizard /mnt/toshiba/git/kodoo
python -m konstructor inspect /caminho/kodoo --json
python -m konstructor index /caminho/kodoo
python -m konstructor plan /caminho/kodoo "criar módulo de contratos" --output PLAN.md
python -m konstructor generate /caminho/kodoo PLAN_ID
python -m konstructor validate /caminho/kodoo CHANGESET_ID
python -m konstructor diff /caminho/kodoo CHANGESET_ID
python -m konstructor apply /caminho/kodoo CHANGESET_ID --yes
python -m konstructor tui /caminho/kodoo
python -m konstructor tui /caminho/kodoo --theme high-contrast
python -m konstructor tui /caminho/kodoo --theme /caminho/meu-tema.json
python -m konstructor web /caminho/kodoo --host 0.0.0.0 --port 8765
```

`app wizard` orienta a criação manual de um módulo Kodoo (incluindo decisão sobre Direct) e salva uma proposta no estado local. Ele não modifica o workspace; o scaffold oficial continua sendo `python3 scripts/standard.py new <slug>` depois de revisar a proposta.

`app wizard --apply` exige terminal interativo e confirmação digitando `APPLY`, e então delega ao scaffold oficial. `app review <workspace> <proposal-id>` reabre a proposta. `app new-app` prepara um app Dioxus mínimo (tela inicial, CSS, configuração e teste SSR), propõe sua inclusão no workspace Cargo e salva um diff revisável. Use `app diff`, `app validate` e `app apply`; a aplicação exige confirmação interativa digitando `APPLY`. O scaffold não liga módulos Direct automaticamente: isso depende das escolhas de produto do app.

## Provedor OpenAI-compatible

Configure `KONSTRUCTOR_LLM_BASE_URL`, `KONSTRUCTOR_LLM_MODEL` e, quando exigido, `KONSTRUCTOR_LLM_API_KEY` no ambiente. O padrão do endpoint é `https://api.openai.com/v1`; tokens nunca são gravados na configuração. `konstructor provider status` verifica a configuração e `konstructor app wizard --llm` usa saída JSON estruturada para enriquecer o resumo. O modo local continua disponível sem credenciais.

## Servidor MCP para Codex

O servidor MCP usa stdio; o Codex fornece o raciocínio e o conteúdo. O Konstructor não envia chamadas a provedor LLM nesse fluxo. Configure o processo MCP com `KONSTRUCTOR_WORKSPACE` apontando para o repositório Kodoo e, opcionalmente, `KONSTRUCTOR_HOME` para o estado local:

```json
{
  "mcpServers": {
    "konstructor": {
      "command": "konstructor",
      "args": ["mcp"],
      "env": {
        "KONSTRUCTOR_WORKSPACE": "/mnt/toshiba/git/kodoo",
        "KONSTRUCTOR_HOME": "/home/green/.konstructor"
      }
    }
  }
}
```

As ferramentas cobrem inspeção/indexação, planos, propostas de app/módulo, staging de arquivos, diff, validação e operações do roadmap. Para aplicar um changeset: revise o diff, valide, rode `konstructor approve /mnt/toshiba/git/kodoo CHANGESET_ID` num terminal e confirme digitando `APPLY CHANGESET_ID`; passe o token de uso único emitido ao MCP. Token expira em cinco minutos e fica vinculado ao changeset e aos hashes dos arquivos. O servidor não oferece shell nem acesso a outro workspace.

## Roadmap dinâmico

Importe a planilha herdada uma vez. O Konstructor guarda snapshot, hash, abas e linhas em SQLite sob `~/.konstructor/workspaces/`; alterações de estado/evidência ficam como histórico separado, e os status herdados continuam preservados como referência não verificada. Depois da importação, use a base do Konstructor em vez de editar a planilha:

```bash
konstructor roadmap import /mnt/toshiba/git/kodoo context/kodoo-roadmap.xlsx
konstructor roadmap dashboard /mnt/toshiba/git/kodoo
konstructor roadmap list /mnt/toshiba/git/kodoo --sheet Roadmap --priority P0
konstructor roadmap show /mnt/toshiba/git/kodoo RM-003
konstructor roadmap update /mnt/toshiba/git/kodoo RM-003 --status 'Em andamento' --evidence 'crates/kodoo-core; cargo test -p kodoo-core'
konstructor roadmap export /mnt/toshiba/git/kodoo /tmp/kodoo-roadmap-backup.json
```

O roadmap inicial do próprio Konstructor está em `konstructor/roadmap_seed.json`. Para carregá-lo em uma base associada ao checkout Konstructor, rode `konstructor roadmap seed /mnt/toshiba/git/konstructor` (ou use `konstructor_roadmap_seed` pelo MCP). A importação JSON aceita tanto esse seed quanto backups exportados.

Reimportar uma revisão da planilha cria um novo snapshot e o torna ativo; snapshots e histórico anteriores são mantidos. A planilha de origem não é apagada automaticamente.

Para editar arquivos existentes, selecione explicitamente os caminhos que o modelo poderá alterar:

```bash
konstructor edit /mnt/toshiba/git/kodoo "adicione tratamento para estado vazio" \
  --file apps/kalkulisto/src/home.rs --file apps/kalkulisto/tests/navigation.rs
konstructor diff /mnt/toshiba/git/kodoo CHANGESET_ID
konstructor validate /mnt/toshiba/git/kodoo CHANGESET_ID
konstructor apply /mnt/toshiba/git/kodoo CHANGESET_ID --yes
```

O modelo só pode devolver conteúdo para os arquivos selecionados; alterações fora dessa lista são rejeitadas. A validação é feita numa cópia temporária. Se algum check estiver `failed` ou `blocked`, apply fica bloqueado.

Para workspaces grandes, `KONSTRUCTOR_TMPDIR` escolhe onde a cópia isolada será criada e `KONSTRUCTOR_VALIDATE_TIMEOUT` ajusta o timeout de `cargo fmt` em segundos (padrão: 300). Diretórios locais `.native`, `target` e `.git` são omitidos da cópia.

## Interface TUI

`tui` abre a interface Textual com árvore navegável do workspace e abas para detalhes, diff, logs e chat. Na aba Workspace, selecione um arquivo para editar, use o campo de caminho relativo para criar um arquivo e salve ou descarte explicitamente. Arquivos `.md` abrem na prévia Markdown com índice de títulos, atualizada enquanto você edita; a subaba Editor mostra a fonte. O editor bloqueia caminhos fora do workspace e recusa salvar sobre alterações externas detectadas. Para criar um app manualmente com Neovim ou pela TUI, siga a [receita de criação de apps](docs/APP_CREATION_RECIPE.md).

Selecione uma ação no menu e use o campo abaixo para informar o requisito ou ID; `apply` só prossegue depois de digitar `APPLY <ID>`. O chat usa o provedor OpenAI-compatible configurado e não executa ações nem aplica alterações. `Criar plano` transforma a última solicitação do chat em um plano local revisável.

Ao executar do checkout fonte, instale as dependências com `python3 -m pip install -e .`.

As mensagens do chat e o inventário carregado são enviados ao endpoint configurado. A árvore permite navegar pelos arquivos, e a aba `LOGS` recebe eventos do gerador em tempo real. Use `1`/`2`/`3`/`4` para abrir Workspace, Diff, Logs e Chat.

Temas disponíveis: `kodoo-dark`, `high-contrast` e `mono`. Também é possível passar um JSON customizado, ou criar `~/.konstructor/theme.json` para torná-lo o tema padrão. Exemplo:

```json
{
  "name": "minha-identidade",
  "header": "magenta",
  "accent": "cyan",
  "selected": "yellow",
  "text": "white",
  "muted": "white",
  "panel": "blue",
  "success": "green",
  "error": "red",
  "diff_add": "green",
  "diff_del": "red"
}
```

## Instalação global

O script instala o launcher `konstructor` e suas dependências Python, incluindo Textual. A instalação do Textual requer acesso ao índice de pacotes Python. Como root, o padrão é `/usr/local`; como usuário comum, o padrão é `~/.local`. O prefixo pode ser escolhido explicitamente:

```bash
./install.sh --prefix "$HOME/.local"
./install.sh --workspace /mnt/toshiba/git/kodoo
./install.sh --dry-run --prefix /usr/local
./install.sh --add-to-path
./install.sh --uninstall --prefix "$HOME/.local"
```

O instalador não altera arquivos do shell por padrão. `--add-to-path` adiciona o diretório `bin` do prefixo ao `~/.bashrc` ou `~/.zshrc`, conforme `$SHELL`, sem duplicar a entrada. Reabra o terminal ou carregue o arquivo indicado pelo instalador. Sem essa opção, use o caminho completo mostrado na saída ou adicione o diretório `bin` ao `PATH` manualmente.

O workspace Kodoo é detectado a partir do diretório atual ou do checkout `../kodoo`; em terminal interativo, você pode aceitar o caminho encontrado ou informar outro. `--workspace DIR` configura isso sem prompt; pode apontar para a raiz do Kodoo ou para um caminho dentro de um app, que será associado à raiz do workspace. O launcher instalado usa esse destino como padrão para `tui`, `status`, inspeção, wizard de módulo/app e roadmap. Um `KONSTRUCTOR_WORKSPACE` definido no ambiente tem precedência.

Depois da instalação, se `bin` já estiver no `PATH`:

```bash
konstructor tui /caminho/kodoo --theme high-contrast
```

## Acesso via Web Terminal

O comando `web` expõe a mesma camada de comandos da TUI por HTTP e SSE. O servidor fica desabilitado sem `--enable` e aceita somente bind loopback; acesso remoto não está disponível porque os endpoints de controle não têm autenticação:

```bash
konstructor web /caminho/kodoo --enable --host 127.0.0.1 --port 8765
```

Abra `http://127.0.0.1:8765/` em máquina confiável. O navegador recebe atualizações de workspace, diff e logs pelo endpoint `/events`; comandos usam `/action` e `/input`.

## Atalhos configuráveis

Crie `~/.konstructor/tui.json` ou passe `--config /caminho/tui.json` para `tui`/`web`:

```json
{
  "theme": "high-contrast",
  "refresh_ms": 200,
  "shortcuts": {
    "quit": "x",
    "next_view": "v",
    "logs_view": "l",
    "execute": "ENTER"
  }
}
```

Atalhos válidos: `up`, `down`, `execute`, `quit`, `next_view`, `workspace_view`, `diff_view`, `logs_view`, `split_view`, `page_up` e `page_down`. Teclas especiais usam `UP`, `DOWN`, `ENTER`, `TAB`, `PAGEUP` e `PAGEDOWN`.

Por padrão, o estado fica em `~/.konstructor`, nunca dentro do workspace-alvo. Use `KONSTRUCTOR_HOME` para testes ou isolamento.

## Arquitetura

`cli` apenas traduz comandos; `workflow` orquestra estados; `repository` contém descoberta e o adaptador Kodoo; `planner` produz planos rastreáveis; `staging` e `safety` protegem arquivos; `validator` executa apenas checks allowlisted; `provider` define a fronteira independente de IA.

O gerador atual é determinístico e local; ainda não chama provedor de IA nem atende à geração generalista de produto.
# konstructor
