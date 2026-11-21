"""O app abre sem dados nem treino e mostra a previsao quando ha picks."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_opens_without_data_and_asks_for_picks():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
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
