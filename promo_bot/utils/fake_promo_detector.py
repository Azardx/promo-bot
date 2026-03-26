PALAVRAS_SUSPEITAS = [
    "adesivo",
    "capinha",
    "manual",
    "pdf",
    "amostra"
]


def promo_falsa(titulo):

    titulo = titulo.lower()

    for palavra in PALAVRAS_SUSPEITAS:

        if palavra in titulo:
            return True

    return False