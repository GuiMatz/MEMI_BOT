# MeMi BOT

Bot para Discord com rankings de música, atividade, perfis, níveis, conquistas e
recuperação de histórico. A documentação completa de instalação e comandos está em
[LEIA-ME.md](./LEIA-ME.md).

## Requisitos

- Python 3.10 ou superior
- Um aplicativo de bot criado no [Discord Developer Portal](https://discord.com/developers/applications)
- Message Content Intent habilitado no portal

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item token.txt.example token.txt
Copy-Item config.example.json config.json
```

Edite `token.txt` com o token do bot. Em `config.json`, configure os IDs do dono e do
canal de avisos; os valores locais são ignorados pelo Git. O `memi.db` é criado pelo
bot e guarda dados do servidor. Nunca compartilhe esses arquivos.

```powershell
python memi_bot.py
```

## Testes

```powershell
python -m unittest -v test_memi_bot.py
```

Os testes usam dados temporários e não conectam ao Discord. A integração contínua executa
essa suíte em cada pull request.

## Contribuições

Leia [CONTRIBUTING.md](./CONTRIBUTING.md) antes de abrir uma issue ou pull request.
Este projeto segue o [Código de Conduta](./CODE_OF_CONDUCT.md) e está sob a licença
[MIT](./LICENSE).
