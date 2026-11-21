# Como contribuir

Obrigado pelo interesse! Qualquer ajuda conta: corrigir um texto, melhorar o app, cobrir um caso que o modelo erra ou propor uma feature nova.

*English:* issues and pull requests in English are welcome too.

## Antes de começar

1. Procure uma issue aberta. As marcadas como **good first issue** são pequenas e boas para começar.
2. Comente na issue que vai pegá-la, para ninguém fazer o mesmo trabalho ao mesmo tempo.
3. Tem uma ideia nova? Abra uma issue antes do PR para conversarmos.

## Ambiente

```bash
git clone https://github.com/<seu-usuario>/dota-draft-preditor.git
cd dota-draft-preditor
pip install -r requirements.txt pytest
python -m pytest
streamlit run app.py
```

Os testes e o app rodam sem dados. Para dados reais, siga a seção de coleta do [README](README.md#coletar-dados-e-treinar).

## Pull requests

- Crie um branch a partir da `main` (ex.: `fix/sigmoid-overflow`).
- Um PR, uma mudança. Refatorações à parte vão em outro PR.
- Correção de bug vem com um teste que falha sem a correção.
- Rode `python -m pytest` antes de abrir o PR e diga na descrição o que testou.
- Mudou o modelo (feature nova, peso, janela)? Mostre a comparação antes/depois (log loss e acerto num conjunto de teste separado no tempo). Mudança no modelo sem medida não entra.

## Regras do modelo

- **Nada de vazamento:** uma partida só pode usar informação de antes dela.
- **Dados e pesos não vão para o git** (ver `.gitignore`).
- **Chaves de API ficam no `config.local.json`**, que também não vai para o git.

## Código

- Python 3.10+.
- Comentários explicam o porquê, não o quê.
- Mensagens de commit curtas e no imperativo, em português ou inglês.
