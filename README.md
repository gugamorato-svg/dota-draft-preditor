# Dota Draft Preditor

[![tests](https://github.com/gugamorato-svg/dota-draft-preditor/actions/workflows/tests.yml/badge.svg)](https://github.com/gugamorato-svg/dota-draft-preditor/actions/workflows/tests.yml)

Prevê o vencedor de partidas **profissionais de Dota 2 a partir do draft**: monte os picks de Radiant e Dire (ou carregue uma partida pelo ID do OpenDota) e veja a chance de vitória de cada lado, com o peso de cada fator na previsão.

> *English summary:* predicts the winner of pro Dota 2 matches from the draft. Pick heroes for Radiant and Dire (or load a match by its OpenDota ID) and get each side's win probability, with a breakdown of what drove it. Python + Streamlit, data from OpenDota and STRATZ. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## O que entra na previsão

O modelo é uma regressão logística sobre diferenças Radiant − Dire:

| Feature | O que mede |
|---|---|
| `hero_meta_diff` | força dos heróis no patch (winrate pro e de alto MMR, pick/ban) |
| `player_comfort_diff` | experiência de cada jogador no herói escolhido |
| `role_fit_diff` | encaixe entre a função do jogador e o herói |
| `draft_balance_diff` | equilíbrio do draft (iniciação, controle, escala...) |
| `lane_matchup_diff` | confrontos de lane |
| `synergy_diff` / `counter_diff` | duplas que funcionam juntas e heróis que anulam outros |
| `rating_diff`, `recent_form_diff`, `player_form_diff`, `head_to_head_diff` | força e momento dos times |
| `patch_meta_diff`, `layoff_diff` | meta do patch e dias sem jogar |

O app mostra a contribuição de cada uma na previsão.

## Como rodar

Requer Python 3.10 ou mais novo.

```bash
pip install -r requirements.txt
streamlit run app.py
```

O app já abre sem dados, com pesos de **exemplo** (não calibrados): serve para explorar a interface. Para previsões de verdade, colete os dados e treine o modelo.

### Coletar dados e treinar

As chaves de API são opcionais. Para usar limites maiores, copie `config.local.example.json` para `config.local.json` e preencha.

```bash
python fetch_pro_matches.py --all-leagues --limit 200   # partidas pro do OpenDota -> pro_matches.csv
python update_team_ratings.py
python update_hero_stats.py
python update_synergies.py
python update_team_form.py
python update_player_form.py
python update_head_to_head.py
python update_patch_meta.py
python train_model.py                                   # gera model_coefficients.csv
```

Depois disso, `streamlit run app.py` usa os seus dados e pesos. Os arquivos gerados não entram no git.

> Este repositório traz o código e o método, não pesos treinados nem dados. Cada pessoa gera os seus a partir das fontes públicas.

## Testes

```bash
python -m pytest
```

Rodam offline, sem dados.

## Estrutura

| Arquivo | O que faz |
|---|---|
| `app.py` | interface Streamlit |
| `features.py` | cálculo das features e da probabilidade |
| `temporal_context.py` | estatísticas que andam no tempo, sem usar o futuro |
| `train_model.py` | treino da regressão logística |
| `fetch_*.py` | coleta no OpenDota e no STRATZ |
| `update_*.py` | transformam as partidas coletadas nas tabelas que o modelo usa |
| `utils.py` | funções comuns (nomes de heróis e times, cliente do OpenDota) |

## Contribuindo

Contribuições são bem-vindas. Veja o [CONTRIBUTING.md](CONTRIBUTING.md) e as issues marcadas como **good first issue**.

## Créditos

- Partidas e estatísticas: [OpenDota](https://www.opendota.com/) e [STRATZ](https://stratz.com/).
- Funções de jogadores: [Liquipedia](https://liquipedia.net/dota2/).

Dota 2 is a registered trademark of Valve Corporation. This project is not affiliated with or endorsed by Valve.

## Licença

[MIT](LICENSE)
