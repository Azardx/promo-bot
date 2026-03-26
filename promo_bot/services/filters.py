BLOCK_WORDS = [
    "capinha",
    "película",
    "adesivo",
    "suporte celular",
    "cabo usb"
]


def promo_valida(titulo):

    t = titulo.lower()

    if len(t) < 15:
        return False

    for word in BLOCK_WORDS:
        if word in t:
            return False

    return True