"""Equal-weight reciprocal rank fusion; equal scores receive equal ranks."""

def reciprocal_rank_fusion(lexical, semantic, k=60):
    if len(lexical) != len(semantic):
        raise ValueError("Metric lengths differ")
    result = [0.0] * len(lexical)
    for values in (lexical, semantic):
        ordered = sorted((i for i, value in enumerate(values) if value is not None),
                         key=lambda i: -values[i])
        previous, rank = None, 0
        for position, index in enumerate(ordered, 1):
            if values[index] != previous:
                rank = position
            result[index] += 1 / (k + rank)
            previous = values[index]
    return result
