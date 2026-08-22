"""
Puerto directo del proyecto de prueba
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional

_MONTO_PATTERN = re.compile(
    r"(?:S/?\.?|\$)\s*(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
)
_FECHA_TEXTO_PATTERN = re.compile(r"(\d{1,2})\s+([a-zA-Zé]{3,12})\.?\s+(\d{4})", re.IGNORECASE)
_FECHA_NUMERICA_PATTERN = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_FECHA_SIN_ANIO_PATTERN = re.compile(r"(\d{1,2})\s+([a-zA-Zé]{3,12})\.(?=[,\s]|$)", re.IGNORECASE)

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

UMBRAL_CONFIANZA_ALTA = 0.85

def extraer_montos(texto: str) -> List[float]:
    montos = []
    for match in _MONTO_PATTERN.finditer(texto):
        #raw = match.group(1)
        #raw = raw.replace(",", "") if raw.count(".") <= 1 else raw.replace(".", "").replace(",", ".")
        try:
            montos.append(_normalizar_monto(match.group(1)))
        except ValueError:
            continue
    return montos

def extraer_monedas(texto: str) -> List[str]:
    monedas = []
    if re.search(r"S/\.?", texto):
        monedas.append("PEN")
    if re.search(r"\$", texto):
        monedas.append("USD")
    return monedas


def extraer_fechas(texto: str) -> List[str]:
    fechas = []
    for dia, mes_txt, anio in _FECHA_TEXTO_PATTERN.findall(texto):
        mes_num = _MESES.get(mes_txt.lower()[:3])
        if mes_num:
            try:
                fechas.append(date(int(anio), mes_num, int(dia)).isoformat())
            except ValueError:
                continue
    for d1, d2, anio in _FECHA_NUMERICA_PATTERN.findall(texto):
        try:
            fechas.append(date(int(anio), int(d2), int(d1)).isoformat())
        except ValueError:
            continue

    if not fechas:
        anio_actual = date.today().year
        for dia, mes_txt in _FECHA_SIN_ANIO_PATTERN.findall(texto):
            mes_num = _MESES.get(mes_txt.lower()[:3])
            if mes_num:
                try:
                    fechas.append(date(anio_actual, mes_num, int(dia)).isoformat())
                except ValueError:
                    continue

    return fechas


def fecha_es_plausible(fecha_str: Optional[str], referencia: Optional[date] = None, dias_gracia_fin_de_anio: int = 10) -> bool:
    if not fecha_str:
        return False
    referencia = referencia or date.today()
    try:
        fecha = date.fromisoformat(fecha_str)
    except ValueError:
        return False
    if fecha.year == referencia.year:
        return True
    en_gracia_de_enero = referencia.month == 1 and referencia.day <= dias_gracia_fin_de_anio
    es_diciembre_del_anio_anterior = fecha.year == referencia.year - 1 and fecha.month == 12
    return en_gracia_de_enero and es_diciembre_del_anio_anterior

def resolver_campo(valor_actual: Any, candidatos: List[Any], comparador, confianza_ia: Optional[float] = None, umbral: float = UMBRAL_CONFIANZA_ALTA) -> Dict[str, Any]:
    candidatos_unicos = list(dict.fromkeys(candidatos))

    if valor_actual is not None and any(comparador(valor_actual, c) for c in candidatos_unicos):
        return {"accion": "ninguna", "valor_final": valor_actual, "candidatos": candidatos_unicos, "motivo": ""}

    if len(candidatos_unicos) == 1:
        return {
            "accion": "auto_corregido",
            "valor_final": candidatos_unicos[0],
            "candidatos": candidatos_unicos,
            "motivo": f"IA dijo {valor_actual!r}, OCR detectó únicamente {candidatos_unicos[0]!r} -> se usó el de OCR.",
        }

    if len(candidatos_unicos) == 0:
        if confianza_ia is not None and confianza_ia >= umbral:
            return {
                "accion": "ninguna_confianza_alta",
                "valor_final": valor_actual,
                "candidatos": candidatos_unicos,
                "motivo": f"OCR no detectó nada, pero la IA reportó confianza alta ({confianza_ia:.2f}).",
            }
        return {
            "accion": "revision_manual",
            "valor_final": valor_actual,
            "candidatos": candidatos_unicos,
            "motivo": f"OCR no detectó nada y la confianza de la IA es {('desconocida' if confianza_ia is None else f'baja ({confianza_ia:.2f})')}.",
        }

    return {
        "accion": "revision_manual",
        "valor_final": valor_actual,
        "candidatos": candidatos_unicos,
        "motivo": f"IA dijo {valor_actual!r}, OCR dio candidatos ambiguos: {candidatos_unicos}.",
    }


def puede_autoclasificar_antiguo(resultado_fecha: Dict[str, Any], confianza_fecha: Optional[float], umbral: float = UMBRAL_CONFIANZA_ALTA) -> tuple[bool, str]:
    if resultado_fecha["accion"] == "ninguna":
        return True, "coincidencia_exacta"
    if resultado_fecha["accion"] == "auto_corregido" and confianza_fecha is not None and confianza_fecha >= umbral:
        return True, "auto_corregido_confianza_alta"
    return False, "no_califica"

def _normalizar_monto(raw: str) -> float:
    raw = raw.replace(" ", "").strip()

    ultima_coma = raw.rfind(",")
    ultimo_punto = raw.rfind(".")

    if ultima_coma >= 0 and ultimo_punto >= 0:
        separador_decimal = "," if ultima_coma > ultimo_punto else "."
    elif ultima_coma >= 0:
        decimales = len(raw) - ultima_coma - 1
        separador_decimal = "," if decimales in (1, 2) else None
    elif ultimo_punto >= 0:
        decimales = len(raw) - ultimo_punto - 1
        separador_decimal = "." if decimales in (1, 2) else None
    else:
        separador_decimal = None

    if separador_decimal:
        miles = "." if separador_decimal == "," else ","
        normalizado = raw.replace(miles, "")
        entero, decimal = normalizado.rsplit(separador_decimal, 1)
        entero = entero.replace(separador_decimal, "")
        normalizado = f"{entero}.{decimal}"
    else:
        normalizado = raw.replace(",", "").replace(".", "")

    return round(float(normalizado), 2) 