from datetime import date

import pytest

from app.utils.ocr_cross_check import (
    extraer_fechas,
    extraer_montos,
    fecha_es_plausible,
    resolver_campo,
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Total S/ 1,234.56", [1234.56]),
        ("Total S/ 1.234,56", [1234.56]),
        ("Total S/ 100,50", [100.50]),
        ("Total S/ 1,234", [1234.00]),
    ],
)
def test_extraer_montos_soporta_formatos_comunes(texto, esperado):
    assert extraer_montos(texto) == esperado


def test_resolver_campo_conserva_llama_si_ocr_discrepa():
    resultado = resolver_campo(
        39.82,
        [10.00],
        lambda a, b: abs(float(a) - float(b)) < 0.01,
        confianza_ia=0.97,
    )

    assert resultado["accion"] == "revision_manual"
    assert resultado["valor_final"] == 39.82
    assert resultado["candidatos"] == [10.00]
    assert "Se conservó el valor de Llama" in resultado["motivo"]


def test_resolver_campo_usa_ocr_solo_si_llama_no_tiene_valor():
    resultado = resolver_campo(
        None,
        [10.00],
        lambda a, b: a == b,
        confianza_ia=None,
    )

    assert resultado["accion"] == "auto_corregido"
    assert resultado["valor_final"] == 10.00


def test_resolver_campo_marca_coincidencia():
    resultado = resolver_campo(
        39.82,
        [39.82],
        lambda a, b: abs(float(a) - float(b)) < 0.01,
        confianza_ia=0.95,
    )

    assert resultado["accion"] == "ninguna"
    assert resultado["valor_final"] == 39.82


def test_resolver_campo_sin_ocr_confia_en_llama_solo_si_hay_valor():
    con_valor = resolver_campo("PEN", [], lambda a, b: a == b, confianza_ia=0.90)
    sin_valor = resolver_campo(None, [], lambda a, b: a == b, confianza_ia=0.90)

    assert con_valor["accion"] == "ninguna_confianza_alta"
    assert sin_valor["accion"] == "revision_manual"


def test_fecha_date_de_llama_coincide_con_iso_de_ocr():
    fecha_llama = date(2026, 8, 21)
    candidatos = extraer_fechas("Fecha 21/08/2026")

    resultado = resolver_campo(
        fecha_llama,
        candidatos,
        lambda a, b: str(a)[:10] == str(b)[:10],
        confianza_ia=0.93,
    )

    assert resultado["accion"] == "ninguna"
    assert resultado["valor_final"] == fecha_llama


def test_fecha_es_plausible_acepta_date_y_texto_iso():
    referencia = date(2026, 8, 22)

    assert fecha_es_plausible(date(2026, 8, 21), referencia=referencia)
    assert fecha_es_plausible("2026-08-21", referencia=referencia)
    assert not fecha_es_plausible("2025-08-21", referencia=referencia)
