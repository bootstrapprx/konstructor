# ADR-001 — Python no MVP inicial

## Status
Aceita para o spike/MVP.

## Contexto
A especificação deixou Rust ou Python em aberto. A integração precisa ler `Cargo.toml`, analisar fontes e executar com segurança os checks disponíveis. O ambiente de implementação não possui Rust/Cargo, enquanto Python 3.12 e `tomllib` estão disponíveis na biblioteca padrão.

## Decisão
Usar Python sem dependências externas no núcleo inicial. A interface de `LLMProvider` e o `KodooRepositoryAdapter` mantêm as fronteiras para trocar linguagem, parser ou provedor depois.

## Consequências
O MVP é simples de instalar e reproduzir, mas o parsing Rust é lexical e deliberadamente conservador; símbolos não reconhecidos são `unknown`, nunca inventados. Um parser AST dedicado pode ser adicionado quando o contrato e o ambiente do alvo justificarem.
