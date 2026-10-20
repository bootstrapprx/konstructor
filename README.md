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

`app wizard --apply` exige terminal interativo e confirmação digitando `APPLY`, e então delega ao scaffold oficial. `app review <workspace> <proposal-id>` reabre a proposta. `app new-app` registra requisitos de um app Dioxus, mas não cria arquivos: Kodoo ainda não tem template/comando oficial para esse caso.

## Provedor OpenAI-compatible

Configure `KONSTRUCTOR_LLM_BASE_URL`, `KONSTRUCTOR_LLM_MODEL` e, quando exigido, `KONSTRUCTOR_LLM_API_KEY` no ambiente. O padrão do endpoint é `https://api.openai.com/v1`; tokens nunca são gravados na configuração. `konstructor provider status` verifica a configuração e `konstructor app wizard --llm` usa saída JSON estruturada para enriquecer o resumo. O modo local continua disponível sem credenciais.

## Interface TUI

`tui` abre uma interface interativa baseada em `curses`. Use as setas para escolher `Inspect`, `Index`, `Plan`, `Generate`, `Validate`, `Diff`, `Apply` ou `Status`, e pressione Enter para executar. O diff é somente leitura e a aplicação exige digitar `sim` na confirmação da própria tela. `q` sai sem alterar o workspace.

Há quatro abas: `WORKSPACE`, `DIFF`, `LOGS` e `SPLIT`. A aba `SPLIT` é a visualização padrão e mostra o inventário do workspace, o diff/detalhe e os logs simultaneamente. A aba `LOGS` recebe eventos do próprio gerador e do módulo `logging` padrão em tempo real, com atualização automática a cada 200 ms. Use `Tab` ou `1`/`2`/`3`/`4` para trocar de painel.

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

O script instala a TUI e o launcher `konstructor` sem dependências externas. Como root, o padrão é `/usr/local`; como usuário comum, o padrão é `~/.local`. O prefixo pode ser escolhido explicitamente:

```bash
./install.sh --prefix "$HOME/.local"
./install.sh --dry-run --prefix /usr/local
./install.sh --uninstall --prefix "$HOME/.local"
```

Depois da instalação:

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
