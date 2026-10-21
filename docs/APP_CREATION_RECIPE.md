# Receita manual para criar um app Kodoo

Use esta receita para construir o primeiro corte de um app com Neovim ou com o editor da TUI do Konstructor. O checkout Kodoo é a fonte das convenções: comece por um app existente semelhante e confirme os caminhos e comandos reais antes de copiar qualquer estrutura.

## 1. Descubra o padrão local

```bash
cd /mnt/toshiba/git/kodoo
find apps -maxdepth 2 -type f | sort | head -100
sed -n '1,220p' Cargo.toml
sed -n '1,220p' apps/<app-semelhante>/Cargo.toml
```

Anote como o workspace registra apps, como cada app configura Dioxus, onde ficam `main.rs`, estilos, assets e testes, e quais comandos de validação o repositório já usa. Reuse a forma de um app próximo em vez de criar uma estrutura nova por intuição.

## 2. Feche o escopo antes dos arquivos

Escreva um resumo curto com:

- nome e problema que o app resolve;
- usuário principal e tarefa central;
- plataforma inicial (web, desktop ou mobile) e o que fica fora do primeiro corte;
- telas e estados essenciais, incluindo vazio, carregamento e erro;
- dados e módulos Kodoo usados; registre explicitamente qualquer decisão sobre Direct;
- critérios de aceite que possam ser verificados.

## 3. Crie os arquivos

Escolha o editor:

**Neovim**

```bash
nvim /mnt/toshiba/git/kodoo/apps/<slug>/Cargo.toml
nvim /mnt/toshiba/git/kodoo/apps/<slug>/src/main.rs
```

Crie os demais caminhos seguindo o app de referência. Use o `Cargo.toml` da raiz para registrar o novo membro somente se essa for a convenção do workspace.

**TUI do Konstructor**

```bash
konstructor tui /mnt/toshiba/git/kodoo
```

Na aba Workspace, selecione arquivos para abrir e editar. Para criar um arquivo, informe um caminho relativo ao workspace, por exemplo `apps/<slug>/src/main.rs`, e escolha **Novo arquivo**. **Salvar no workspace** grava o arquivo diretamente; **Descartar** recarrega a versão salva. Se o arquivo mudou fora da TUI desde a abertura, o salvamento é recusado: reabra e reconcilie as versões. Para arquivos acima de 2 MB ou edição avançada, use Neovim.

## 4. Valide no próprio checkout

Use os checks documentados pelo Kodoo. Para um workspace Rust, o ponto de partida costuma ser:

```bash
cd /mnt/toshiba/git/kodoo
cargo fmt --check
cargo check -p <nome-do-pacote>
cargo test -p <nome-do-pacote>
```

Execute o app com o comando Dioxus já usado pelo repositório, na plataforma escolhida. Ajuste os comandos ao que o checkout realmente oferece; registre falhas e decisões ainda abertas.

## 5. Revise o resultado

```bash
git status --short
git diff --check
git diff -- Cargo.toml apps/<slug>
```

Confirme que o diff contém apenas o app e as entradas de workspace necessárias, que nenhum segredo ou arquivo local foi incluído, e que cada critério de aceite tem evidência. Depois registre no roadmap o estado e os comandos executados.
