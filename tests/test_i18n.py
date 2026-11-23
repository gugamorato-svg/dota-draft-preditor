"""O dicionario pt/en tem as mesmas chaves e `t()` cai no portugues."""

import i18n


def test_both_languages_have_the_same_keys():
    assert set(i18n.TEXTS["pt"]) == set(i18n.TEXTS["en"])


def test_t_translates_and_formats():
    assert i18n.t("label_pick", "pt", n=2) == "Pick 2"
    assert i18n.t("label_pick", "en", n=2) == "Pick 2"
    assert i18n.t("caption_real_result", "en", winner="Radiant") == "Actual result of the loaded match: Radiant won."


def test_t_falls_back_to_portuguese_for_unknown_language_or_key():
    assert i18n.t("title", "fr") == i18n.TEXTS["pt"]["title"]
    assert i18n.t("chave-que-nao-existe", "en") == "chave-que-nao-existe"
