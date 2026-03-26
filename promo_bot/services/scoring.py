def score_promo(titulo):

    score = 0

    titulo = titulo.lower()

    if "ssd" in titulo:
        score += 5

    if "rtx" in titulo or "rx" in titulo:
        score += 7

    if "notebook" in titulo:
        score += 6

    if "iphone" in titulo:
        score += 8

    return score