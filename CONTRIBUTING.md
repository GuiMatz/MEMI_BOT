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
- O código segue o formato do `black` (configuração em `pyproject.toml`, linhas de 100
  colunas). A integração contínua usa a versão fixada abaixo:

  ```powershell
  python -m pip install black==26.5.1
  python -m black memi_bot.py memi_tray.py estilo.py imagens.py changelog.py test_memi_bot.py test_estilo.py test_imagens.py test_changelog.py
  ```

- Antes de abrir o pull request, execute:

  ```powershell
  python -m unittest discover -v -p "test_*.py"
  ```

- Não inclua arquivos gerados, dados de usuários ou configurações locais.

## Pull requests

Descreva o problema, a solução e como validou a alteração. Informe limitações conhecidas
e inclua capturas de tela somente quando forem úteis e não expuserem dados privados.
Um mantenedor revisará a proposta antes de ela ser integrada.
