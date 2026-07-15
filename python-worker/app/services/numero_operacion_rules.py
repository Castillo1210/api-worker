import re

def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")

def _last_n_digits(n: int):
    def rule (numero_operacion: str) -> str:
        digits = _digits_only(numero_operacion)
        if len(digits) > n:
            return digits[-n:]
        return numero_operacion
    return rule

_LETTER_PREFIX_RE = re.compile(r"^[A-Za-z]+\d+$")

def _banbif_rule():
    def rule(numero_operacion: str) -> str:
        tokens = (numero_operacion or "").split()
        if not tokens:
            return numero_operacion
        
        for token in tokens:
            if _LETTER_PREFIX_RE.match(token):
                digits = _digits_only(token)
                if digits:
                    return digits
                
        digits = _digits_only(tokens[0])
        return digits if digits else numero_operacion
    return rule

# Reglas por banco_id. Agregar un banco nuevo es solo una línea acá
BANK_NUMERO_OPERACION_RULES = {
    "37d700b7-1dde-4fa5-801d-026e589296ba": _last_n_digits(10),
    "f7285cef-1e96-4a0f-b790-400199e0c68a": _banbif_rule()
}

def normalize_numero_operacion(banco_id: str, numero_operacion: str) -> str:
    if not numero_operacion:
        return numero_operacion
    rule = BANK_NUMERO_OPERACION_RULES.get(banco_id)
    return rule(numero_operacion) if rule else numero_operacion