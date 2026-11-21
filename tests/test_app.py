"""O app abre sem dados nem treino e mostra a previsao quando ha picks."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_opens_without_data_and_asks_for_picks():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    # Quem ja treinou localmente tem model_coefficients.csv; o aviso so vale sem ele.
    if not (Path(APP).parent / "model_coefficients.csv").exists():
        assert any("pesos de **exemplo**" in w.value for w in at.warning)
    assert any("Escolha os herois" in i.value for i in at.info)


def test_app_shows_both_probabilities_after_picking_heroes():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.selectbox(key="radiant_pick_0").set_value("Axe")
    at.selectbox(key="dire_pick_0").set_value("Pudge")
    at.run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert labels == ["Radiant", "Dire"]
    radiant = float(at.metric[0].value.rstrip("%"))
    dire = float(at.metric[1].value.rstrip("%"))
    assert radiant + dire == 100.0


FAKE_MATCH = {
    "radiant_name": "Team A",
    "dire_name": "Team B",
    "radiant_win": True,
    "picks_bans": [
        {"is_pick": True, "hero_id": 2, "team": 0},
        {"is_pick": True, "hero_id": 14, "team": 1},
        {"is_pick": False, "hero_id": 1, "team": 1},
    ],
    "players": [
        {"isRadiant": True, "hero_id": 2, "name": "jogador_a"},
        {"isRadiant": False, "hero_id": 14, "name": "jogador_b"},
    ],
}


def test_link_with_match_id_opens_that_match(monkeypatch):
    import utils

    calls = []

    def fake_get(path, *args, **kwargs):
        calls.append(path)
        return FAKE_MATCH

    monkeypatch.setattr(utils, "opendota_get_json", fake_get)
    at = AppTest.from_file(APP, default_timeout=60)
    at.query_params["match_id"] = "123"
    at.run()
    assert not at.exception
    assert calls == ["/matches/123"]
    assert at.selectbox(key="radiant_pick_0").value == "Axe"
    assert at.selectbox(key="dire_pick_0").value == "Pudge"
    assert at.text_input(key="radiant_team").value == "Team A"
    assert [m.label for m in at.metric] == ["Radiant", "Dire"]
