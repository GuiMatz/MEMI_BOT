# Contribuindo

Obrigado por considerar uma contribuição ao MeMi BOT. Issues e pull requests são bem-vindos.

## Antes de começar

- Procure issues existentes antes de abrir uma nova.
- Para mudanças grandes, abra uma issue primeiro para alinhar a proposta.
- Seja respeitoso e siga o [Código de Conduta](./CODE_OF_CONDUCT.md).

## Preparando o ambiente

1. Faça um fork do repositório e clone o seu fork.
2. Crie um ambiente virtual e instale as dependências:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. Use `config.example.json` e `token.txt.example` como modelos locais. Não use nem
   publique tokens, bancos, logs, backups ou dados reais de servidores.

## Alterações e testes

- Mantenha as mudanças focadas e compatíveis com Python 3.10 ou superior.
- Adicione ou atualize testes para mudanças de comportamento.
- Antes de abrir o pull request, execute:

  ```powershell
  python -m unittest -v test_memi_bot.py
  ```

- Não inclua arquivos gerados, dados de usuários ou configurações locais.

## Pull requests

Descreva o problema, a solução e como validou a alteração. Informe limitações conhecidas
e inclua capturas de tela somente quando forem úteis e não expuserem dados privados.
Um mantenedor revisará a proposta antes de ela ser integrada.
